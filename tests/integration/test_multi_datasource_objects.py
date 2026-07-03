"""End-to-end scenarios for column-wise multi-datasource object types (MDO).

One object type backed by N datasets: every non-PK dataset property maps to
exactly one datasource, the PK column exists in every datasource, and the
object universe is the UNION of PKs across datasources (a PK missing from a
segment leaves that segment's properties null). A datasource ``requiredRole``
masks the segment's property VALUES (never the schema) for callers lacking
the role, and gates edits through those properties.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import PermissionDenied, ValidationFailed

from tests.conftest import prepare_indexed_demo

_SHIPMENT_ONTOLOGY = """
objectTypes:
  - apiName: Shipment
    primaryKey: shipmentId
    backing:
      datasources:
        - {name: core, dataset: ship.core, primaryKeyColumns: [shipment_id]}
        - {name: costs, dataset: ship.costs, primaryKeyColumns: [shipment_id], requiredRole: finance}
    properties:
      - {apiName: shipmentId, column: shipment_id, type: string, nullable: false, indexed: true}
      - {apiName: carrier, column: carrier, type: string, indexed: true}
      - apiName: cost
        column: cost
        type: float
        indexed: true
        editable: true
        editPolicy: edit_wins
        datasource: costs
actionTypes:
  - apiName: SetShipmentCost
    target: Shipment
    parameters:
      - {apiName: newCost, type: float, required: true}
    permissions:
      allowedRoles: [ops_manager, finance]
    mutations:
      - {type: setProperty, property: cost, valueFrom: params.newCost}
"""

_CORE_CSV = "shipment_id,carrier,cost\nS-1,UPS,0.0\nS-2,DHL,0.0\n"
_COSTS_CSV = "shipment_id,cost\nS-1,10.5\nS-3,7.25\n"


def _prepare_shipments(foundry: FoundryLite, tmp_path: Path) -> RequestContext:
    ctx = demo_admin_context()
    core_csv = tmp_path / "ship_core.csv"
    core_csv.write_text(_CORE_CSV, encoding="utf-8")
    costs_csv = tmp_path / "ship_costs.csv"
    costs_csv.write_text(_COSTS_CSV, encoding="utf-8")
    foundry.datasets.ensure("ship.core", ctx=ctx, primary_key=["shipment_id"])
    foundry.datasets.ensure("ship.costs", ctx=ctx, primary_key=["shipment_id"])
    foundry.datasets.upload_csv("ship.core", str(core_csv), ctx=ctx)
    foundry.datasets.upload_csv("ship.costs", str(costs_csv), ctx=ctx)
    ontology_path = tmp_path / "shipments.yaml"
    ontology_path.write_text(_SHIPMENT_ONTOLOGY, encoding="utf-8")
    foundry.ontology.apply(str(ontology_path), ctx=ctx)
    foundry.objects.reindex("Shipment", ctx=ctx)
    return ctx


def _index_run_source_ref(foundry: FoundryLite, ctx: RequestContext, run_id: str) -> dict[str, object]:
    run = next(run for run in foundry.operations.list_runs(ctx=ctx)["indexRuns"] if run["id"] == run_id)
    return dict(run["source_ref"])


def test_multi_datasource_index_merges_union_of_primary_keys(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = _prepare_shipments(foundry, tmp_path)

    merged = foundry.objects.get("Shipment", "S-1", ctx=ctx)
    core_only = foundry.objects.get("Shipment", "S-2", ctx=ctx)
    costs_only = foundry.objects.get("Shipment", "S-3", ctx=ctx)
    page = foundry.objects.query("Shipment", ctx=ctx, limit=10)

    # Column-wise merge: each property resolves from ITS datasource — the
    # core dataset's decoy cost column never leaks into the cost property.
    assert merged["properties"] == {"shipmentId": "S-1", "carrier": "UPS", "cost": 10.5}
    # PK missing from the costs segment -> costs-mapped properties are null.
    assert core_only["properties"]["cost"] is None
    assert core_only["properties"]["carrier"] == "DHL"
    # PK present only in the costs segment -> the object still exists (union).
    assert costs_only["properties"]["carrier"] is None
    assert costs_only["properties"]["cost"] == 7.25
    assert [item["objectId"] for item in page["items"]] == ["S-1", "S-2", "S-3"]
    # The record's source id is the deterministic composite of both segments.
    assert str(merged["sourceDatasetVersionId"]).count("+") == 1


def test_multi_datasource_run_records_composite_and_per_segment_versions(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = _prepare_shipments(foundry, tmp_path)

    rebuild = foundry.objects.reindex("Shipment", ctx=ctx)
    source_ref = _index_run_source_ref(foundry, ctx, rebuild["index_run_id"])
    shadow = foundry.objects.shadow_reindex("Shipment", ctx=ctx)

    segment_versions = source_ref["segmentDatasetVersionIds"]
    assert isinstance(segment_versions, dict)
    assert list(segment_versions) == ["core", "costs"]
    assert source_ref["dataset_version_id"] == "+".join(str(segment_versions[name]) for name in ("core", "costs"))
    # Shadow rebuilds reuse the same merged reader and switch cleanly.
    assert shadow["is_switched"] is True
    assert shadow["rows_read"] == 3


def test_segment_masking_nulls_values_but_keeps_schema_visible(foundry: FoundryLite, tmp_path: Path) -> None:
    admin = _prepare_shipments(foundry, tmp_path)
    viewer = RequestContext(actor_user_id="viewer-1", roles=("viewer",))
    ops_manager = RequestContext(actor_user_id="ops-1", roles=("ops_manager",))
    finance = RequestContext(actor_user_id="fin-1", roles=("finance",))

    viewer_get = foundry.objects.get("Shipment", "S-1", ctx=viewer)
    ops_query = foundry.objects.query("Shipment", ctx=ops_manager, limit=10)
    finance_get = foundry.objects.get("Shipment", "S-1", ctx=finance)
    admin_get = foundry.objects.get("Shipment", "S-1", ctx=admin)
    catalog = foundry.ontology.catalog(ctx=viewer)

    # Value masking, not schema hiding: the caller sees the property as null.
    assert viewer_get["properties"]["cost"] is None
    assert viewer_get["properties"]["carrier"] == "UPS"
    assert all(item["properties"]["cost"] is None for item in ops_query["items"])
    # Holding the segment role (or admin, implicitly) reveals the values.
    assert finance_get["properties"]["cost"] == 10.5
    assert admin_get["properties"]["cost"] == 10.5
    shipment = next(item for item in catalog["objectTypes"] if item["apiName"] == "Shipment")
    cost_property = next(prop for prop in shipment["properties"] if prop["apiName"] == "cost")
    assert cost_property["datasource"] == "costs"
    assert [entry["name"] for entry in shipment["backing"]["datasources"]] == ["core", "costs"]


def test_segment_masked_property_rejected_in_filter_order_and_aggregate(foundry: FoundryLite, tmp_path: Path) -> None:
    _prepare_shipments(foundry, tmp_path)
    viewer = RequestContext(actor_user_id="viewer-1", roles=("viewer",))
    ops_manager = RequestContext(actor_user_id="ops-1", roles=("ops_manager",))

    with pytest.raises(ValidationFailed, match="masked property"):
        foundry.objects.query("Shipment", ctx=viewer, filter_ast={"property": "cost", "op": "gte", "value": 1.0})
    with pytest.raises(ValidationFailed, match="masked property"):
        foundry.objects.query("Shipment", ctx=viewer, order_by=[{"property": "cost", "direction": "asc"}])
    with pytest.raises(PermissionDenied, match="masked property"):
        foundry.objects.aggregate(
            "Shipment",
            ctx=ops_manager,
            select=[{"function": "sum", "property": "cost"}],
        )


def test_action_editing_segment_property_requires_segment_view(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = _prepare_shipments(foundry, tmp_path)
    ops_manager = RequestContext(actor_user_id="ops-1", roles=("ops_manager",))
    finance = RequestContext(actor_user_id="fin-1", roles=("finance", "ops_manager"))
    target = foundry.objects.get("Shipment", "S-1", ctx=ctx)

    with pytest.raises(PermissionDenied, match="datasource segment"):
        foundry.actions.apply(
            "SetShipmentCost",
            object_type="Shipment",
            object_id="S-1",
            expected_object_version=target["objectVersion"],
            params={"newCost": 42.0},
            idempotency_key="segment-edit-denied",
            ctx=ops_manager,
        )
    with pytest.raises(PermissionDenied, match="datasource segment"):
        foundry.actions.apply_batch(
            "SetShipmentCost",
            object_type="Shipment",
            targets=[{"object_id": "S-1", "expected_object_version": target["objectVersion"]}],
            params={"newCost": 42.0},
            idempotency_key="segment-batch-denied",
            ctx=ops_manager,
        )

    applied = foundry.actions.apply(
        "SetShipmentCost",
        object_type="Shipment",
        object_id="S-1",
        expected_object_version=target["objectVersion"],
        params={"newCost": 42.0},
        idempotency_key="segment-edit-allowed",
        ctx=finance,
    )
    updated = foundry.objects.get("Shipment", "S-1", ctx=finance)

    assert applied["status"] == "succeeded"
    assert updated["properties"]["cost"] == 42.0


def test_changelog_incremental_touches_only_pks_changed_in_one_segment(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = _prepare_shipments(foundry, tmp_path)
    seeded = foundry.objects.reindex("Shipment", ctx=ctx)
    seeded_ref = _index_run_source_ref(foundry, ctx, seeded["index_run_id"])
    unchanged_before = foundry.objects.get("Shipment", "S-2", ctx=ctx)
    updated_costs = tmp_path / "ship_costs_v2.csv"
    updated_costs.write_text("shipment_id,cost\nS-1,11.0\nS-3,7.25\n", encoding="utf-8")
    foundry.datasets.upload_csv("ship.costs", str(updated_costs), ctx=ctx)

    result = foundry.objects.reindex("Shipment", ctx=ctx)
    source_ref = _index_run_source_ref(foundry, ctx, result["index_run_id"])
    changed = foundry.objects.get("Shipment", "S-1", ctx=ctx)
    unchanged = foundry.objects.get("Shipment", "S-2", ctx=ctx)

    # Only the finance dataset committed a new version: the composite id moves,
    # the merged hash changes only for the affected PK, everything else skips.
    assert seeded_ref["mode"] == "changelog_incremental"
    assert source_ref["mode"] == "changelog_incremental"
    assert source_ref["dataset_version_id"] != seeded_ref["dataset_version_id"]
    assert source_ref["changed"] == 1
    assert source_ref["skipped"] == 2
    assert changed["properties"]["cost"] == 11.0
    assert unchanged["objectVersion"] == unchanged_before["objectVersion"]


def test_multi_datasource_migration_guards_and_rollback_round_trip(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = _prepare_shipments(foundry, tmp_path)

    added = foundry.ontology.validate(
        _SHIPMENT_ONTOLOGY.replace(
            "        - {name: costs, dataset: ship.costs, primaryKeyColumns: [shipment_id], requiredRole: finance}",
            "        - {name: costs, dataset: ship.costs, primaryKeyColumns: [shipment_id], requiredRole: finance}\n"
            "        - {name: audit, dataset: ship.costs, primaryKeyColumns: [shipment_id]}",
        ),
        ctx=ctx,
    )
    moved = foundry.ontology.validate(
        _SHIPMENT_ONTOLOGY.replace("datasource: costs", "datasource: core"),
        ctx=ctx,
    )
    removed = foundry.ontology.validate(
        _SHIPMENT_ONTOLOGY.replace(
            "        - {name: costs, dataset: ship.costs, primaryKeyColumns: [shipment_id], requiredRole: finance}\n",
            "",
        )
        .replace(
            "        column: cost\n",
            "        source: edit_layer\n",
        )
        .replace("        datasource: costs\n", ""),
        ctx=ctx,
    )

    added_kinds = {change["kind"]: change["status"] for change in added["migration_plan"]["changes"]}
    moved_kinds = {change["kind"]: change["status"] for change in moved["migration_plan"]["changes"]}
    removed_kinds = {change["kind"]: change["status"] for change in removed["migration_plan"]["changes"]}
    assert added["migration_plan"]["status"] == "compatible_with_warning"
    assert added_kinds["object_datasource_added"] == "warning"
    assert moved["migration_plan"]["status"] == "blocked"
    assert moved_kinds["property_datasource_moved"] == "blocked"
    assert removed["migration_plan"]["status"] == "blocked"
    assert removed_kinds["object_datasource_removed"] == "blocked"
    with pytest.raises(ValidationFailed, match="ontology migration requires explicit migration plan"):
        foundry.ontology.apply_text(
            _SHIPMENT_ONTOLOGY.replace("datasource: costs", "datasource: core"),
            ctx=ctx,
        )

    # Rollback round-trip: a requiredRole re-scope is a warning both ways, so
    # the original version replays cleanly with its datasources intact.
    v2 = foundry.ontology.apply_text(_SHIPMENT_ONTOLOGY.replace(", requiredRole: finance", ""), ctx=ctx)
    rolled_back = foundry.ontology.rollback(v2["version_number"] - 1, ctx=ctx)
    catalog = foundry.ontology.catalog(ctx=ctx)
    shipment = next(item for item in catalog["objectTypes"] if item["apiName"] == "Shipment")
    cost_property = next(prop for prop in shipment["properties"] if prop["apiName"] == "cost")
    reindexed = foundry.objects.reindex("Shipment", ctx=ctx)
    viewer_get = foundry.objects.get("Shipment", "S-1", ctx=RequestContext(actor_user_id="v-1", roles=("viewer",)))

    assert rolled_back["rolled_back_to_version_number"] == v2["version_number"] - 1
    assert [entry["name"] for entry in shipment["backing"]["datasources"]] == ["core", "costs"]
    assert shipment["backing"]["datasources"][1]["requiredRole"] == "finance"
    assert cost_property["datasource"] == "costs"
    assert reindexed["rows_read"] == 3
    assert viewer_get["properties"]["cost"] is None


def test_demo_margin_composes_segment_and_classification_masking(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)
    viewer = RequestContext(actor_user_id="viewer-1", roles=("viewer",))
    finance = RequestContext(actor_user_id="fin-1", roles=("finance",))

    viewer_order = foundry.objects.get("Order", "O-1001", ctx=viewer)
    finance_order = foundry.objects.get("Order", "O-1001", ctx=finance)
    admin_order = foundry.objects.get("Order", "O-1001", ctx=ctx)

    # margin is BOTH classified (finance) and mapped to the finance datasource:
    # the classification sentinel stays observable for callers lacking finance,
    # while holders of the segment role read the real value.
    assert viewer_order["properties"]["margin"] == "***MASKED***"
    assert finance_order["properties"]["margin"] == 230.0
    assert admin_order["properties"]["margin"] == 230.0
