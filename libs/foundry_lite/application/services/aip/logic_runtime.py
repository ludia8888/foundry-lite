"""AIP Logic Runtime (P0j, §8.11/§12.3).

This first slice validates and executes a typed, bounded Logic DAG without
letting blocks bypass the existing AIP safety boundaries. Read calls go through
ToolBroker, write intent goes through ActionProposal, and direct ApplyAction is
rejected until a human approval signal is present.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from foundry_lite.application.ports.ai_run_repository import AiExecutionEventRecord
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.aip.action_proposal import ActionProposalRequest, ActionProposalService
from foundry_lite.application.services.aip.tool_broker import (
    ToolBrokerRequest,
    ToolBrokerService,
    ToolCallRequest,
    ToolSpec,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.runtime_error_payloads import scrub_error_text
from foundry_lite.domain.context import RequestContext

JsonObject = Mapping[str, object]
_SUPPORTED_BLOCKS = frozenset(
    {
        "Input",
        "CallFunction",
        "Condition",
        "CreateActionProposal",
        "HumanApproval",
        "ApplyAction",
        "Output",
    }
)


@dataclass(frozen=True)
class LogicBlock:
    """One typed block in a published AIP Logic DAG."""

    block_id: str
    kind: str
    inputs: JsonObject = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class LogicRunRequest:
    """Execute one bounded Logic DAG against an existing AI run ledger."""

    logic_run_id: str
    ai_run_id: str
    blocks: tuple[LogicBlock, ...]
    input_json: JsonObject
    tool_manifest: tuple[ToolSpec, ...] = ()
    agent_allowed_tools: tuple[str, ...] = ()
    agent_allowed_actions: tuple[str, ...] = ()
    model_allowed_classifications: tuple[str, ...] = ("public",)
    policy_version: str = "policy-v1"
    max_blocks: int = 25
    remaining_timeout_seconds: int = 30
    max_tool_output_bytes: int = 4096


@dataclass(frozen=True)
class LogicBlockResult:
    block_id: str
    kind: str
    status: str
    output_hash: str
    output_preview: JsonObject


@dataclass(frozen=True)
class LogicRunResult:
    logic_run_id: str
    ai_run_id: str
    status: str
    graph_hash: str
    result_hash: str
    block_results: tuple[LogicBlockResult, ...]
    output_json: JsonObject


@dataclass
class LogicRuntimeError(Exception):
    """Typed fail-closed Logic Runtime rejection."""

    reason: str
    detail: str

    def __post_init__(self) -> None:
        Exception.__init__(self, self.detail)


class LogicRuntimeService(CoreService):
    """Validate and run a safe first slice of AIP Logic."""

    required_dependencies = ("engine", "ai_run_repository")
    required_collaborators = ("tool_broker_service", "action_proposal_service")
    action_proposal_service: ActionProposalService
    tool_broker_service: ToolBrokerService

    def run(self, ctx: RequestContext, request: LogicRunRequest) -> LogicRunResult:
        _validate_request(request)
        graph_hash = _graph_hash(request)
        order = _topological_order(request.blocks)
        self._require_ai_run(ctx, request.ai_run_id)
        sequence = self._next_event_sequence(ctx, request.ai_run_id)
        self._append_event(ctx, request, sequence, "aip.logic.started", {"graphHash": graph_hash})
        state: dict[str, JsonObject] = {}
        results: list[LogicBlockResult] = []
        try:
            status = self._run_blocks(ctx, request, order, state, results)
        except Exception as exc:
            self._append_event(ctx, request, sequence + len(results) + 1, "aip.logic.failed", _error_payload(exc))
            raise
        output = _terminal_output(order, state)
        result_hash = _hash_json({"status": status, "output": output, "graphHash": graph_hash})
        completed = {"status": status, "resultHash": result_hash, "blockCount": len(results)}
        self._append_event(ctx, request, sequence + len(results) + 1, "aip.logic.completed", completed)
        return LogicRunResult(
            logic_run_id=request.logic_run_id,
            ai_run_id=request.ai_run_id,
            status=status,
            graph_hash=graph_hash,
            result_hash=result_hash,
            block_results=tuple(results),
            output_json=output,
        )

    def _run_blocks(
        self,
        ctx: RequestContext,
        request: LogicRunRequest,
        order: Sequence[LogicBlock],
        state: dict[str, JsonObject],
        results: list[LogicBlockResult],
    ) -> str:
        for block in order:
            if len(results) >= request.max_blocks:
                raise LogicRuntimeError("budget_exceeded", "logic block count exceeds max_blocks")
            output = self._execute_block(ctx, request, block, state, len(results) + 1)
            state[block.block_id] = output
            results.append(_block_result(block, output))
            if block.kind == "HumanApproval":
                return "waiting_human_review"
        return "succeeded"

    def _execute_block(
        self,
        ctx: RequestContext,
        request: LogicRunRequest,
        block: LogicBlock,
        state: Mapping[str, JsonObject],
        sequence: int,
    ) -> JsonObject:
        if block.kind == "Input":
            return _input_output(request, block)
        if block.kind == "Condition":
            return _condition_output(block, state)
        if block.kind == "CallFunction":
            return self._call_function(ctx, request, block, sequence)
        if block.kind == "CreateActionProposal":
            return self._create_action_proposal(ctx, request, block)
        if block.kind == "HumanApproval":
            return {"status": "waiting_human_review", "reason": _text(block.inputs, "reason", "approval_required")}
        if block.kind == "ApplyAction":
            raise LogicRuntimeError("human_approval_required", "ApplyAction requires an approved proposal signal")
        if block.kind == "Output":
            return _output_block(block, state)
        raise LogicRuntimeError("unsupported_block", f"unsupported Logic block kind {block.kind}")

    def _call_function(
        self, ctx: RequestContext, request: LogicRunRequest, block: LogicBlock, sequence: int
    ) -> JsonObject:
        tool_call = _tool_call_request(request, block, sequence)
        result = self.tool_broker_service.execute(
            ctx,
            ToolBrokerRequest(
                tool_call=tool_call,
                tool_manifest=request.tool_manifest,
                agent_allowed_tools=request.agent_allowed_tools,
                model_allowed_classifications=request.model_allowed_classifications,
                remaining_timeout_seconds=request.remaining_timeout_seconds,
                max_tool_output_bytes=request.max_tool_output_bytes,
            ),
        )
        with self.engine.begin() as transaction:
            self.ai_run_repository.record_tool_call(transaction=transaction, record=result.ledger_record)
        return {
            "toolCallId": result.tool_call_id,
            "status": result.status,
            "output": dict(result.output_json),
            "argumentsHash": result.arguments_hash,
            "resultHash": result.result_hash,
        }

    def _create_action_proposal(self, ctx: RequestContext, request: LogicRunRequest, block: LogicBlock) -> JsonObject:
        proposal = self.action_proposal_service.propose(ctx, _proposal_request(request, block))
        return {
            "proposalId": proposal.proposal_id,
            "proposalFingerprint": proposal.proposal_fingerprint,
            "reviewId": proposal.review_id,
            "status": proposal.review_payload["status"],
        }

    def _require_ai_run(self, ctx: RequestContext, ai_run_id: str) -> None:
        with self.engine.begin() as transaction:
            row = self.ai_run_repository.execution_run_by_id(
                transaction=transaction, tenant_id=ctx.tenant_id, ai_run_id=ai_run_id
            )
        if row is None:
            raise LogicRuntimeError("run_not_found", f"AI run {ai_run_id} was not found")

    def _next_event_sequence(self, ctx: RequestContext, ai_run_id: str) -> int:
        with self.engine.begin() as transaction:
            ledger = self.ai_run_repository.ledger_for_run(
                transaction=transaction, tenant_id=ctx.tenant_id, ai_run_id=ai_run_id
            )
        if ledger is None:
            raise LogicRuntimeError("run_not_found", f"AI run {ai_run_id} was not found")
        sequences = [row.get("sequence") for row in ledger["events"]]
        return max((int(value) for value in sequences if isinstance(value, int)), default=0) + 1

    def _append_event(
        self, ctx: RequestContext, request: LogicRunRequest, sequence: int, event_type: str, payload: JsonObject
    ) -> None:
        event = _event_record(ctx, request, sequence, event_type, payload)
        with self.engine.begin() as transaction:
            self.ai_run_repository.append_execution_event(transaction=transaction, record=event)


def _validate_request(request: LogicRunRequest) -> None:
    _required_text(request.logic_run_id, "logic_run_id")
    _required_text(request.ai_run_id, "ai_run_id")
    if not request.blocks:
        raise LogicRuntimeError("empty_graph", "logic graph must contain at least one block")
    if request.max_blocks <= 0:
        raise LogicRuntimeError("invalid_budget", "max_blocks must be positive")
    if len(request.blocks) > request.max_blocks:
        raise LogicRuntimeError("budget_exceeded", "logic graph exceeds max_blocks")
    _topological_order(request.blocks)


def _topological_order(blocks: Sequence[LogicBlock]) -> tuple[LogicBlock, ...]:
    by_id = _blocks_by_id(blocks)
    visited: set[str] = set()
    ordered: list[LogicBlock] = []
    for root in blocks:
        if root.block_id in visited:
            continue
        _visit_iterative(root, by_id, visited, ordered)
    return tuple(ordered)


def _blocks_by_id(blocks: Sequence[LogicBlock]) -> dict[str, LogicBlock]:
    by_id: dict[str, LogicBlock] = {}
    for block in blocks:
        _required_text(block.block_id, "block_id")
        if block.block_id in by_id:
            raise LogicRuntimeError("duplicate_block", f"duplicate Logic block id {block.block_id}")
        if block.kind not in _SUPPORTED_BLOCKS:
            raise LogicRuntimeError("unsupported_block", f"unsupported Logic block kind {block.kind}")
        by_id[block.block_id] = block
    return by_id


def _visit_iterative(
    root: LogicBlock,
    by_id: Mapping[str, LogicBlock],
    visited: set[str],
    ordered: list[LogicBlock],
) -> None:
    """Iterative post-order DFS so deep dependency chains cannot raise RecursionError."""
    on_path: set[str] = {root.block_id}
    stack: list[tuple[LogicBlock, int]] = [(root, 0)]
    while stack:
        block, index = stack[-1]
        if index < len(block.depends_on):
            stack[-1] = (block, index + 1)
            dependency_id = block.depends_on[index]
            dependency = by_id.get(dependency_id)
            if dependency is None:
                raise LogicRuntimeError("missing_dependency", f"block {block.block_id} depends on {dependency_id}")
            if dependency.block_id in visited:
                continue
            if dependency.block_id in on_path:
                raise LogicRuntimeError("cycle_detected", "logic graph contains a dependency cycle")
            on_path.add(dependency.block_id)
            stack.append((dependency, 0))
        else:
            stack.pop()
            on_path.discard(block.block_id)
            visited.add(block.block_id)
            ordered.append(block)


def _input_output(request: LogicRunRequest, block: LogicBlock) -> JsonObject:
    if block.inputs:
        return {"value": dict(block.inputs)}
    return {"value": dict(request.input_json)}


def _condition_output(block: LogicBlock, state: Mapping[str, JsonObject]) -> JsonObject:
    source = _required_input_text(block, "fromBlock")
    expected = block.inputs.get("equals", True)
    actual = state.get(source)
    return {"condition": actual == expected, "sourceBlock": source}


def _tool_call_request(request: LogicRunRequest, block: LogicBlock, sequence: int) -> ToolCallRequest:
    tool_id = _required_input_text(block, "toolId")
    version = _required_input_text(block, "version")
    return ToolCallRequest(
        tool_call_id=_text(block.inputs, "toolCallId", f"{request.logic_run_id}-{block.block_id}"),
        tool_id=tool_id,
        version=version,
        arguments_json=_arguments_json(block.inputs.get("arguments")),
        ai_run_id=request.ai_run_id,
        sequence=sequence,
        request_id=_text(block.inputs, "requestId", request.logic_run_id),
        idempotency_key=_text(block.inputs, "idempotencyKey", ""),
        occurred_at=_text(block.inputs, "occurredAt", _now()),
    )


def _proposal_request(request: LogicRunRequest, block: LogicBlock) -> ActionProposalRequest:
    return ActionProposalRequest(
        originating_ai_run_id=request.ai_run_id,
        action_type=_required_input_text(block, "actionType"),
        target_object_type=_required_input_text(block, "targetObjectType"),
        target_object_id=_required_input_text(block, "targetObjectId"),
        expected_object_version=_required_input_int(block, "expectedObjectVersion"),
        parameters=_required_input_mapping(block, "parameters"),
        evidence_context_ids=_required_input_tuple(block, "evidenceContextIds"),
        agent_allowed_actions=request.agent_allowed_actions,
        policy_version=request.policy_version,
        expires_at=_required_input_text(block, "expiresAt"),
        claim_text=_required_input_text(block, "claimText"),
        originating_tool_call_id=_optional_input_text(block, "originatingToolCallId"),
        priority=_text(block.inputs, "priority", "normal"),
        assignee_user_id=_optional_input_text(block, "assigneeUserId"),
    )


def _output_block(block: LogicBlock, state: Mapping[str, JsonObject]) -> JsonObject:
    source = block.inputs.get("fromBlock")
    if isinstance(source, str):
        output = state.get(source)
        if output is None:
            raise LogicRuntimeError("missing_output_source", f"output source block {source} has not run")
        return output
    return {"value": dict(block.inputs)}


def _terminal_output(order: Sequence[LogicBlock], state: Mapping[str, JsonObject]) -> JsonObject:
    for block in reversed(order):
        if block.block_id in state:
            return state[block.block_id]
    return {}


def _block_result(block: LogicBlock, output: JsonObject) -> LogicBlockResult:
    return LogicBlockResult(
        block_id=block.block_id,
        kind=block.kind,
        status="succeeded",
        output_hash=_hash_json(output),
        output_preview=_preview(output),
    )


def _event_record(
    ctx: RequestContext, request: LogicRunRequest, sequence: int, event_type: str, payload: JsonObject
) -> AiExecutionEventRecord:
    payload_hash = _hash_json(payload)
    return AiExecutionEventRecord(
        id=f"ai-logic-event-{request.logic_run_id}-{sequence}",
        tenant_id=ctx.tenant_id,
        ai_run_id=request.ai_run_id,
        sequence=sequence,
        event_type=event_type,
        payload_ref=f"logic://{request.logic_run_id}/{sequence}",
        payload_hash=payload_hash,
        redacted_preview=_json_block(_preview(payload))[:512],
        created_at=_now(),
    )


def _graph_hash(request: LogicRunRequest) -> str:
    return _hash_json(
        {
            "logicRunId": request.logic_run_id,
            "aiRunId": request.ai_run_id,
            "blocks": [
                {
                    "blockId": block.block_id,
                    "kind": block.kind,
                    "inputs": dict(block.inputs),
                    "dependsOn": list(block.depends_on),
                }
                for block in request.blocks
            ],
        }
    )


def _preview(value: JsonObject) -> dict[str, object]:
    return {key: _safe_preview(value[key]) for key in sorted(value)[:10]}


def _safe_preview(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _safe_preview(item) for key, item in sorted(value.items())[:10] if isinstance(key, str)}
    if isinstance(value, list | tuple):
        return [_safe_preview(item) for item in value[:10]]
    if isinstance(value, str) and len(value) > 160:
        return f"{value[:157]}..."
    return value


def _error_payload(exc: Exception) -> JsonObject:
    reason = exc.reason if isinstance(exc, LogicRuntimeError) else exc.__class__.__name__
    return {"reason": reason, "detail": scrub_error_text(str(exc))[:240]}


def _arguments_json(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return _json_block(value)
    raise LogicRuntimeError("schema_invalid", "CallFunction arguments must be a JSON object or JSON string")


def _required_input_text(block: LogicBlock, key: str) -> str:
    value = block.inputs.get(key)
    if isinstance(value, str) and value:
        return value
    raise LogicRuntimeError("schema_invalid", f"{block.block_id}.{key} must be a non-empty string")


def _optional_input_text(block: LogicBlock, key: str) -> str | None:
    value = block.inputs.get(key)
    return value if isinstance(value, str) and value else None


def _required_input_int(block: LogicBlock, key: str) -> int:
    value = block.inputs.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    raise LogicRuntimeError("schema_invalid", f"{block.block_id}.{key} must be a positive integer")


def _required_input_mapping(block: LogicBlock, key: str) -> JsonObject:
    value = block.inputs.get(key)
    if isinstance(value, Mapping):
        return dict(value)
    raise LogicRuntimeError("schema_invalid", f"{block.block_id}.{key} must be an object")


def _required_input_tuple(block: LogicBlock, key: str) -> tuple[str, ...]:
    value = block.inputs.get(key)
    if isinstance(value, list | tuple) and all(isinstance(item, str) and item for item in value):
        return tuple(value)
    raise LogicRuntimeError("schema_invalid", f"{block.block_id}.{key} must be a list of strings")


def _required_text(value: str, field_name: str) -> None:
    if not value:
        raise LogicRuntimeError("schema_invalid", f"{field_name} must be non-empty")


def _text(source: JsonObject, key: str, default: str) -> str:
    value = source.get(key)
    return value if isinstance(value, str) and value else default


def _hash_json(value: object) -> str:
    return f"sha256:{hashlib.sha256(_json_block(value).encode()).hexdigest()}"


def _json_block(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
