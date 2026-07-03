from __future__ import annotations

from pathlib import Path

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import RuntimeRow
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.infrastructure import schema as db
from sqlalchemy import select

from tests.conftest import DEMO_ROOT, prepare_indexed_demo

_ORDERS_HEADER = "order_id,customer_id,source_status,amount,margin,order_ts,region\n"
_ORDER_1001 = "O-1001,C-100,PENDING,1200.0,230.0,2026-06-09T10:00:00Z,NA\n"
_ORDER_1002 = "O-1002,C-101,REVIEW,800.0,80.0,2026-06-09T11:00:00Z,EU\n"
_ORDER_1003 = "O-1003,C-100,APPROVED,300.0,20.0,2026-06-09T12:00:00Z,NA\n"


def test_incremental_reindex_touches_only_changed_rows(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = prepare_indexed_demo(foundry)
    changed_before = foundry.objects.get("Order", "O-1001", ctx=ctx)
    unchanged_before = foundry.objects.get("Order", "O-1002", ctx=ctx)
    unchanged_events_before = _object_changed_count(foundry, ctx, "Order/O-1002")
    _upload_orders(
        foundry,
        tmp_path,
        ctx,
        "O-1001,C-100,PENDING,1500.0,230.0,2026-06-09T10:00:00Z,NA\n"
        + _ORDER_1002
        + "O-1004,C-101,PENDING,450.0,45.0,2026-06-10T09:00:00Z,EU\n",
    )

    result = foundry.objects.reindex("Order", ctx=ctx)
    changed = foundry.objects.get("Order", "O-1001", ctx=ctx)
    unchanged = foundry.objects.get("Order", "O-1002", ctx=ctx)
    added = foundry.objects.get("Order", "O-1004", ctx=ctx)
    deleted = foundry.objects.get("Order", "O-1003", ctx=ctx)
    run = _index_run(foundry, ctx, result["index_run_id"])

    assert result["rows_read"] == 3
    assert result["objects_upserted"] == 2
    assert result["objects_deleted"] == 1
    assert run["source_ref"]["mode"] == "changelog_incremental"
    assert run["source_ref"]["changed"] == 2
    assert run["source_ref"]["deleted"] == 1
    assert run["source_ref"]["skipped"] == 1
    assert changed["properties"]["amount"] == 1500.0
    assert changed["objectVersion"] == changed_before["objectVersion"] + 1
    assert added["properties"]["amount"] == 450.0
    assert added["objectVersion"] == 1
    assert deleted["deleted"] is True
    assert deleted["deletionReason"] == "source_missing"
    # The unchanged row is untouched: no object_version bump and no spurious
    # object.changed event, so held expectedObjectVersion values stay valid.
    assert unchanged["objectVersion"] == unchanged_before["objectVersion"]
    assert unchanged["properties"] == unchanged_before["properties"]
    assert _object_changed_count(foundry, ctx, "Order/O-1002") == unchanged_events_before


def test_first_reindex_runs_full_and_seeds_changelog_for_next_refresh(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)
    first_run = next(
        run
        for run in foundry.operations.list_runs(ctx=ctx)["indexRuns"]
        if run["object_type_api_name"] == "Order" and run["trigger_type"] == "reindex"
    )

    rerun = foundry.objects.reindex("Order", ctx=ctx)
    rerun_row = _index_run(foundry, ctx, rerun["index_run_id"])

    # No stored baseline exists on the very first refresh, so it must run the
    # legacy full pass; the seeded hashes then let the immediate re-run of the
    # same dataset version resolve incrementally with nothing to do.
    assert first_run["source_ref"]["mode"] == "full"
    assert first_run["source_ref"]["changed"] == 3
    assert first_run["source_ref"]["skipped"] == 0
    assert rerun["objects_upserted"] == 0
    assert rerun["objects_deleted"] == 0
    assert rerun_row["source_ref"]["mode"] == "changelog_incremental"
    assert rerun_row["source_ref"]["changed"] == 0
    assert rerun_row["source_ref"]["skipped"] == 3


def test_incremental_rerun_of_same_dataset_version_is_idempotent(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = prepare_indexed_demo(foundry)
    _upload_orders(
        foundry,
        tmp_path,
        ctx,
        "O-1001,C-100,PENDING,1500.0,230.0,2026-06-09T10:00:00Z,NA\n" + _ORDER_1002 + _ORDER_1003,
    )
    foundry.objects.reindex("Order", ctx=ctx)
    before = foundry.objects.get("Order", "O-1001", ctx=ctx)

    replay = foundry.objects.reindex("Order", ctx=ctx)
    replay_row = _index_run(foundry, ctx, replay["index_run_id"])
    after = foundry.objects.get("Order", "O-1001", ctx=ctx)

    assert replay["objects_upserted"] == 0
    assert replay["objects_deleted"] == 0
    assert replay_row["source_ref"]["mode"] == "changelog_incremental"
    assert replay_row["source_ref"]["changed"] == 0
    assert replay_row["source_ref"]["skipped"] == 3
    assert after["objectVersion"] == before["objectVersion"]
    assert after["properties"] == before["properties"]


def test_user_edit_on_unchanged_row_survives_incremental_refresh(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1002", ctx=ctx)
    foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1002",
        expected_object_version=order["objectVersion"],
        params={"reason": "edit survives changelog skip"},
        idempotency_key="changelog-unchanged-edit",
        ctx=ctx,
    )
    edited = foundry.objects.get("Order", "O-1002", ctx=ctx)
    _upload_orders(
        foundry,
        tmp_path,
        ctx,
        "O-1001,C-100,PENDING,1500.0,230.0,2026-06-09T10:00:00Z,NA\n" + _ORDER_1002 + _ORDER_1003,
    )

    result = foundry.objects.reindex("Order", ctx=ctx)
    run = _index_run(foundry, ctx, result["index_run_id"])
    after = foundry.objects.get("Order", "O-1002", ctx=ctx)

    # Edits live in the edit layer, not the parquet base row, so an edit alone
    # must not force the row through the upsert path - and skipping the row
    # must leave the edit (and its object_version) fully intact.
    assert run["source_ref"]["mode"] == "changelog_incremental"
    assert run["source_ref"]["skipped"] == 2
    assert after["objectVersion"] == edited["objectVersion"]
    assert after["properties"]["status"] == "APPROVED"
    assert after["properties"]["operatorNote"] == "edit survives changelog skip"


def test_user_edit_on_changed_row_merges_and_records_conflict(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = _prepare_indexed_demo_with_conflict_review(foundry, tmp_path)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "conflict review edit"},
        idempotency_key="changelog-conflict-edit",
        ctx=ctx,
    )
    edited = foundry.objects.get("Order", "O-1001", ctx=ctx)
    committed = _upload_orders(
        foundry,
        tmp_path,
        ctx,
        "O-1001,C-100,CANCELLED,1500.0,230.0,2026-06-09T10:00:00Z,NA\n" + _ORDER_1002 + _ORDER_1003,
    )

    result = foundry.objects.reindex("Order", ctx=ctx)
    run = _index_run(foundry, ctx, result["index_run_id"])
    merged = foundry.objects.get("Order", "O-1001", ctx=ctx)
    conflicts = _open_conflicts(foundry, "O-1001")

    # The changed row goes through the exact per-record machinery a full
    # rebuild uses: source refresh bumps the version, the edit still wins the
    # merge, and the source-vs-edit collision is recorded for review.
    assert run["source_ref"]["mode"] == "changelog_incremental"
    assert run["source_ref"]["changed"] == 1
    assert merged["objectVersion"] > edited["objectVersion"]
    assert merged["properties"]["status"] == "APPROVED"
    assert merged["properties"]["amount"] == 1500.0
    assert len(conflicts) == 1
    assert conflicts[0]["property_api_name"] == "status"
    assert conflicts[0]["source_value"] == "CANCELLED"
    assert conflicts[0]["edit_value"] == "APPROVED"
    # Order is multi-datasource: the record's source id is the composite of
    # both segment versions in declaration order (erp first).
    assert str(conflicts[0]["source_dataset_version_id"]).split("+")[0] == committed
    assert conflicts[0]["status"] == "open"


def test_shadow_reindex_reseeds_changelog_for_next_incremental(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = prepare_indexed_demo(foundry)
    shadow = foundry.objects.shadow_reindex("Order", ctx=ctx)
    _upload_orders(
        foundry,
        tmp_path,
        ctx,
        "O-1001,C-100,PENDING,1500.0,230.0,2026-06-09T10:00:00Z,NA\n" + _ORDER_1002 + _ORDER_1003,
    )

    result = foundry.objects.reindex("Order", ctx=ctx)
    run = _index_run(foundry, ctx, result["index_run_id"])
    changed = foundry.objects.get("Order", "O-1001", ctx=ctx)

    # The shadow rebuild replaced every record under a new index version; it
    # must also refresh the hash baseline at swap time so the next plain
    # refresh can still diff instead of falling back to a full pass.
    assert shadow["is_switched"] is True
    assert run["source_ref"]["mode"] == "changelog_incremental"
    assert run["source_ref"]["changed"] == 1
    assert run["source_ref"]["skipped"] == 2
    assert changed["properties"]["amount"] == 1500.0


def test_ontology_migration_reindex_stays_full_and_reseeds_changelog(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = prepare_indexed_demo(foundry)
    foundry.ontology.apply(_status_remapped_ontology(tmp_path), ctx=ctx)
    catalog = foundry.ontology.catalog(ctx=ctx)
    order_type = next(item for item in catalog["objectTypes"] if item["apiName"] == "Order")
    reindex_key = str(order_type["config"]["objectReindexPlan"][0]["reindexKey"])

    migration_result = foundry.objects.reindex_ontology_migration("Order", reindex_key, ctx=ctx)
    migration_run = _index_run(foundry, ctx, migration_result["index_run_id"])
    _upload_orders(
        foundry,
        tmp_path,
        ctx,
        "O-1001,C-100,PENDING,1500.0,230.0,2026-06-09T10:00:00Z,NA\n" + _ORDER_1002 + _ORDER_1003,
    )
    incremental = foundry.objects.reindex("Order", ctx=ctx)
    incremental_run = _index_run(foundry, ctx, incremental["index_run_id"])

    # An ontology-migration reindex changes the property projection, so it must
    # reprocess every row (mode stays "full") - and reseed the baseline under
    # the new projection so the follow-up refresh diffs correctly against it.
    assert migration_run["trigger_type"] == "ontology_migration_reindex"
    assert migration_run["source_ref"]["mode"] == "full"
    assert migration_result["rows_read"] == 3
    assert incremental_run["source_ref"]["mode"] == "changelog_incremental"
    assert incremental_run["source_ref"]["changed"] == 1
    assert incremental_run["source_ref"]["skipped"] == 2


def _upload_orders(foundry: FoundryLite, tmp_path: Path, ctx: RequestContext, body: str) -> str:
    # Order is multi-datasource (erp + finance segments): refresh both clean
    # datasets so the merged PK universe follows the new raw upload.
    csv_path = tmp_path / f"orders_{abs(hash(body))}.csv"
    csv_path.write_text(_ORDERS_HEADER + body, encoding="utf-8")
    foundry.datasets.upload_csv("raw.erp_orders", str(csv_path), ctx=ctx)
    committed = foundry.transforms.run("clean_orders", ctx=ctx)
    foundry.transforms.run("clean_order_finance", ctx=ctx)
    return committed.version_id


def _prepare_indexed_demo_with_conflict_review(foundry: FoundryLite, tmp_path: Path) -> RequestContext:
    """Demo setup whose Order.status records conflicts instead of silently winning."""
    ctx = demo_admin_context()
    foundry.demo.seed_files()
    foundry.datasets.ensure("raw.erp_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("raw.crm_customers", ctx=ctx, primary_key=["customer_id"])
    foundry.datasets.ensure("clean.orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.order_finance", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.customers", ctx=ctx, primary_key=["customer_id"])
    foundry.datasets.ensure("ops.action_log", ctx=ctx, primary_key=["action_run_id"])
    foundry.datasets.ensure("ops.order_current", ctx=ctx, primary_key=["orderId"])
    foundry.demo.register_transforms(ctx)
    foundry.datasets.upload_csv("raw.erp_orders", str(DEMO_ROOT / "data" / "orders.csv"), ctx=ctx)
    foundry.datasets.upload_csv("raw.crm_customers", str(DEMO_ROOT / "data" / "customers.csv"), ctx=ctx)
    foundry.transforms.run("clean_orders", ctx=ctx)
    foundry.transforms.run("clean_order_finance", ctx=ctx)
    foundry.transforms.run("clean_customers", ctx=ctx)
    ontology = (DEMO_ROOT / "ontology" / "order-customer.yaml").read_text(encoding="utf-8")
    conflict_ontology = tmp_path / "order-customer-conflict-review.yaml"
    conflict_ontology.write_text(
        ontology.replace("editPolicy: edit_wins", "editPolicy: conflict_requires_review", 1),
        encoding="utf-8",
    )
    foundry.ontology.apply(str(conflict_ontology), ctx=ctx)
    foundry.objects.reindex("Order", ctx=ctx)
    foundry.objects.reindex("Customer", ctx=ctx)
    return ctx


def _status_remapped_ontology(tmp_path: Path) -> Path:
    ontology = (DEMO_ROOT / "ontology" / "order-customer.yaml").read_text(encoding="utf-8")
    path = tmp_path / "order-customer-status-remapped.yaml"
    path.write_text(ontology.replace("        column: source_status", "        column: customer_id", 1))
    return path


def _open_conflicts(foundry: FoundryLite, object_id: str) -> list[dict[str, object]]:
    with foundry.engine.begin() as conn:
        rows = conn.execute(select(db.object_conflicts).where(db.object_conflicts.c.object_id == object_id))
        return [dict(row) for row in rows.mappings().all()]


def _object_changed_count(foundry: FoundryLite, ctx: RequestContext, aggregate_id: str) -> int:
    return sum(
        1
        for event in foundry.operations.list_runs(ctx=ctx)["outboxEvents"]
        if event["event_type"] == "object.changed" and event["aggregate_id"] == aggregate_id
    )


def _index_run(foundry: FoundryLite, ctx: RequestContext, run_id: str) -> RuntimeRow:
    return next(run for run in foundry.operations.list_runs(ctx=ctx)["indexRuns"] if run["id"] == run_id)
