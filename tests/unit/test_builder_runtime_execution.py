"""Unit tests for AIP Builder-to-runtime execution (P0m, §14.6/§8.11)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure import schema as db
from sqlalchemy import func, select

from tests.conftest import prepare_indexed_demo

_CTX = RequestContext(
    tenant_id="tenant-demo",
    actor_user_id="ops-user",
    roles=("admin", "data_engineer", "ops_manager"),
    request_id="req-builder-runtime",
)


def test_builder_runtime_validates_seeds_ai_ledger_and_creates_review_proposal(foundry: Any) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)

    result = foundry.aip.run_builder_payload(payload=_run_payload(order), ctx=_CTX)

    ledger = foundry.operations.run_detail("ai", result.ai_run_id or "", ctx=_CTX)
    assert result.run_status == "succeeded"
    assert result.validation.validation_status == "ready"
    assert result.logic_result is not None
    assert result.logic_result.output_json["status"] == "pending"
    assert result.to_payload()["operations"] == {
        "runType": "ai",
        "runId": result.ai_run_id,
        "detailPath": f"/api/operations/runs/ai/{result.ai_run_id}",
    }
    assert ledger["ai"]["summary"]["status"] == "succeeded"
    assert ledger["ai"]["summary"]["contextItemCount"] == 1
    assert ledger["ai"]["summary"]["toolCallCount"] == 1
    assert _table_count(foundry.engine, db.insight_reviews) == 1
    assert _table_count(foundry.engine, db.action_runs) == 0


def test_builder_runtime_blocked_validation_does_not_seed_ai_run(foundry: Any) -> None:
    result = foundry.aip.run_builder_payload(
        payload={**_run_payload({"objectVersion": 1}), "contextSources": [_context("tenant-other:internal")]},
        ctx=_CTX,
    )

    assert result.run_status == "blocked"
    assert result.ai_run_id is None
    assert result.logic_result is None
    assert {issue.code for issue in result.validation.blocking_issues} == {"tenant_partition_mismatch"}
    assert _table_count(foundry.engine, db.ai_execution_runs) == 0


def _run_payload(order: Mapping[str, object]) -> dict[str, object]:
    return {
        "logicRunId": "logic-builder-runtime-1",
        "agentVersionId": "agent.order-ops.v1",
        "releaseChannel": "dev",
        "modelAliasVersion": "gpt-governed@v1",
        "promptVersionId": "prompt-order-copilot@v1",
        "contextSources": [_context("tenant-demo:internal")],
        "toolManifest": [_tool()],
        "logicBlocks": _logic_blocks(order),
        "evalAxes": ["retrieval", "answer", "citation", "tool", "action", "security"],
        "agentAllowedTools": ["ontology.get_object"],
        "agentAllowedActions": ["ApproveOrder"],
        "modelAllowedClassifications": ["public", "internal"],
        "inputJson": {"objectType": "Order", "objectId": "O-1001"},
        "userMessage": "Run the order operations agent.",
        "maxLogicBlocks": 25,
    }


def _context(security_partition: str) -> dict[str, object]:
    return {
        "sourceId": "ctx-order-ops",
        "kind": "ontology.object_set",
        "securityPartition": security_partition,
        "selectedProperties": ["orderId", "status", "priority", "customerId"],
        "tokenBudget": 800,
    }


def _tool() -> dict[str, object]:
    return {
        "toolId": "ontology.get_object",
        "version": "2026-06-25",
        "effect": "READ",
        "requiredPermission": "object:read",
        "confirmationPolicy": "NONE",
        "objectTypeAllowlist": ["Order"],
        "propertyAllowlist": ["orderId", "status", "priority", "customerId"],
        "resultClassification": "internal",
        "inputSchema": {
            "type": "object",
            "required": ["object_type", "object_id"],
            "properties": {
                "object_type": {"type": "string"},
                "object_id": {"type": "string"},
                "property_names": {"type": "array"},
            },
        },
        "outputSchema": {"type": "object"},
        "status": "published",
    }


def _logic_blocks(order: Mapping[str, object]) -> list[dict[str, object]]:
    return [
        {"blockId": "input", "kind": "Input", "inputs": {"objectId": "O-1001"}},
        {
            "blockId": "retrieve",
            "kind": "CallFunction",
            "inputs": {
                "toolId": "ontology.get_object",
                "version": "2026-06-25",
                "arguments": {
                    "object_type": "Order",
                    "object_id": "O-1001",
                    "property_names": ["orderId", "status", "priority", "customerId"],
                },
            },
            "dependsOn": ["input"],
        },
        {
            "blockId": "proposal",
            "kind": "CreateActionProposal",
            "inputs": {
                "actionType": "ApproveOrder",
                "targetObjectType": "Order",
                "targetObjectId": "O-1001",
                "expectedObjectVersion": order["objectVersion"],
                "parameters": {"reason": "Inventory confirmed"},
                "evidenceContextIds": ["ctx-order-ops"],
                "expiresAt": "2026-06-26T00:10:00Z",
                "claimText": "Approve O-1001 based on selected AIP evidence.",
                "originatingToolCallId": "logic-builder-runtime-1-retrieve",
            },
            "dependsOn": ["retrieve"],
        },
        {"blockId": "output", "kind": "Output", "inputs": {"fromBlock": "proposal"}, "dependsOn": ["proposal"]},
    ]


def _table_count(engine: Any, table: Any) -> int:
    with engine.begin() as transaction:
        return int(transaction.execute(select(func.count()).select_from(table)).scalar_one())
