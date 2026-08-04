"""Application service helpers for agent runtime tools workflows."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from foundry_lite.application.ports.ai_run_repository import AiToolCallRecord
from foundry_lite.application.ports.language_model import ModelMessage, ModelRequest, ModelResponse, ModelToolCall
from foundry_lite.application.services.aip.action_proposal import (
    ActionProposalError,
    ActionProposalRequest,
    ActionProposalResult,
)
from foundry_lite.application.services.aip.context_compiler import CompiledContext, ToolDefinition
from foundry_lite.application.services.aip.fde_ontology_tools import FdeOntologyToolError
from foundry_lite.application.services.aip.fde_tool_result import FdePlatformToolError, FdePlatformToolRequest
from foundry_lite.application.services.aip.tool_broker import (
    ToolBrokerError,
    ToolBrokerRequest,
    ToolBrokerResult,
    ToolCallRequest,
    ToolSpec,
    is_direct_vendor_tool_id,
    published_tool_spec,
    validated_tool_arguments,
)
from foundry_lite.domain.context import RequestContext


class ToolBroker(Protocol):
    def execute(self, ctx: RequestContext, request: ToolBrokerRequest) -> ToolBrokerResult: ...


class ActionProposalCreator(Protocol):
    def propose(self, ctx: RequestContext, request: ActionProposalRequest) -> ActionProposalResult: ...


class FdePlatformToolExecutor(Protocol):
    def execute(self, ctx: RequestContext, request: FdePlatformToolRequest) -> ToolBrokerResult: ...


@dataclass(frozen=True)
class AgentRuntimeToolExecution:
    """One brokered model-requested tool call plus the follow-up prompt artifact."""

    result: ToolBrokerResult
    followup_messages: tuple[ModelMessage, ...]
    followup_prompt_hash: str
    followup_prompt_text: str
    model_call_count: int = 2
    tool_call_count: int = 1


@dataclass(frozen=True)
class AgentRuntimeActionProposalExecution:
    """One model-requested action proposal routed to human review."""

    proposal: ActionProposalResult
    ledger_record: AiToolCallRecord
    answer: str


@dataclass
class AgentRuntimeToolLoopError(Exception):
    reason: str
    detail: str

    def __post_init__(self) -> None:
        Exception.__init__(self, self.detail)


def execute_model_tool_call(
    *,
    ctx: RequestContext,
    broker: ToolBroker,
    request: AgentRuntimeToolRequest,
    ai_run_id: str,
    compiled: CompiledContext,
    response: ModelResponse,
    occurred_at: str,
) -> AgentRuntimeToolExecution | None:
    if not response.normalized_tool_calls:
        return None
    _guard_tool_call_budget(request, response.normalized_tool_calls)
    tool_call = _tool_call_request(request, ai_run_id, response.normalized_tool_calls[0], occurred_at)
    result = broker.execute(ctx, _tool_broker_request(request, tool_call))
    return _tool_execution(compiled, result)


def execute_action_proposal_tool_call(
    *,
    ctx: RequestContext,
    proposer: ActionProposalCreator,
    request: AgentRuntimeActionProposalRequest,
    ai_run_id: str,
    response: ModelResponse,
    occurred_at: str,
) -> AgentRuntimeActionProposalExecution | None:
    if not response.normalized_tool_calls:
        return None
    _guard_tool_call_budget(request, response.normalized_tool_calls)
    tool_call = _tool_call_request(request, ai_run_id, response.normalized_tool_calls[0], occurred_at)
    broker_request = _tool_broker_request(request, tool_call)
    spec = published_tool_spec(broker_request)
    if spec.effect == "READ":
        return None
    _guard_action_proposal_spec(spec)
    arguments = validated_tool_arguments(spec, tool_call.arguments_json)
    proposal = proposer.propose(ctx, _action_proposal_request(request, ai_run_id, tool_call, arguments))
    return _action_proposal_execution(ctx, request, tool_call, spec, arguments, proposal, occurred_at)


class AgentRuntimeToolRequest(Protocol):
    @property
    def model_alias(self) -> str: ...

    @property
    def environment(self) -> str: ...

    @property
    def output_schema(self) -> Mapping[str, object] | None: ...

    @property
    def max_output_tokens(self) -> int: ...

    @property
    def agent_run_id(self) -> str: ...

    @property
    def data_classification(self) -> str: ...

    @property
    def region_requirement(self) -> str | None: ...

    @property
    def max_tool_calls(self) -> int: ...

    @property
    def max_tool_output_bytes(self) -> int: ...

    @property
    def tool_manifest(self) -> tuple[ToolSpec, ...]: ...

    @property
    def agent_allowed_tools(self) -> tuple[str, ...]: ...

    @property
    def allowed_classifications(self) -> tuple[str, ...] | None: ...


class AgentRuntimeActionProposalRequest(AgentRuntimeToolRequest, Protocol):
    @property
    def agent_allowed_actions(self) -> tuple[str, ...]: ...

    @property
    def policy_version(self) -> str: ...


class AgentRuntimeFdeToolRequest(AgentRuntimeToolRequest, Protocol):
    @property
    def branch_id(self) -> str | None: ...

    @property
    def fde_scope_ref(self) -> str | None: ...

    @property
    def tool_catalog(self) -> tuple[ToolSpec, ...]: ...

    @property
    def approved_tool_ids(self) -> tuple[str, ...]: ...

    @property
    def state_json(self) -> Mapping[str, object]: ...


def tool_definitions(tools: tuple[ToolSpec, ...]) -> tuple[ToolDefinition, ...]:
    return tuple(
        ToolDefinition(
            tool_id=tool.tool_id,
            version=tool.version,
            description=tool.description or f"{tool.tool_id} server-side governed tool",
            input_schema=dict(tool.input_schema),
            effect=tool.effect,
            required_permission=tool.required_permission,
            confirmation_policy=tool.confirmation_policy,
        )
        for tool in tools
        if not is_direct_vendor_tool_id(tool.tool_id)
    )


def model_tool_names(request: AgentRuntimeToolRequest) -> tuple[str, ...]:
    return _model_tool_names(request.tool_manifest, request.agent_allowed_tools)


def _model_tool_names(tools: tuple[ToolSpec, ...], allowed_tool_ids: tuple[str, ...]) -> tuple[str, ...]:
    allowed = set(allowed_tool_ids)
    return tuple(
        f"{tool.tool_id}@{tool.version}"
        for tool in tools
        if tool.tool_id in allowed and not is_direct_vendor_tool_id(tool.tool_id)
    )


def followup_model_request(
    request: AgentRuntimeToolRequest,
    ai_run_id: str,
    tool_execution: AgentRuntimeToolExecution,
    model_call_attempt: int = 2,
    available_tools: tuple[ToolSpec, ...] | None = None,
    active_tool_ids: tuple[str, ...] | None = None,
) -> ModelRequest:
    return ModelRequest(
        model_alias=request.model_alias,
        messages=tool_execution.followup_messages,
        environment=request.environment,
        tools=_model_tool_names(
            available_tools or request.tool_manifest,
            active_tool_ids or request.agent_allowed_tools,
        ),
        response_schema=_response_schema(request.output_schema),
        max_output_tokens=request.max_output_tokens,
        request_id=request.agent_run_id,
        ai_run_id=ai_run_id,
        request_hash=tool_execution.followup_prompt_hash,
        model_call_attempt=model_call_attempt,
        data_classification=request.data_classification,
        region_requirement=request.region_requirement,
    )


def messages_prompt_text(messages: tuple[ModelMessage, ...]) -> str:
    return json.dumps(
        {"messages": [{"role": message.role, "content": message.content} for message in messages]},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def guard_final_response(response: ModelResponse) -> None:
    if response.normalized_tool_calls:
        raise AgentRuntimeToolLoopError(
            "tool_call_loop_limit_exceeded",
            "follow-up model call returned another tool call",
        )


def tool_error_payload(exc: Exception) -> dict[str, object] | None:
    if isinstance(
        exc,
        AgentRuntimeToolLoopError | ToolBrokerError | ActionProposalError | FdeOntologyToolError | FdePlatformToolError,
    ):
        return {"reason": exc.reason, "detail": exc.detail}
    return None


def success_event_sequence(
    tool_execution: AgentRuntimeToolExecution | None,
    action_proposal_execution: AgentRuntimeActionProposalExecution | None = None,
) -> int:
    if tool_execution is not None:
        return 4 + (tool_execution.tool_call_count * 3)
    if action_proposal_execution is not None:
        return 6
    return 4


def _tool_call_request(
    request: AgentRuntimeToolRequest,
    ai_run_id: str,
    call: ModelToolCall,
    occurred_at: str,
    sequence: int = 1,
    tool_catalog: tuple[ToolSpec, ...] | None = None,
) -> ToolCallRequest:
    tool_id, version = _resolved_tool_ref(tool_catalog or request.tool_manifest, call.tool_name)
    return ToolCallRequest(
        tool_call_id=f"{ai_run_id}-tool-{sequence}",
        tool_id=tool_id,
        version=version,
        arguments_json=call.arguments_json,
        ai_run_id=ai_run_id,
        sequence=sequence,
        request_id=request.agent_run_id,
        occurred_at=occurred_at,
    )


def _resolved_tool_ref(tools: tuple[ToolSpec, ...], tool_name: str) -> tuple[str, str]:
    if "@" in tool_name:
        tool_id, version = tool_name.split("@", maxsplit=1)
        return tool_id, version
    matches = tuple(tool for tool in tools if tool.tool_id == tool_name)
    if len(matches) == 1:
        return matches[0].tool_id, matches[0].version
    raise AgentRuntimeToolLoopError(
        "tool_not_in_agent_manifest", f"tool {tool_name} is not uniquely in the agent manifest"
    )


def _tool_broker_request(request: AgentRuntimeToolRequest, tool_call: ToolCallRequest) -> ToolBrokerRequest:
    return ToolBrokerRequest(
        tool_call=tool_call,
        tool_manifest=request.tool_manifest,
        agent_allowed_tools=request.agent_allowed_tools,
        model_allowed_classifications=_tool_allowed_classifications(request),
        max_tool_output_bytes=request.max_tool_output_bytes,
    )


def _tool_allowed_classifications(request: AgentRuntimeToolRequest) -> tuple[str, ...]:
    return request.allowed_classifications or ("public", request.data_classification)


def _required_fde_branch_id(request: AgentRuntimeFdeToolRequest) -> str:
    if not request.branch_id:
        raise AgentRuntimeToolLoopError("fde_branch_required", "AI FDE tool calls require an ontology branch")
    return request.branch_id


def required_fde_scope_ref(request: AgentRuntimeFdeToolRequest) -> str:
    if request.fde_scope_ref:
        return request.fde_scope_ref
    if request.branch_id:
        return f"ontology-branch:{request.branch_id}"
    raise AgentRuntimeToolLoopError("fde_scope_required", "AI FDE tool calls require a governed workspace scope")


def _tool_execution(
    compiled: CompiledContext,
    result: ToolBrokerResult,
    *,
    base_messages: tuple[ModelMessage, ...] | None = None,
) -> AgentRuntimeToolExecution:
    messages = (*(base_messages or compiled.messages), ModelMessage(role="user", content=_tool_result_message(result)))
    prompt_text = messages_prompt_text(messages)
    return AgentRuntimeToolExecution(
        result=result,
        followup_messages=messages,
        followup_prompt_hash=_hash_text(prompt_text),
        followup_prompt_text=prompt_text,
    )


def _guard_action_proposal_spec(spec: ToolSpec) -> None:
    if spec.effect == "WRITE":
        raise AgentRuntimeToolLoopError("direct_write_tool_denied", "direct WRITE tools must use action proposals")
    if spec.effect != "PROPOSE_WRITE":
        raise AgentRuntimeToolLoopError("unsupported_tool_effect", f"tool effect {spec.effect} is not supported")
    if spec.tool_id != "action.propose":
        raise AgentRuntimeToolLoopError("unsupported_action_proposal_tool", "only action.propose is supported")
    if spec.confirmation_policy != "HUMAN_REVIEW":
        raise AgentRuntimeToolLoopError("proposal_requires_human_review", "action.propose requires HUMAN_REVIEW")


def _action_proposal_request(
    request: AgentRuntimeActionProposalRequest,
    ai_run_id: str,
    tool_call: ToolCallRequest,
    arguments: Mapping[str, object],
) -> ActionProposalRequest:
    return ActionProposalRequest(
        originating_ai_run_id=ai_run_id,
        action_type=_text_arg(arguments, "actionType", "action_type"),
        target_object_type=_text_arg(arguments, "targetObjectType", "target_object_type"),
        target_object_id=_text_arg(arguments, "targetObjectId", "target_object_id"),
        expected_object_version=_int_arg(arguments, "expectedObjectVersion", "expected_object_version"),
        parameters=_mapping_arg(arguments, "parameters"),
        evidence_context_ids=_text_tuple_arg(arguments, "evidenceContextIds", "evidence_context_ids"),
        agent_allowed_actions=request.agent_allowed_actions,
        policy_version=request.policy_version,
        expires_at=_text_arg(arguments, "expiresAt", "expires_at"),
        claim_text=_text_arg(arguments, "claimText", "claim_text"),
        originating_tool_call_id=tool_call.tool_call_id,
        priority=_optional_text_arg(arguments, "priority") or "normal",
        assignee_user_id=_optional_text_arg(arguments, "assigneeUserId", "assignee_user_id"),
    )


def _action_proposal_execution(
    ctx: RequestContext,
    request: AgentRuntimeActionProposalRequest,
    tool_call: ToolCallRequest,
    spec: ToolSpec,
    arguments: Mapping[str, object],
    proposal: ActionProposalResult,
    occurred_at: str,
) -> AgentRuntimeActionProposalExecution:
    ledger = AiToolCallRecord(
        id=tool_call.tool_call_id,
        tenant_id=ctx.tenant_id,
        ai_run_id=tool_call.ai_run_id,
        sequence=tool_call.sequence,
        tool_id=spec.tool_id,
        tool_version=spec.version,
        arguments_hash=_hash_json(arguments),
        effect=spec.effect,
        authorization_decision="pending_human_review",
        confirmation_policy=spec.confirmation_policy,
        status="pending_review",
        result_hash=proposal.proposal_fingerprint,
        linked_action_run_id=None,
        started_at=occurred_at,
        completed_at=occurred_at,
        error_json=None,
    )
    return AgentRuntimeActionProposalExecution(
        proposal=proposal,
        ledger_record=ledger,
        answer=(
            f"Action proposal {proposal.proposal_id} is awaiting human review for agent run {request.agent_run_id}."
        ),
    )


def _text_arg(arguments: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = arguments.get(key)
        if isinstance(value, str) and value:
            return value
    raise AgentRuntimeToolLoopError("invalid_action_proposal_arguments", f"{keys[0]} is required")


def _optional_text_arg(arguments: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = arguments.get(key)
        if value is None:
            continue
        if isinstance(value, str) and value:
            return value
        raise AgentRuntimeToolLoopError("invalid_action_proposal_arguments", f"{key} must be a non-empty string")
    return None


def _int_arg(arguments: Mapping[str, object], *keys: str) -> int:
    for key in keys:
        value = arguments.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    raise AgentRuntimeToolLoopError("invalid_action_proposal_arguments", f"{keys[0]} must be an integer")


def _mapping_arg(arguments: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = arguments.get(key)
    if isinstance(value, Mapping):
        return value
    raise AgentRuntimeToolLoopError("invalid_action_proposal_arguments", f"{key} must be an object")


def _text_tuple_arg(arguments: Mapping[str, object], *keys: str) -> tuple[str, ...]:
    for key in keys:
        value = arguments.get(key)
        if isinstance(value, list | tuple) and all(isinstance(item, str) and item for item in value):
            return tuple(value)
    raise AgentRuntimeToolLoopError("invalid_action_proposal_arguments", f"{keys[0]} must be a string list")


def _tool_result_message(result: ToolBrokerResult) -> str:
    return "## brokered_tool_result\n" + json.dumps(
        {
            "toolCallId": result.tool_call_id,
            "status": result.status,
            "authorizationDecision": result.authorization_decision,
            "argumentsHash": result.arguments_hash,
            "resultHash": result.result_hash,
            "output": dict(result.output_json),
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _guard_tool_call_budget(request: AgentRuntimeToolRequest, tool_calls: tuple[ModelToolCall, ...]) -> None:
    if request.max_tool_calls == 0:
        raise AgentRuntimeToolLoopError("tool_calls_not_supported_in_readonly_runtime", "model returned tool calls")
    if len(tool_calls) > request.max_tool_calls:
        raise AgentRuntimeToolLoopError(
            "tool_call_budget_exceeded", "model returned more tool calls than this run allows"
        )


def _response_schema(output_schema: object) -> str | None:
    if not output_schema:
        return None
    return json.dumps(output_schema, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _hash_text(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _hash_json(value: object) -> str:
    return _hash_text(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
