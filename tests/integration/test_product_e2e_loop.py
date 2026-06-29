from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import RuntimeRunDetail
from foundry_lite.application.services.outbox_publisher_service import DEFAULT_OUTBOX_STREAM_NAME
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies

from tests.conftest import prepare_indexed_demo


def test_raw_to_action_materialization_aip_operations_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_SECRET_AIP_CITATION_NAVIGATION_SIGNER", "product-e2e-citation-secret")
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "flite")
    foundry = FoundryLite(dependencies=dependencies)
    ctx = prepare_indexed_demo(foundry)

    raw_orders = foundry.datasets.list_versions("raw.erp_orders", ctx=ctx)
    clean_orders = foundry.datasets.list_versions("clean.orders", ctx=ctx)
    pending_orders = foundry.objects.query(
        "Order",
        ctx=ctx,
        filter_ast={"property": "status", "op": "eq", "value": "PENDING"},
        limit=10,
    )
    order = foundry.objects.get("Order", "O-1001", ctx=ctx, include_explain=True)
    action = foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Product E2E hold cleared"},
        idempotency_key="product-e2e-approve-order",
        ctx=ctx,
    )
    search_update = foundry.objects.consume_search_change("Order", "O-1001", ctx=ctx)
    search_hits = foundry.objects.query("Order", ctx=ctx, search_text="Product E2E hold cleared", limit=5)
    action_log = foundry.materialization.run("action_log", ctx=ctx)
    order_current = foundry.materialization.run("order_current", ctx=ctx)
    published = foundry.operations.publish_pending_outbox(ctx=ctx, limit=100)
    agent = foundry.aip.run_agent_payload(payload=_agent_payload(), ctx=ctx)

    action_detail = foundry.operations.run_detail("action", str(action["actionRunId"]), ctx=ctx)
    ai_detail = foundry.operations.run_detail("ai", str(agent.ai_run_id), ctx=ctx)
    materialization_detail = _materialization_detail(foundry, ctx, order_current.version_id)
    outbox_rows = foundry.operations.query_runs(ctx=ctx, run_type="outbox", status="published")["outboxEvents"]
    stream_events = dependencies.stream_adapter.read_events(DEFAULT_OUTBOX_STREAM_NAME)

    assert raw_orders and clean_orders
    assert pending_orders["items"][0]["objectId"] == "O-1001"
    explain = order.get("explain")
    assert explain is not None
    assert explain["lineage"]
    assert action["status"] == "succeeded"
    assert _has_relation(action_detail, target_type="action_writeback", relation="writeback_attempt")
    assert _has_relation(action_detail, target_type="outbox", relation="emitted")
    assert search_update["status"] == "indexed"
    assert [item["objectId"] for item in search_hits["items"]] == ["O-1001"]
    assert action_log.row_count >= 1
    assert order_current.row_count == 3
    assert _materialized_status(foundry, ctx, order_current.version_id)["O-1001"] == "APPROVED"
    assert _has_relation(materialization_detail, target_type="outbox", relation="emitted")
    assert published["failed"] == 0
    assert published["published"] > 0
    assert len(stream_events) == published["published"]
    assert any(row["event_type"] == "action.run.committed" for row in outbox_rows)
    assert agent.run_status == "succeeded"
    assert agent.ai_run_id is not None
    assert agent.citations
    ai = _ai_payload(ai_detail)
    summary = _mapping(ai, "summary")
    citations = _mapping_list(ai, "citations")
    assert _int(summary, "contextItemCount") >= 1
    assert _int(summary, "modelCallCount") == 1
    assert _int(summary, "promptArtifactCount") == 1
    assert _int(summary, "citationCount") == 1
    assert str(citations[0]["rendered_ref"]).startswith("[1] object:")


def _agent_payload() -> dict[str, object]:
    return {
        "agentRunId": "product-e2e-agent",
        "agentVersionId": "agent.order-ops.v1",
        "modelAlias": "default-completion",
        "promptVersionId": "prompt-order-copilot@v1",
        "userMessage": "Explain Order O-1001 after the approval action.",
        "agentInstruction": "Answer as the Order Operations Copilot. Do not execute tools.",
        "securityPartition": "tenant-demo:internal",
        "allowedSecurityPartitions": ["tenant-demo:internal"],
        "stateJson": {"objectType": "Order", "objectId": "O-1001"},
        "outputSchema": {
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
                "citations": {"type": "array", "items": {"type": "object"}},
            },
        },
        "dataClassification": "internal",
        "maxContextItems": 4,
        "maxContextTokens": 1200,
        "maxModelCalls": 1,
        "maxLoopIterations": 1,
        "maxOutputTokens": 512,
        "policyVersion": "policy-v1",
    }


def _has_relation(detail: RuntimeRunDetail, *, target_type: str, relation: str) -> bool:
    rows = cast(list[dict[str, object]], detail["runRelations"])
    return any(row["target_run_type"] == target_type and row["relation"] == relation for row in rows)


def _materialization_detail(foundry: FoundryLite, ctx: RequestContext, version_id: str) -> RuntimeRunDetail:
    run = next(
        row
        for row in foundry.operations.query_runs(ctx=ctx, run_type="materialization")["materializationRuns"]
        if row["target_dataset_version_id"] == version_id
    )
    return foundry.operations.run_detail("materialization", str(run["id"]), ctx=ctx)


def _materialized_status(foundry: FoundryLite, ctx: RequestContext, version_id: str) -> dict[str, str]:
    rows = foundry.datasets.preview("ops.order_current", ctx=ctx, version=version_id)
    return {str(row["orderId"]): str(row["status"]) for row in rows}


def _ai_payload(detail: RuntimeRunDetail) -> dict[str, object]:
    payload = cast(Mapping[str, object], detail).get("ai")
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


def _mapping(payload: Mapping[str, object], key: str) -> dict[str, object]:
    value = payload[key]
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _mapping_list(payload: Mapping[str, object], key: str) -> list[dict[str, object]]:
    value = payload[key]
    assert isinstance(value, list)
    return cast(list[dict[str, object]], value)


def _int(payload: Mapping[str, object], key: str) -> int:
    value = payload[key]
    assert isinstance(value, int)
    return value
