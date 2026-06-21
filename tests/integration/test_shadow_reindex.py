from __future__ import annotations

from pathlib import Path

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import RuntimeRow
from foundry_lite.application.services.object_store.indexing import ObjectIndexingService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

from tests.conftest import prepare_indexed_demo


def test_shadow_reindex_replays_action_edits(
    foundry: FoundryLite,
) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Shadow validation proof"},
        idempotency_key="shadow-reindex-proof",
        ctx=ctx,
    )
    approved = foundry.objects.get("Order", "O-1001", ctx=ctx)

    result = foundry.objects.shadow_reindex("Order", ctx=ctx)
    after_switch = foundry.objects.get("Order", "O-1001", ctx=ctx)
    query_page = foundry.objects.query("Order", ctx=ctx, limit=10)
    run = _index_run(foundry, ctx, result["index_run_id"])

    assert result["is_switched"] is True
    assert result["previousIndexVersion"] == "active"
    assert result["indexVersion"].startswith("index_run_")
    assert result["validation"]["expectedCount"] == result["validation"]["actualCount"] == 3
    assert result["validation"]["expectedHash"] == result["validation"]["actualHash"]
    assert after_switch["properties"] == approved["properties"]
    assert after_switch["objectVersion"] == approved["objectVersion"]
    assert after_switch["properties"]["status"] == "APPROVED"
    assert after_switch["properties"]["operatorNote"] == "Shadow validation proof"
    assert [item["objectId"] for item in query_page["items"]] == ["O-1001", "O-1002", "O-1003"]
    assert run["trigger_type"] == "shadow_reindex"
    assert run["status"] == "succeeded"


def test_shadow_reindex_alias_switch_cursor_version_safe(
    foundry: FoundryLite,
) -> None:
    ctx = prepare_indexed_demo(foundry)
    order_by = [{"property": "amount", "direction": "desc"}]
    first_page = foundry.objects.query("Order", ctx=ctx, order_by=order_by, limit=1)
    cursor = first_page["nextCursor"]
    assert cursor is not None

    result = foundry.objects.shadow_reindex("Order", ctx=ctx)

    with pytest.raises(ValidationFailed, match="active index version"):
        foundry.objects.query("Order", ctx=ctx, order_by=order_by, cursor=cursor)
    assert result["is_switched"] is True
    fresh_page = foundry.objects.query("Order", ctx=ctx, order_by=order_by, limit=3)
    assert [item["objectId"] for item in fresh_page["items"]] == ["O-1001", "O-1002", "O-1003"]


def test_shadow_reindex_validation_failure_keeps_existing_active_index(
    foundry: FoundryLite,
) -> None:
    ctx = prepare_indexed_demo(foundry)
    before = foundry.objects.get("Order", "O-1001", ctx=ctx)

    with pytest.raises(ValidationFailed):
        foundry.objects.shadow_reindex("Order", ctx=ctx, expected_hash="not-the-baseline-hash")

    after_failure = foundry.objects.get("Order", "O-1001", ctx=ctx)
    query_page = foundry.objects.query("Order", ctx=ctx, limit=10)
    failed_runs = [
        run for run in foundry.operations.list_runs(ctx=ctx)["indexRuns"] if run["trigger_type"] == "shadow_reindex"
    ]

    assert after_failure == before
    assert [item["objectId"] for item in query_page["items"]] == ["O-1001", "O-1002", "O-1003"]
    assert failed_runs[-1]["status"] == "failed"


def test_reindex_same_dataset_version_idempotent(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)
    before = foundry.objects.get("Order", "O-1001", ctx=ctx)
    changed_before = _object_changed_count(foundry, ctx, "Order/O-1001")

    result = foundry.objects.reindex("Order", ctx=ctx)
    after = foundry.objects.get("Order", "O-1001", ctx=ctx)
    changed_after = _object_changed_count(foundry, ctx, "Order/O-1001")

    # Re-indexing the same source dataset version with unchanged content must not
    # bump object_version or emit a spurious object.changed event. Otherwise every
    # replay churns search/materialization projections and breaks any held
    # expectedObjectVersion for in-flight actions.
    assert result["rows_read"] == 3
    assert after["objectVersion"] == before["objectVersion"]
    assert after["properties"] == before["properties"]
    assert changed_after == changed_before


def test_reindex_same_dataset_version_does_not_break_expected_object_version(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)

    foundry.objects.reindex("Order", ctx=ctx)

    # The object version held before the no-op reindex must still satisfy the
    # action precondition; an idempotent reindex cannot invalidate it.
    applied = foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Action after idempotent reindex"},
        idempotency_key="reindex-idempotent-action",
        ctx=ctx,
    )

    assert applied["status"] == "succeeded"


def test_full_reindex_marks_source_missing_object_deleted(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = prepare_indexed_demo(foundry)
    before_ids = [item["objectId"] for item in foundry.objects.query("Order", ctx=ctx, limit=10)["items"]]
    reduced_orders = tmp_path / "orders_without_o1003.csv"
    reduced_orders.write_text(
        "order_id,customer_id,source_status,amount,margin,order_ts,region\n"
        "O-1001,C-100,PENDING,1200.0,230.0,2026-06-09T10:00:00Z,NA\n"
        "O-1002,C-101,REVIEW,800.0,80.0,2026-06-09T11:00:00Z,EU\n",
        encoding="utf-8",
    )

    foundry.datasets.upload_csv("raw.erp_orders", str(reduced_orders), ctx=ctx)
    foundry.transforms.run("clean_orders", ctx=ctx)
    result = foundry.objects.reindex("Order", ctx=ctx)
    after_page = foundry.objects.query("Order", ctx=ctx, limit=10)
    deleted_order = foundry.objects.get("Order", "O-1003", ctx=ctx)

    assert before_ids == ["O-1001", "O-1002", "O-1003"]
    assert result["rows_read"] == 2
    assert result["objects_deleted"] == 1
    assert [item["objectId"] for item in after_page["items"]] == ["O-1001", "O-1002"]
    assert deleted_order["deleted"] is True
    assert deleted_order["deletionReason"] == "source_missing"


def test_object_version_increments_for_base_and_edit_updates(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = prepare_indexed_demo(foundry)
    base_order = foundry.objects.get("Order", "O-1001", ctx=ctx)

    edited = _approve_with_note(foundry, ctx, base_order["objectVersion"], "version-increment-edit")
    _reindex_with_changed_amount(foundry, tmp_path, ctx, amount="1500.0")
    reindexed = foundry.objects.get("Order", "O-1001", ctx=ctx)

    # An edit update and a base update must each advance object_version, so a
    # later optimistic-concurrency precondition reflects every mutation.
    assert edited["objectVersion"] > base_order["objectVersion"]
    assert reindexed["objectVersion"] > edited["objectVersion"]
    assert reindexed["properties"]["amount"] == 1500.0


def test_object_merge_edit_only_not_overwritten_by_source(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = prepare_indexed_demo(foundry)
    base_order = foundry.objects.get("Order", "O-1001", ctx=ctx)

    _approve_with_note(foundry, ctx, base_order["objectVersion"], "edit-only-survives")
    _reindex_with_changed_amount(foundry, tmp_path, ctx, amount="1500.0")
    reindexed = foundry.objects.get("Order", "O-1001", ctx=ctx)

    # operatorNote is edit_only, so a source reindex that changes base properties
    # must not overwrite it; the edit_wins status edit also survives while the
    # source-backed amount is refreshed.
    assert reindexed["properties"]["operatorNote"] == "edit-only-survives"
    assert reindexed["properties"]["status"] == "APPROVED"
    assert reindexed["properties"]["amount"] == 1500.0


def test_shadow_reindex_catches_up_delta_edits_before_switch(
    foundry: FoundryLite, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    original_read = ObjectIndexingService._read_index_source_rows
    applied: dict[str, object] = {}

    def read_then_apply_delta_edit(self, plan):
        rows = original_read(self, plan)
        if not applied:
            applied.update(
                foundry.actions.apply(
                    "ApproveOrder",
                    object_type="Order",
                    object_id="O-1001",
                    expected_object_version=order["objectVersion"],
                    params={"reason": "delta edit during reindex"},
                    idempotency_key="delta-edit-during-reindex",
                    ctx=ctx,
                )
            )
        return rows

    monkeypatch.setattr(ObjectIndexingService, "_read_index_source_rows", read_then_apply_delta_edit)

    result = foundry.objects.shadow_reindex("Order", ctx=ctx)
    after_switch = foundry.objects.get("Order", "O-1001", ctx=ctx)

    # An action edit committed after the shadow source rows are read but before
    # the alias switch is still replayed into the switched index, because the
    # rebuild captures edit properties and switches inside one transaction.
    assert applied["status"] == "succeeded"
    assert result["is_switched"] is True
    assert after_switch["properties"]["status"] == "APPROVED"
    assert after_switch["properties"]["operatorNote"] == "delta edit during reindex"


def test_index_progress_cursor_advances_only_after_bulk_upsert_commit(
    foundry: FoundryLite, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = prepare_indexed_demo(foundry)
    before = foundry.objects.get("Order", "O-1001", ctx=ctx)
    original_index_object_row = ObjectIndexingService._index_object_row

    def fail_during_bulk_upsert(self, conn, ctx, plan, row):
        original_index_object_row(self, conn, ctx, plan, row)
        raise RuntimeError("injected bulk upsert failure")

    monkeypatch.setattr(ObjectIndexingService, "_index_object_row", fail_during_bulk_upsert)

    with pytest.raises(RuntimeError, match="injected bulk upsert failure"):
        foundry.objects.reindex("Order", ctx=ctx)

    failed_run = next(
        run
        for run in foundry.operations.list_runs(ctx=ctx)["indexRuns"]
        if run["object_type_api_name"] == "Order" and run["status"] == "failed"
    )
    after = foundry.objects.get("Order", "O-1001", ctx=ctx)

    # The bulk upsert transaction rolled back, so the index progress cursor and
    # rows_read must not advance and no partial object change becomes visible.
    assert failed_run["cursor"] == {}
    assert failed_run["rows_read"] == 0
    assert after["objectVersion"] == before["objectVersion"]
    assert after["properties"] == before["properties"]


def _approve_with_note(
    foundry: FoundryLite, ctx: RequestContext, expected_object_version: int, note: str
) -> RuntimeRow:
    foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=expected_object_version,
        params={"reason": note},
        idempotency_key=f"merge-version-{note}",
        ctx=ctx,
    )
    return foundry.objects.get("Order", "O-1001", ctx=ctx)


def _reindex_with_changed_amount(foundry: FoundryLite, tmp_path: Path, ctx: RequestContext, *, amount: str) -> None:
    modified_csv = tmp_path / "orders_v2.csv"
    modified_csv.write_text(
        "order_id,customer_id,source_status,amount,margin,order_ts,region\n"
        f"O-1001,C-100,PENDING,{amount},230.0,2026-06-09T10:00:00Z,NA\n"
        "O-1002,C-101,REVIEW,800.0,80.0,2026-06-09T11:00:00Z,EU\n"
        "O-1003,C-100,APPROVED,300.0,20.0,2026-06-09T12:00:00Z,NA\n"
    )
    foundry.datasets.upload_csv("raw.erp_orders", str(modified_csv), ctx=ctx)
    foundry.transforms.run("clean_orders", ctx=ctx)
    foundry.objects.reindex("Order", ctx=ctx)


def _object_changed_count(foundry: FoundryLite, ctx: RequestContext, aggregate_id: str) -> int:
    return sum(
        1
        for event in foundry.operations.list_runs(ctx=ctx)["outboxEvents"]
        if event["event_type"] == "object.changed" and event["aggregate_id"] == aggregate_id
    )


def _index_run(foundry: FoundryLite, ctx: RequestContext, run_id: str) -> RuntimeRow:
    return next(run for run in foundry.operations.list_runs(ctx=ctx)["indexRuns"] if run["id"] == run_id)
