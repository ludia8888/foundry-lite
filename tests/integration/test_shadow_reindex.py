from __future__ import annotations

import pytest
from foundry_lite.application.core import FoundryLiteCore
from foundry_lite.application.ports import RuntimeRow
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

from tests.conftest import prepare_indexed_demo


def test_shadow_reindex_switches_after_validation_and_replays_action_edits(
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


def _index_run(core: FoundryLiteCore, ctx: RequestContext, run_id: str) -> RuntimeRow:
    return next(run for run in core.list_runs(ctx=ctx)["indexRuns"] if run["id"] == run_id)
