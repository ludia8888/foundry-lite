from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from foundry_lite.application.core import FoundryLiteCore
from foundry_lite.application.ports import TabularRow
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
    core = FoundryLiteCore(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    _seed_order_snapshot(core, tmp_path, ctx)
    core.apply_ontology(str(_order_ontology(tmp_path)), ctx=ctx)
    core.index_rebuild("Order", ctx=ctx)

    update = _cdc_event("topic:0:12", "u", 12, after={"order_id": "O-1001", "status": "APPROVED", "amount": 725})
    update_result = core.index_cdc_events("Order", [update], ctx=ctx)
    updated = core.get_object("Order", "O-1001", ctx=ctx)
    updated_version = updated["objectVersion"]
    object_changed_count = _object_changed_count(core, ctx, "O-1001")

    replay_result = core.index_cdc_events("Order", [update], ctx=ctx)
    stale = _cdc_event("topic:0:11", "u", 11, after={"order_id": "O-1001", "status": "REVIEW", "amount": 700})
    stale_result = core.index_cdc_events("Order", [stale], ctx=ctx)
    after_stale = core.get_object("Order", "O-1001", ctx=ctx)
    stale_snapshot = _cdc_event(
        "topic:0:10",
        "r",
        10,
        after={"order_id": "O-1001", "status": "SNAPSHOT_PENDING", "amount": 650},
    )
    stale_snapshot_result = core.index_cdc_events("Order", [stale_snapshot], ctx=ctx)
    after_stale_snapshot = core.get_object("Order", "O-1001", ctx=ctx)
    delete = _cdc_event(
        "topic:0:13",
        "d",
        13,
        before={"order_id": "O-1001", "status": "APPROVED", "amount": 725},
    )
    delete_result = core.index_cdc_events("Order", [delete], ctx=ctx)
    deleted = core.get_object("Order", "O-1001", ctx=ctx)
    late_update_after_delete = _cdc_event(
        "topic:0:11-retry",
        "u",
        11,
        after={"order_id": "O-1001", "status": "REOPENED", "amount": 999},
    )
    late_update_result = core.index_cdc_events("Order", [late_update_after_delete], ctx=ctx)
    still_deleted = core.get_object("Order", "O-1001", ctx=ctx)
    active_page = core.query_objects("Order", ctx=ctx)
    core.ensure_dataset("ops.order_current", ctx=ctx, primary_key=["orderId"])
    order_current = core.materialize("order_current", ctx=ctx)
    order_current_rows = _materialization_rows_for_version(core, ctx, order_current.version_id)

    assert update_result["objects_upserted"] == 1
    assert updated["properties"]["status"] == "APPROVED"
    assert replay_result["events_skipped"] == 1
    assert stale_result["events_skipped"] == 1
    assert after_stale["objectVersion"] == updated_version
    assert after_stale["properties"]["status"] == "APPROVED"
    assert stale_snapshot_result["events_skipped"] == 1
    assert after_stale_snapshot["objectVersion"] == updated_version
    assert after_stale_snapshot["properties"]["status"] == "APPROVED"
    assert _object_changed_count(core, ctx, "O-1001") == object_changed_count + 1
    assert delete_result["objects_deleted"] == 1
    assert deleted.get("deleted") is True
    assert deleted.get("deletionReason") == "source_deleted"
    assert late_update_result["events_skipped"] == 1
    assert still_deleted.get("deleted") is True
    assert still_deleted.get("deletionReason") == "source_deleted"
    assert active_page["items"] == []
    assert order_current_rows == []
    assert _index_run(core, ctx, delete_result["index_run_id"])["trigger_type"] == "cdc_incremental"


def test_cdc_object_indexing_inserts_new_object_and_new_tombstone(tmp_path: Path) -> None:
    core = FoundryLiteCore(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    _seed_order_snapshot(core, tmp_path, ctx)
    core.apply_ontology(str(_order_ontology(tmp_path)), ctx=ctx)

    create = _cdc_event("topic:0:20", "c", 20, after={"order_id": "O-2002", "status": "NEW", "amount": 50})
    delete = _cdc_event("topic:0:21", "d", 21, before={"order_id": "O-3003", "status": "VOID", "amount": 0})

    create_result = core.index_cdc_events("Order", [create], ctx=ctx)
    delete_result = core.index_cdc_events("Order", [delete], ctx=ctx)
    created = core.get_object("Order", "O-2002", ctx=ctx)
    deleted = core.get_object("Order", "O-3003", ctx=ctx)

    assert create_result["objects_upserted"] == 1
    assert created["properties"]["status"] == "NEW"
    assert delete_result["objects_deleted"] == 1
    assert deleted.get("deleted") is True
    assert deleted.get("deletionReason") == "source_deleted"


def test_cdc_duplicate_event_idempotent(tmp_path: Path) -> None:
    core = FoundryLiteCore(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    _seed_order_snapshot(core, tmp_path, ctx)
    core.apply_ontology(str(_order_ontology(tmp_path)), ctx=ctx)
    core.index_rebuild("Order", ctx=ctx)

    update = _cdc_event("topic:0:30", "u", 30, after={"order_id": "O-1001", "status": "APPROVED", "amount": 900})
    first = core.index_cdc_events("Order", [update], ctx=ctx)
    after_first = core.get_object("Order", "O-1001", ctx=ctx)
    duplicate = core.index_cdc_events("Order", [update], ctx=ctx)
    after_duplicate = core.get_object("Order", "O-1001", ctx=ctx)

    # Re-delivering the exact same CDC event (same topic/partition/offset id and
    # ordering) must be a no-op: no second upsert, no object_version bump, and no
    # property drift, so at-least-once delivery cannot double-apply a change.
    assert first["objects_upserted"] == 1
    assert duplicate["events_skipped"] == 1
    assert duplicate["objects_upserted"] == 0
    assert after_duplicate["objectVersion"] == after_first["objectVersion"]
    assert after_duplicate["properties"] == after_first["properties"]


def test_empty_shadow_reindex_persists_active_pointer_for_next_cdc_insert(tmp_path: Path) -> None:
    compute = EmptyReindexReadComputeAdapter()
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "flite")
    core = FoundryLiteCore(dependencies=replace(dependencies, compute_adapter=compute))
    ctx = demo_admin_context()
    _seed_order_snapshot(core, tmp_path, ctx)
    core.apply_ontology(str(_order_ontology(tmp_path)), ctx=ctx)

    compute.force_empty_reads = True
    shadow = core.index_shadow_rebuild("Order", ctx=ctx)
    create = _cdc_event("topic:0:30", "c", 30, after={"order_id": "O-4004", "status": "NEW", "amount": 88})
    create_result = core.index_cdc_events("Order", [create], ctx=ctx)
    created = core.get_object("Order", "O-4004", ctx=ctx)
    stored_index_version = _object_index_version(core, ctx, "O-4004")

    assert shadow["is_switched"] is True
    assert shadow["validation"]["expectedCount"] == shadow["validation"]["actualCount"] == 0
    assert create_result["objects_upserted"] == 1
    assert created["properties"]["status"] == "NEW"
    assert stored_index_version == shadow["indexVersion"]


def _seed_order_snapshot(core: FoundryLiteCore, tmp_path: Path, ctx: RequestContext) -> None:
    core.ensure_dataset("clean.orders", ctx=ctx, primary_key=["order_id"])
    core.ensure_dataset("raw_cdc.erp_orders", ctx=ctx, primary_key=["event_id"])
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("order_id,status,amount\nO-1001,PENDING,700\n", encoding="utf-8")
    core.upload_csv("clean.orders", csv_path, ctx=ctx)


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


def _object_changed_count(core: FoundryLiteCore, ctx: RequestContext, object_id: str) -> int:
    return sum(
        1
        for event in core.query_runs(ctx=ctx, run_type="outbox")["outboxEvents"]
        if event["event_type"] == "object.changed" and event["aggregate_id"] == f"Order/{object_id}"
    )


def _index_run(core: FoundryLiteCore, ctx: RequestContext, run_id: str) -> dict[str, object]:
    return dict(next(row for row in core.query_runs(ctx=ctx, run_type="index")["indexRuns"] if row["id"] == run_id))


def _materialization_rows_for_version(
    core: FoundryLiteCore,
    ctx: RequestContext,
    version_id: str,
) -> list[dict[str, object]]:
    run = next(
        row
        for row in core.query_runs(ctx=ctx, run_type="materialization")["materializationRuns"]
        if row["target_dataset_version_id"] == version_id
    )
    return [dict(row) for row in core.replay_materialization_rows(str(run["id"]), ctx=ctx).rows]


def _object_index_version(core: FoundryLiteCore, ctx: RequestContext, object_id: str) -> str:
    with core.engine.begin() as conn:
        row = conn.execute(
            select(db.object_records.c.index_version).where(
                db.object_records.c.tenant_id == ctx.tenant_id,
                db.object_records.c.object_type_api_name == "Order",
                db.object_records.c.object_id == object_id,
            )
        ).scalar_one()
    return str(row)
