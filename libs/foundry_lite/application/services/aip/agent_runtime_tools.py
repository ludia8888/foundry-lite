from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from foundry_lite.application.ports.language_model import ModelMessage, ModelRequest, ModelResponse, ModelToolCall
from foundry_lite.application.services.aip.context_compiler import CompiledContext, ToolDefinition
from foundry_lite.application.services.aip.tool_broker import (
    ToolBrokerError,
    ToolBrokerRequest,
    ToolBrokerResult,
    ToolCallRequest,
    ToolSpec,
)
from foundry_lite.domain.context import RequestContext

JsonObject = Mapping[str, object]


class ToolBroker(Protocol):
    def execute(self, ctx: RequestContext, request: ToolBrokerRequest) -> ToolBrokerResult: ...


@dataclass(frozen=True)
class AgentRuntimeToolExecution:
    """One brokered model-requested tool call plus the follow-up prompt artifact."""

    result: ToolBrokerResult
    followup_messages: tuple[ModelMessage, ...]
    followup_prompt_hash: str
    followup_prompt_text: str


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


class AgentRuntimeToolRequest(Protocol):
    @property
    def model_alias(self) -> str: ...

    @property
    def output_schema(self) -> JsonObject | None: ...

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


def tool_definitions(tools: tuple[ToolSpec, ...]) -> tuple[ToolDefinition, ...]:
    return tuple(
        ToolDefinition(
            tool_id=tool.tool_id,
            version=tool.version,
            description=f"{tool.tool_id} server-side governed tool",
            input_schema=dict(tool.input_schema),
            effect=tool.effect,
            required_permission=tool.required_permission,
            confirmation_policy=tool.confirmation_policy,
        )
        for tool in tools
    )


def model_tool_names(request: AgentRuntimeToolRequest) -> tuple[str, ...]:
    allowed = set(request.agent_allowed_tools)
    return tuple(f"{tool.tool_id}@{tool.version}" for tool in request.tool_manifest if tool.tool_id in allowed)


def followup_model_request(
    request: AgentRuntimeToolRequest, ai_run_id: str, tool_execution: AgentRuntimeToolExecution
) -> ModelRequest:
    return ModelRequest(
        model_alias=request.model_alias,
        messages=tool_execution.followup_messages,
        tools=model_tool_names(request),
        response_schema=_response_schema(request.output_schema),
        max_output_tokens=request.max_output_tokens,
        request_id=request.agent_run_id,
        ai_run_id=ai_run_id,
        request_hash=tool_execution.followup_prompt_hash,
        model_call_attempt=2,
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
    if isinstance(exc, AgentRuntimeToolLoopError | ToolBrokerError):
        return {"reason": exc.reason, "detail": exc.detail}
    return None


def success_event_sequence(tool_execution: AgentRuntimeToolExecution | None) -> int:
    return 7 if tool_execution is not None else 4


def _tool_call_request(
    request: AgentRuntimeToolRequest, ai_run_id: str, call: ModelToolCall, occurred_at: str
) -> ToolCallRequest:
    tool_id, version = _resolved_tool_ref(request.tool_manifest, call.tool_name)
    return ToolCallRequest(
        tool_call_id=f"{ai_run_id}-tool-1",
        tool_id=tool_id,
        version=version,
        arguments_json=call.arguments_json,
        ai_run_id=ai_run_id,
        sequence=1,
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


def _tool_execution(compiled: CompiledContext, result: ToolBrokerResult) -> AgentRuntimeToolExecution:
    messages = (*compiled.messages, ModelMessage(role="user", content=_tool_result_message(result)))
    prompt_text = messages_prompt_text(messages)
    return AgentRuntimeToolExecution(
        result=result,
        followup_messages=messages,
        followup_prompt_hash=_hash_text(prompt_text),
        followup_prompt_text=prompt_text,
    )


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
