from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import TabularRow
from foundry_lite.application.services.object_store.indexing import ObjectIndexingService
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.compute import DuckDBComputeAdapter
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from sqlalchemy import select


class EmptyReindexReadComputeAdapter(DuckDBComputeAdapter):
    def __init__(self) -> None:
        self.force_empty_reads = False

    def rows_from_parquet(self, parquet_path: Path) -> list[TabularRow]:
        if self.force_empty_reads:
            return []
        return super().rows_from_parquet(parquet_path)


def test_cdc_object_indexing_updates_tombstones_and_skips_stale_events(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    _seed_order_snapshot(foundry, tmp_path, ctx)
    foundry.ontology.apply(str(_order_ontology(tmp_path)), ctx=ctx)
    foundry.objects.reindex("Order", ctx=ctx)

    update = _cdc_event("topic:0:12", "u", 12, after={"order_id": "O-1001", "status": "APPROVED", "amount": 725})
    update_result = foundry.objects.index_cdc_events("Order", [update], ctx=ctx)
    updated = foundry.objects.get("Order", "O-1001", ctx=ctx)
    updated_version = updated["objectVersion"]
    object_changed_count = _object_changed_count(foundry, ctx, "O-1001")

    replay_result = foundry.objects.index_cdc_events("Order", [update], ctx=ctx)
    stale = _cdc_event("topic:0:11", "u", 11, after={"order_id": "O-1001", "status": "REVIEW", "amount": 700})
    stale_result = foundry.objects.index_cdc_events("Order", [stale], ctx=ctx)
    after_stale = foundry.objects.get("Order", "O-1001", ctx=ctx)
    stale_snapshot = _cdc_event(
        "topic:0:10",
        "r",
        10,
        after={"order_id": "O-1001", "status": "SNAPSHOT_PENDING", "amount": 650},
    )
    stale_snapshot_result = foundry.objects.index_cdc_events("Order", [stale_snapshot], ctx=ctx)
    after_stale_snapshot = foundry.objects.get("Order", "O-1001", ctx=ctx)
    delete = _cdc_event(
        "topic:0:13",
        "d",
        13,
        before={"order_id": "O-1001", "status": "APPROVED", "amount": 725},
    )
    delete_result = foundry.objects.index_cdc_events("Order", [delete], ctx=ctx)
    deleted = foundry.objects.get("Order", "O-1001", ctx=ctx)
    late_update_after_delete = _cdc_event(
        "topic:0:11-retry",
        "u",
        11,
        after={"order_id": "O-1001", "status": "REOPENED", "amount": 999},
    )
    late_update_result = foundry.objects.index_cdc_events("Order", [late_update_after_delete], ctx=ctx)
    still_deleted = foundry.objects.get("Order", "O-1001", ctx=ctx)
    active_page = foundry.objects.query("Order", ctx=ctx)
    foundry.datasets.ensure("ops.order_current", ctx=ctx, primary_key=["orderId"])
    order_current = foundry.materialization.run("order_current", ctx=ctx)
    order_current_rows = _materialization_rows_for_version(foundry, ctx, order_current.version_id)

    assert update_result["objects_upserted"] == 1
    assert updated["properties"]["status"] == "APPROVED"
    assert replay_result["events_skipped"] == 1
    assert stale_result["events_skipped"] == 1
    assert after_stale["objectVersion"] == updated_version
    assert after_stale["properties"]["status"] == "APPROVED"
    assert stale_snapshot_result["events_skipped"] == 1
    assert after_stale_snapshot["objectVersion"] == updated_version
    assert after_stale_snapshot["properties"]["status"] == "APPROVED"
    assert _object_changed_count(foundry, ctx, "O-1001") == object_changed_count + 1
    assert delete_result["objects_deleted"] == 1
    assert deleted.get("deleted") is True
    assert deleted.get("deletionReason") == "source_deleted"
    assert late_update_result["events_skipped"] == 1
    assert still_deleted.get("deleted") is True
    assert still_deleted.get("deletionReason") == "source_deleted"
    assert active_page["items"] == []
    assert order_current_rows == []
    assert _index_run(foundry, ctx, delete_result["index_run_id"])["trigger_type"] == "cdc_incremental"


def test_cdc_object_indexing_inserts_new_object_and_new_tombstone(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    _seed_order_snapshot(foundry, tmp_path, ctx)
    foundry.ontology.apply(str(_order_ontology(tmp_path)), ctx=ctx)

    create = _cdc_event("topic:0:20", "c", 20, after={"order_id": "O-2002", "status": "NEW", "amount": 50})
    delete = _cdc_event("topic:0:21", "d", 21, before={"order_id": "O-3003", "status": "VOID", "amount": 0})

    create_result = foundry.objects.index_cdc_events("Order", [create], ctx=ctx)
    delete_result = foundry.objects.index_cdc_events("Order", [delete], ctx=ctx)
    created = foundry.objects.get("Order", "O-2002", ctx=ctx)
    deleted = foundry.objects.get("Order", "O-3003", ctx=ctx)

    assert create_result["objects_upserted"] == 1
    assert created["properties"]["status"] == "NEW"
    assert delete_result["objects_deleted"] == 1
    assert deleted.get("deleted") is True
    assert deleted.get("deletionReason") == "source_deleted"


def test_cdc_duplicate_event_idempotent(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    _seed_order_snapshot(foundry, tmp_path, ctx)
    foundry.ontology.apply(str(_order_ontology(tmp_path)), ctx=ctx)
    foundry.objects.reindex("Order", ctx=ctx)

    update = _cdc_event("topic:0:30", "u", 30, after={"order_id": "O-1001", "status": "APPROVED", "amount": 900})
    first = foundry.objects.index_cdc_events("Order", [update], ctx=ctx)
    after_first = foundry.objects.get("Order", "O-1001", ctx=ctx)
    duplicate = foundry.objects.index_cdc_events("Order", [update], ctx=ctx)
    after_duplicate = foundry.objects.get("Order", "O-1001", ctx=ctx)

    # Re-delivering the exact same CDC event (same topic/partition/offset id and
    # ordering) must be a no-op: no second upsert, no object_version bump, and no
    # property drift, so at-least-once delivery cannot double-apply a change.
    assert first["objects_upserted"] == 1
    assert duplicate["events_skipped"] == 1
    assert duplicate["objects_upserted"] == 0
    assert after_duplicate["objectVersion"] == after_first["objectVersion"]
    assert after_duplicate["properties"] == after_first["properties"]


def test_late_event_reopens_expected_materialization(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    _seed_order_snapshot(foundry, tmp_path, ctx)
    foundry.ontology.apply(str(_order_ontology(tmp_path)), ctx=ctx)
    foundry.objects.reindex("Order", ctx=ctx)
    foundry.datasets.ensure("ops.order_current", ctx=ctx, primary_key=["orderId"])
    first = foundry.materialization.run("order_current", ctx=ctx)
    late_update = _cdc_event(
        "topic:0:40",
        "u",
        40,
        after={"order_id": "O-1001", "status": "APPROVED_LATE", "amount": 760},
    )
    late_update["late_data_status"] = "LATE_REQUIRES_REPROCESS"
    late_update["event_time_lag_seconds"] = 7200

    index_result = foundry.objects.index_cdc_events("Order", [late_update], ctx=ctx)
    second = foundry.materialization.run("order_current", ctx=ctx)
    second_run = _materialization_run_for_version(foundry, ctx, second.version_id)
    detail = foundry.operations.run_detail("materialization", str(second_run["id"]), ctx=ctx)
    rows = _materialization_rows_for_version(foundry, ctx, second.version_id)
    materialization = detail["materialization"]
    reopen = materialization["reopen"]

    assert index_result["objects_upserted"] == 1
    assert rows[0]["status"] == "APPROVED_LATE"
    assert reopen["isReopened"] is True
    assert reopen["reason"] == "late_data_reprocess"
    assert reopen["previousDatasetVersionId"] == first.version_id
    assert reopen["sourceIndexRunId"] == index_result["index_run_id"]
    assert reopen["lateEventIds"] == ["topic:0:40"]
    assert materialization["watermark"]["lateDataReopen"] == reopen
    assert detail["datasetTransaction"]["committed_version_id"] == second.version_id


def test_late_data_badge_marks_impacted_insight(tmp_path: Path) -> None:
    foundry, ctx, index_result, detail = _late_order_current_context(tmp_path)

    object_detail = foundry.objects.get("Order", "O-1001", ctx=ctx, include_explain=True)
    object_badge = object_detail["explain"]["lateDataBadge"]
    materialization_badge = detail["materialization"]["lateDataBadge"]
    source_links = object_detail["explain"]["sourceRunChain"]

    assert object_badge["isImpacted"] is True
    assert object_badge["sourceEventId"] == "topic:0:40"
    assert object_badge["sourceRun"]["runId"] == index_result["index_run_id"]
    assert materialization_badge["isImpacted"] is True
    assert materialization_badge["apiName"] == "order_current"
    assert materialization_badge["sourceIndexRunId"] == index_result["index_run_id"]
    assert any(link["relation"] == "cdc_event_indexed_from" for link in source_links)


def test_late_data_downstream_impact_graph_lists_materializations(tmp_path: Path) -> None:
    _, _, index_result, detail = _late_order_current_context(tmp_path)

    impact = detail["downstreamImpact"]
    roots = {(row["resourceType"], row["resourceId"]) for row in impact["rootResources"]}
    affected_runs = {(row["runType"], row["runId"], row["relation"]) for row in impact["affectedRuns"]}
    badges = impact["lateDataBadges"]

    assert impact["status"] == "IMPACTED"
    assert ("index_run", index_result["index_run_id"]) in roots
    assert ("materialization", detail["runId"], "late_data_reprocessed") in affected_runs
    assert badges[0]["resourceType"] == "materialization"
    assert badges[0]["lateEventIds"] == ["topic:0:40"]


def test_late_delete_does_not_resurrect_stale_object(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    _seed_order_snapshot(foundry, tmp_path, ctx)
    foundry.ontology.apply(str(_order_ontology(tmp_path)), ctx=ctx)
    foundry.objects.reindex("Order", ctx=ctx)
    update = _cdc_event(
        "topic:0:51",
        "u",
        51,
        after={"order_id": "O-1001", "status": "APPROVED_CURRENT", "amount": 780},
    )
    foundry.objects.index_cdc_events("Order", [update], ctx=ctx)
    late_delete = _cdc_event(
        "topic:0:50-late-delete",
        "d",
        50,
        before={"order_id": "O-1001", "status": "PENDING", "amount": 700},
    )
    late_delete["late_data_status"] = "LATE_REQUIRES_REPROCESS"
    late_delete["event_time_lag_seconds"] = 5400

    result = foundry.objects.index_cdc_events("Order", [late_delete], ctx=ctx)
    current = foundry.objects.get("Order", "O-1001", ctx=ctx)
    active_ids = {item["objectId"] for item in foundry.objects.query("Order", ctx=ctx)["items"]}
    foundry.datasets.ensure("ops.order_current", ctx=ctx, primary_key=["orderId"])
    order_current = foundry.materialization.run("order_current", ctx=ctx)
    rows = _materialization_rows_for_version(foundry, ctx, order_current.version_id)

    assert result["events_skipped"] == 1
    assert result["objects_deleted"] == 0
    assert current.get("deleted") is not True
    assert current["properties"]["status"] == "APPROVED_CURRENT"
    assert active_ids == {"O-1001"}
    assert rows[0]["status"] == "APPROVED_CURRENT"


def test_cdc_source_transaction_group_not_partially_committed_without_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    _seed_order_snapshot(foundry, tmp_path, ctx)
    foundry.ontology.apply(str(_order_ontology(tmp_path)), ctx=ctx)
    foundry.objects.reindex("Order", ctx=ctx)

    # Two row changes delivered together as one source transaction group.
    group = [
        _cdc_event("topic:0:50", "c", 50, after={"order_id": "O-5001", "status": "NEW", "amount": 100}),
        _cdc_event("topic:0:51", "c", 51, after={"order_id": "O-5002", "status": "NEW", "amount": 200}),
    ]
    original_apply = ObjectIndexingService._apply_cdc_event

    def fail_on_second(self, conn, ctx, object_type, event):
        if event.object_id == "O-5002":
            raise RuntimeError("injected mid-group cdc failure")
        return original_apply(self, conn, ctx, object_type, event)

    monkeypatch.setattr(ObjectIndexingService, "_apply_cdc_event", fail_on_second)

    with pytest.raises(RuntimeError, match="injected mid-group cdc failure"):
        foundry.objects.index_cdc_events("Order", group, ctx=ctx)

    active_ids = {item["objectId"] for item in foundry.objects.query("Order", ctx=ctx)["items"]}
    failed_runs = [
        run
        for run in foundry.operations.query_runs(ctx=ctx, run_type="index")["indexRuns"]
        if run["status"] == "failed"
    ]

    # The whole CDC batch rolls back atomically: neither row of the source
    # transaction group is partially committed, and the failure surfaces as a
    # retryable FAILED index run rather than a silent partial apply.
    assert active_ids == {"O-1001"}
    assert len(failed_runs) >= 1
    assert failed_runs[-1]["error"]["message"] == "injected mid-group cdc failure"


def test_empty_shadow_reindex_persists_active_pointer_for_next_cdc_insert(tmp_path: Path) -> None:
    compute = EmptyReindexReadComputeAdapter()
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "flite")
    foundry = FoundryLite(dependencies=replace(dependencies, compute_adapter=compute))
    ctx = demo_admin_context()
    _seed_order_snapshot(foundry, tmp_path, ctx)
    foundry.ontology.apply(str(_order_ontology(tmp_path)), ctx=ctx)

    compute.force_empty_reads = True
    shadow = foundry.objects.shadow_reindex("Order", ctx=ctx)
    create = _cdc_event("topic:0:30", "c", 30, after={"order_id": "O-4004", "status": "NEW", "amount": 88})
    create_result = foundry.objects.index_cdc_events("Order", [create], ctx=ctx)
    created = foundry.objects.get("Order", "O-4004", ctx=ctx)
    stored_index_version = _object_index_version(foundry, ctx, "O-4004")

    assert shadow["is_switched"] is True
    assert shadow["validation"]["expectedCount"] == shadow["validation"]["actualCount"] == 0
    assert create_result["objects_upserted"] == 1
    assert created["properties"]["status"] == "NEW"
    assert stored_index_version == shadow["indexVersion"]


def _seed_order_snapshot(foundry: FoundryLite, tmp_path: Path, ctx: RequestContext) -> None:
    foundry.datasets.ensure("clean.orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("raw_cdc.erp_orders", ctx=ctx, primary_key=["event_id"])
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("order_id,status,amount\nO-1001,PENDING,700\n", encoding="utf-8")
    foundry.datasets.upload_csv("clean.orders", csv_path, ctx=ctx)


def _late_order_current_context(
    tmp_path: Path,
) -> tuple[FoundryLite, RequestContext, dict[str, object], dict[str, object]]:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    _seed_order_snapshot(foundry, tmp_path, ctx)
    foundry.ontology.apply(str(_order_ontology(tmp_path)), ctx=ctx)
    foundry.objects.reindex("Order", ctx=ctx)
    foundry.datasets.ensure("ops.order_current", ctx=ctx, primary_key=["orderId"])
    foundry.materialization.run("order_current", ctx=ctx)
    late_update = _late_update_event()
    index_result = foundry.objects.index_cdc_events("Order", [late_update], ctx=ctx)
    second = foundry.materialization.run("order_current", ctx=ctx)
    second_run = _materialization_run_for_version(foundry, ctx, second.version_id)
    detail = foundry.operations.run_detail("materialization", str(second_run["id"]), ctx=ctx)
    return foundry, ctx, dict(index_result), dict(detail)


def _late_update_event() -> dict[str, object]:
    late_update = _cdc_event(
        "topic:0:40",
        "u",
        40,
        after={"order_id": "O-1001", "status": "APPROVED_LATE", "amount": 760},
    )
    late_update["late_data_status"] = "LATE_REQUIRES_REPROCESS"
    late_update["event_time_lag_seconds"] = 7200
    return late_update


def _order_ontology(tmp_path: Path) -> Path:
    path = tmp_path / "order-cdc.yaml"
    path.write_text(
        """
objectTypes:
  - apiName: Order
    displayName: Order
    primaryKey: orderId
    backing:
      dataset: clean.orders
      mode: snapshot
      primaryKeyColumns: [order_id]
      cdc:
        dataset: raw_cdc.erp_orders
        primaryKeyColumns: [order_id]
        deletePolicy: tombstone
    properties:
      - apiName: orderId
        column: order_id
        type: string
        indexed: true
        nullable: false
      - apiName: status
        column: status
        type: string
        indexed: true
      - apiName: amount
        column: amount
        type: float
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return path


def _cdc_event(
    event_id: str,
    op: str,
    lsn: int,
    *,
    after: dict[str, object] | None = None,
    before: dict[str, object] | None = None,
) -> dict[str, object]:
    row = after or before or {}
    return {
        "event_id": event_id,
        "op": op,
        "pk": {"order_id": row["order_id"]},
        "before": before,
        "after": after,
        "ordering": {"lsn": lsn, "source_ts_ms": 1700000000000 + lsn, "table": "orders"},
    }


def _object_changed_count(foundry: FoundryLite, ctx: RequestContext, object_id: str) -> int:
    return sum(
        1
        for event in foundry.operations.query_runs(ctx=ctx, run_type="outbox")["outboxEvents"]
        if event["event_type"] == "object.changed" and event["aggregate_id"] == f"Order/{object_id}"
    )


def _index_run(foundry: FoundryLite, ctx: RequestContext, run_id: str) -> dict[str, object]:
    return dict(
        next(
            row for row in foundry.operations.query_runs(ctx=ctx, run_type="index")["indexRuns"] if row["id"] == run_id
        )
    )


def _materialization_rows_for_version(
    foundry: FoundryLite,
    ctx: RequestContext,
    version_id: str,
) -> list[dict[str, object]]:
    run = _materialization_run_for_version(foundry, ctx, version_id)
    return [dict(row) for row in foundry.materialization.replay_rows(str(run["id"]), ctx=ctx).rows]


def _materialization_run_for_version(
    foundry: FoundryLite,
    ctx: RequestContext,
    version_id: str,
) -> dict[str, object]:
    return dict(
        next(
            row
            for row in foundry.operations.query_runs(ctx=ctx, run_type="materialization")["materializationRuns"]
            if row["target_dataset_version_id"] == version_id
        )
    )


def _object_index_version(foundry: FoundryLite, ctx: RequestContext, object_id: str) -> str:
    with foundry.engine.begin() as conn:
        row = conn.execute(
            select(db.object_records.c.index_version).where(
                db.object_records.c.tenant_id == ctx.tenant_id,
                db.object_records.c.object_type_api_name == "Order",
                db.object_records.c.object_id == object_id,
            )
        ).scalar_one()
    return str(row)
