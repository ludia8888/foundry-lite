from __future__ import annotations

import pytest
from foundry_lite.application.core import FoundryLiteCore
from foundry_lite.application.ports import RuntimeRow
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

from tests.conftest import prepare_indexed_demo


def test_shadow_reindex_replays_action_edits(
    core: FoundryLiteCore,
) -> None:
    ctx = prepare_indexed_demo(core)
    order = core.get_object("Order", "O-1001", ctx=ctx)
    core.apply_action(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Shadow validation proof"},
        idempotency_key="shadow-reindex-proof",
        ctx=ctx,
    )
    approved = core.get_object("Order", "O-1001", ctx=ctx)

    result = core.index_shadow_rebuild("Order", ctx=ctx)
    after_switch = core.get_object("Order", "O-1001", ctx=ctx)
    query_page = core.query_objects("Order", ctx=ctx, limit=10)
    run = _index_run(core, ctx, result["index_run_id"])

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
    core: FoundryLiteCore,
) -> None:
    ctx = prepare_indexed_demo(core)
    order_by = [{"property": "amount", "direction": "desc"}]
    first_page = core.query_objects("Order", ctx=ctx, order_by=order_by, limit=1)
    cursor = first_page["nextCursor"]
    assert cursor is not None

    result = core.index_shadow_rebuild("Order", ctx=ctx)

    with pytest.raises(ValidationFailed, match="active index version"):
        core.query_objects("Order", ctx=ctx, order_by=order_by, cursor=cursor)
    assert result["is_switched"] is True
    fresh_page = core.query_objects("Order", ctx=ctx, order_by=order_by, limit=3)
    assert [item["objectId"] for item in fresh_page["items"]] == ["O-1001", "O-1002", "O-1003"]


def test_shadow_reindex_validation_failure_keeps_existing_active_index(
    core: FoundryLiteCore,
) -> None:
    ctx = prepare_indexed_demo(core)
    before = core.get_object("Order", "O-1001", ctx=ctx)

    with pytest.raises(ValidationFailed):
        core.index_shadow_rebuild("Order", ctx=ctx, expected_hash="not-the-baseline-hash")

    after_failure = core.get_object("Order", "O-1001", ctx=ctx)
    query_page = core.query_objects("Order", ctx=ctx, limit=10)
    failed_runs = [run for run in core.list_runs(ctx=ctx)["indexRuns"] if run["trigger_type"] == "shadow_reindex"]

    assert after_failure == before
    assert [item["objectId"] for item in query_page["items"]] == ["O-1001", "O-1002", "O-1003"]
    assert failed_runs[-1]["status"] == "failed"


def test_reindex_same_dataset_version_idempotent(core: FoundryLiteCore) -> None:
    ctx = prepare_indexed_demo(core)
    before = core.get_object("Order", "O-1001", ctx=ctx)
    changed_before = _object_changed_count(core, ctx, "Order/O-1001")

    result = core.index_rebuild("Order", ctx=ctx)
    after = core.get_object("Order", "O-1001", ctx=ctx)
    changed_after = _object_changed_count(core, ctx, "Order/O-1001")

    # Re-indexing the same source dataset version with unchanged content must not
    # bump object_version or emit a spurious object.changed event. Otherwise every
    # replay churns search/materialization projections and breaks any held
    # expectedObjectVersion for in-flight actions.
    assert result["rows_read"] == 3
    assert after["objectVersion"] == before["objectVersion"]
    assert after["properties"] == before["properties"]
    assert changed_after == changed_before


def test_reindex_same_dataset_version_does_not_break_expected_object_version(core: FoundryLiteCore) -> None:
    ctx = prepare_indexed_demo(core)
    order = core.get_object("Order", "O-1001", ctx=ctx)

    core.index_rebuild("Order", ctx=ctx)

    # The object version held before the no-op reindex must still satisfy the
    # action precondition; an idempotent reindex cannot invalidate it.
    applied = core.apply_action(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Action after idempotent reindex"},
        idempotency_key="reindex-idempotent-action",
        ctx=ctx,
    )

    assert applied["status"] == "succeeded"


def _object_changed_count(core: FoundryLiteCore, ctx: RequestContext, aggregate_id: str) -> int:
    return sum(
        1
        for event in core.list_runs(ctx=ctx)["outboxEvents"]
        if event["event_type"] == "object.changed" and event["aggregate_id"] == aggregate_id
    )


def _index_run(core: FoundryLiteCore, ctx: RequestContext, run_id: str) -> RuntimeRow:
    return next(run for run in core.list_runs(ctx=ctx)["indexRuns"] if run["id"] == run_id)
