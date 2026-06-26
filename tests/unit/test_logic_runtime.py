"""Unit tests for ``LogicRuntimeService`` (AIP-lite P0j, §8.11/§12.3)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest
from foundry_lite.application.ports.ai_run_repository import (
    AiContextItemRecord,
    AiExecutionRunRecord,
    AiRunLedgerSnapshot,
    AiSessionRecord,
)
from foundry_lite.application.ports.tool_executor import ConfirmationPolicy, ToolEffect
from foundry_lite.application.services.aip.logic_runtime import LogicBlock, LogicRuntimeError
from foundry_lite.application.services.aip.tool_broker import ToolSpec
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyAiRunRepository
from sqlalchemy import func, select

from tests.conftest import prepare_indexed_demo

_CTX = RequestContext(
    tenant_id="tenant-demo",
    actor_user_id="ops-user",
    roles=("admin", "ops_manager", "data_engineer"),
    request_id="req-logic-runtime",
)


def test_logic_runtime_executes_read_tool_through_broker_and_records_ai_trace(foundry: Any) -> None:
    _seed_ai_run(foundry.engine)

    result = foundry.aip.run_logic(
        logic_run_id="logic-read-1",
        ai_run_id="ai-run-logic",
        blocks=(
            LogicBlock("input", "Input"),
            LogicBlock("tool", "CallFunction", inputs=_tool_block_inputs(), depends_on=("input",)),
            LogicBlock("output", "Output", inputs={"fromBlock": "tool"}, depends_on=("tool",)),
        ),
        input_json={"question": "show purchase order"},
        tool_manifest=(_spec(),),
        agent_allowed_tools=("ontology.get_object",),
        model_allowed_classifications=("public", "internal"),
        ctx=_CTX,
    )

    ledger = _ledger(foundry.engine)
    assert result.status == "succeeded"
    assert result.graph_hash.startswith("sha256:")
    assert result.output_json["status"] == "succeeded"
    assert result.output_json["output"]["actor_user_id"] == "ops-user"
    assert [event["event_type"] for event in ledger["events"]] == ["aip.logic.started", "aip.logic.completed"]
    assert ledger["toolCalls"][0]["tool_id"] == "ontology.get_object"
    assert ledger["toolCalls"][0]["authorization_decision"] == "allowed"


def test_logic_runtime_replay_is_deterministic_for_same_graph(foundry: Any) -> None:
    _seed_ai_run(foundry.engine)
    blocks = (
        LogicBlock("input", "Input"),
        LogicBlock("output", "Output", inputs={"fromBlock": "input"}, depends_on=("input",)),
    )

    first = foundry.aip.run_logic(
        logic_run_id="logic-replay-1",
        ai_run_id="ai-run-logic",
        blocks=blocks,
        input_json={"question": "same input"},
        ctx=_CTX,
    )
    replay = foundry.aip.run_logic(
        logic_run_id="logic-replay-1",
        ai_run_id="ai-run-logic",
        blocks=blocks,
        input_json={"question": "same input"},
        ctx=_CTX,
    )

    assert replay.graph_hash == first.graph_hash
    assert replay.result_hash == first.result_hash
    assert len(_ledger(foundry.engine)["events"]) == 4


def test_logic_runtime_condition_and_human_approval_pause(foundry: Any) -> None:
    _seed_ai_run(foundry.engine)

    result = foundry.aip.run_logic(
        logic_run_id="logic-human-approval-1",
        ai_run_id="ai-run-logic",
        blocks=(
            LogicBlock("input", "Input", inputs={"approved": False}),
            LogicBlock(
                "condition",
                "Condition",
                inputs={"fromBlock": "input", "equals": {"value": {"approved": False}}},
                depends_on=("input",),
            ),
            LogicBlock(
                "approval",
                "HumanApproval",
                inputs={"reason": "manager_review_required", "notes": "x" * 220},
                depends_on=("condition",),
            ),
        ),
        input_json={},
        ctx=_CTX,
    )

    assert result.status == "waiting_human_review"
    assert result.output_json == {"status": "waiting_human_review", "reason": "manager_review_required"}
    assert result.block_results[-1].output_preview["reason"] == "manager_review_required"


def test_logic_runtime_blocks_direct_apply_action_and_records_failure(foundry: Any) -> None:
    _seed_ai_run(foundry.engine)

    with pytest.raises(LogicRuntimeError) as excinfo:
        foundry.aip.run_logic(
            logic_run_id="logic-apply-action-1",
            ai_run_id="ai-run-logic",
            blocks=(LogicBlock("apply", "ApplyAction", inputs={"actionType": "ApproveOrder"}),),
            input_json={},
            ctx=_CTX,
        )

    assert excinfo.value.reason == "human_approval_required"
    assert _ledger(foundry.engine)["events"][-1]["event_type"] == "aip.logic.failed"
    assert _table_count(foundry.engine, db.action_runs) == 0


def test_logic_runtime_rejects_missing_run_before_events(foundry: Any) -> None:
    with pytest.raises(LogicRuntimeError) as excinfo:
        foundry.aip.run_logic(
            logic_run_id="logic-missing-run-1",
            ai_run_id="missing-run",
            blocks=(LogicBlock("output", "Output", inputs={"answer": "no run"}),),
            input_json={},
            ctx=_CTX,
        )

    assert excinfo.value.reason == "run_not_found"


def test_logic_runtime_create_action_proposal_uses_review_queue_not_action_side_effect(foundry: Any) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    _seed_ai_run(foundry.engine)

    result = foundry.aip.run_logic(
        logic_run_id="logic-proposal-1",
        ai_run_id="ai-run-logic",
        blocks=(
            LogicBlock(
                "proposal",
                "CreateActionProposal",
                inputs=_proposal_inputs(order),
            ),
            LogicBlock("output", "Output", inputs={"fromBlock": "proposal"}, depends_on=("proposal",)),
        ),
        input_json={"question": "approve order if safe"},
        agent_allowed_actions=("ApproveOrder",),
        policy_version="policy-v1",
        ctx=_CTX,
    )

    assert result.status == "succeeded"
    assert result.output_json["status"] == "pending"
    assert result.output_json["proposalFingerprint"].startswith("sha256:")
    assert _table_count(foundry.engine, db.insight_reviews) == 1
    assert _table_count(foundry.engine, db.action_runs) == 0


@pytest.mark.parametrize(
    ("blocks", "max_blocks", "reason"),
    [
        ((), 25, "empty_graph"),
        ((LogicBlock("", "Input"),), 25, "schema_invalid"),
        ((LogicBlock("a", "Input"), LogicBlock("a", "Output")), 25, "duplicate_block"),
        ((LogicBlock("a", "Bogus"),), 25, "unsupported_block"),
        ((LogicBlock("a", "Output", depends_on=("missing",)),), 25, "missing_dependency"),
        ((LogicBlock("a", "Input"), LogicBlock("b", "Output")), 1, "budget_exceeded"),
        ((LogicBlock("a", "Input"),), 0, "invalid_budget"),
        (
            (LogicBlock("a", "Input", depends_on=("b",)), LogicBlock("b", "Output", depends_on=("a",))),
            25,
            "cycle_detected",
        ),
    ],
)
def test_logic_runtime_rejects_invalid_graphs_before_execution(
    foundry: Any, blocks: tuple[LogicBlock, ...], max_blocks: int, reason: str
) -> None:
    with pytest.raises(LogicRuntimeError) as excinfo:
        foundry.aip.run_logic(
            logic_run_id=f"logic-invalid-{reason}",
            ai_run_id="missing-run",
            blocks=blocks,
            input_json={},
            max_blocks=max_blocks,
            ctx=_CTX,
        )

    assert excinfo.value.reason == reason


def _invalid_proposal_inputs(**overrides: object) -> Mapping[str, object]:
    value: dict[str, object] = {
        "actionType": "ApproveOrder",
        "targetObjectType": "Order",
        "targetObjectId": "O-1001",
        "expectedObjectVersion": 1,
        "parameters": {"reason": "Inventory confirmed"},
        "evidenceContextIds": ["ctx-order-1"],
        "expiresAt": "2026-06-26T00:10:00Z",
        "claimText": "Approve O-1001 based on selected AI evidence.",
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize(
    ("block", "reason"),
    [
        (
            LogicBlock("tool", "CallFunction", inputs={"toolId": "ontology.get_object", "version": "2026-06-25"}),
            "schema_invalid",
        ),
        (
            LogicBlock(
                "tool",
                "CallFunction",
                inputs={"toolId": "ontology.get_object", "version": "2026-06-25", "arguments": []},
            ),
            "schema_invalid",
        ),
        (
            LogicBlock("output", "Output", inputs={"fromBlock": "never-ran"}),
            "missing_output_source",
        ),
        (
            LogicBlock(
                "proposal",
                "CreateActionProposal",
                inputs=_invalid_proposal_inputs(expectedObjectVersion=0),
            ),
            "schema_invalid",
        ),
        (
            LogicBlock(
                "proposal",
                "CreateActionProposal",
                inputs=_invalid_proposal_inputs(parameters=[]),
            ),
            "schema_invalid",
        ),
        (
            LogicBlock(
                "proposal",
                "CreateActionProposal",
                inputs=_invalid_proposal_inputs(evidenceContextIds=[123]),
            ),
            "schema_invalid",
        ),
    ],
)
def test_logic_runtime_rejects_invalid_block_inputs(foundry: Any, block: LogicBlock, reason: str) -> None:
    _seed_ai_run(foundry.engine)

    with pytest.raises(LogicRuntimeError) as excinfo:
        foundry.aip.run_logic(
            logic_run_id=f"logic-invalid-block-{block.block_id}-{reason}",
            ai_run_id="ai-run-logic",
            blocks=(block,),
            input_json={},
            tool_manifest=(_spec(),),
            agent_allowed_tools=("ontology.get_object",),
            agent_allowed_actions=("ApproveOrder",),
            model_allowed_classifications=("public", "internal"),
            ctx=_CTX,
        )

    assert excinfo.value.reason == reason


def test_logic_runtime_accepts_tool_arguments_json_string(foundry: Any) -> None:
    _seed_ai_run(foundry.engine)
    arguments = json.dumps({"object_type": "PurchaseOrder", "object_id": "PO-1042", "property_names": ["id"]})

    result = foundry.aip.run_logic(
        logic_run_id="logic-tool-json-string-1",
        ai_run_id="ai-run-logic",
        blocks=(LogicBlock("tool", "CallFunction", inputs={**_tool_block_inputs(), "arguments": arguments}),),
        input_json={},
        tool_manifest=(_spec(),),
        agent_allowed_tools=("ontology.get_object",),
        model_allowed_classifications=("public", "internal"),
        ctx=_CTX,
    )

    assert result.status == "succeeded"
    assert result.output_json["resultHash"].startswith("sha256:")


def _tool_block_inputs() -> Mapping[str, object]:
    return {
        "toolId": "ontology.get_object",
        "version": "2026-06-25",
        "toolCallId": "tool-call-logic-1",
        "arguments": {"object_type": "PurchaseOrder", "object_id": "PO-1042", "property_names": ["id"]},
        "occurredAt": "2026-06-25T00:00:00Z",
    }


def _proposal_inputs(order: Mapping[str, object]) -> Mapping[str, object]:
    return {
        "actionType": "ApproveOrder",
        "targetObjectType": "Order",
        "targetObjectId": "O-1001",
        "expectedObjectVersion": _object_version(order),
        "parameters": {"reason": "Inventory confirmed"},
        "evidenceContextIds": ["ctx-order-1"],
        "expiresAt": "2026-06-26T00:10:00Z",
        "claimText": "Approve O-1001 based on selected AI evidence.",
        "originatingToolCallId": "tool-call-logic-1",
    }


def _spec(
    *,
    tool_id: str = "ontology.get_object",
    effect: ToolEffect = "READ",
    confirmation_policy: ConfirmationPolicy = "NONE",
) -> ToolSpec:
    return ToolSpec(
        tool_id=tool_id,
        version="2026-06-25",
        input_schema={
            "type": "object",
            "required": ["object_type", "object_id"],
            "additionalProperties": False,
            "properties": {
                "object_type": {"type": "string"},
                "object_id": {"type": "string"},
                "property_names": {"type": "array"},
            },
        },
        output_schema={"type": "object"},
        effect=effect,
        required_permission="object:read",
        confirmation_policy=confirmation_policy,
        object_type_allowlist=("PurchaseOrder",),
        property_allowlist=("id", "status", "supplier", "margin"),
        result_classification="internal",
    )


def _seed_ai_run(engine: Any) -> None:
    repository = SqlAlchemyAiRunRepository(engine)
    with engine.begin() as transaction:
        repository.create_session(transaction=transaction, record=_session_record())
        repository.create_execution_run(transaction=transaction, record=_run_record())
        repository.record_context_item(transaction=transaction, record=_context_item_record())


def _session_record() -> AiSessionRecord:
    return AiSessionRecord(
        id="ai-session-logic",
        tenant_id="tenant-demo",
        agent_version_id="agent-v1",
        actor_user_id="ops-user",
        status="active",
        created_at="2026-06-25T00:00:00Z",
        last_activity_at="2026-06-25T00:00:01Z",
    )


def _run_record() -> AiExecutionRunRecord:
    return AiExecutionRunRecord(
        id="ai-run-logic",
        tenant_id="tenant-demo",
        session_id="ai-session-logic",
        agent_version_id="agent-v1",
        actor_user_id="ops-user",
        request_id="req-logic-runtime",
        trace_id="trace-logic-runtime",
        status="running",
        ontology_version_id="ontology-v1",
        model_alias_version="gpt-4.1@2026-06-25",
        resolved_model_id="gpt-4.1",
        resolved_model_revision="2026-06-25",
        prompt_version_id="prompt-v1",
        compiled_prompt_hash="sha256:prompt",
        tool_manifest_hash="sha256:tools",
        context_manifest_hash="sha256:context-manifest",
        state_snapshot_hash="sha256:state",
        policy_snapshot_hash="sha256:policy",
        budget_json={"maxBlocks": 25},
        usage_json=None,
        error_json=None,
        started_at="2026-06-25T00:00:02Z",
        completed_at=None,
    )


def _context_item_record() -> AiContextItemRecord:
    return AiContextItemRecord(
        id="context-item-logic-1",
        tenant_id="tenant-demo",
        ai_run_id="ai-run-logic",
        context_id="ctx-order-1",
        kind="object",
        source_resource_type="object",
        source_resource_id="O-1001",
        source_version="object-version-1",
        content_hash="sha256:context",
        retrieval_method="object-read",
        relevance_score=1.0,
        security_partition="tenant-demo:internal",
        token_estimate=24,
        is_selected=True,
        omission_reason=None,
    )


def _ledger(engine: Any) -> AiRunLedgerSnapshot:
    with engine.begin() as transaction:
        ledger = SqlAlchemyAiRunRepository(engine).ledger_for_run(
            transaction=transaction, tenant_id="tenant-demo", ai_run_id="ai-run-logic"
        )
    assert ledger is not None
    return ledger


def _object_version(order: Mapping[str, object]) -> int:
    value = order["objectVersion"]
    assert isinstance(value, int)
    return value


def _table_count(engine: Any, table: Any) -> int:
    with engine.begin() as transaction:
        return int(transaction.execute(select(func.count()).select_from(table)).scalar_one())


def test_logic_runtime_has_json_serializable_public_result(foundry: Any) -> None:
    _seed_ai_run(foundry.engine)

    result = foundry.aip.run_logic(
        logic_run_id="logic-json-1",
        ai_run_id="ai-run-logic",
        blocks=(LogicBlock("output", "Output", inputs={"answer": "ok"}),),
        input_json={},
        ctx=_CTX,
    )

    json.dumps(result.output_json, sort_keys=True)
