"""AI FDE-specific tool execution helpers kept outside the shared tool module."""

from __future__ import annotations

from foundry_lite.application.ports.language_model import ModelMessage, ModelResponse
from foundry_lite.application.services.aip.agent_runtime_tools import (
    AgentRuntimeFdeToolRequest,
    AgentRuntimeToolExecution,
    FdePlatformToolExecutor,
    _guard_tool_call_budget,
    _tool_call_request,
    _tool_execution,
    required_fde_scope_ref,
)
from foundry_lite.application.services.aip.context_compiler import CompiledContext
from foundry_lite.application.services.aip.fde_tool_result import FdePlatformToolRequest
from foundry_lite.application.services.aip.tool_broker import (
    ToolBrokerRequest,
    ToolCallRequest,
    ToolSpec,
    published_tool_spec,
    validated_tool_arguments,
)
from foundry_lite.domain.context import RequestContext


def execute_fde_tool_call(
    *,
    ctx: RequestContext,
    executor: FdePlatformToolExecutor,
    request: AgentRuntimeFdeToolRequest,
    ai_run_id: str,
    compiled: CompiledContext,
    response: ModelResponse,
    occurred_at: str,
    base_messages: tuple[ModelMessage, ...] | None = None,
    tool_sequence: int = 1,
    active_tool_ids: tuple[str, ...] | None = None,
) -> AgentRuntimeToolExecution | None:
    if not response.normalized_tool_calls:
        return None
    _guard_tool_call_budget(request, response.normalized_tool_calls)
    catalog = request.tool_catalog or request.tool_manifest
    tool_call = _tool_call_request(
        request,
        ai_run_id,
        response.normalized_tool_calls[0],
        occurred_at,
        sequence=tool_sequence,
        tool_catalog=catalog,
    )
    spec, arguments = _validate_fde_call(request, tool_call, catalog, active_tool_ids)
    result = executor.execute(
        ctx,
        _platform_request(request, ai_run_id, occurred_at, catalog, tool_call, spec, arguments),
    )
    return _tool_execution(compiled, result, base_messages=base_messages)


def _validate_fde_call(
    request: AgentRuntimeFdeToolRequest,
    tool_call: ToolCallRequest,
    catalog: tuple[ToolSpec, ...],
    active_tool_ids: tuple[str, ...] | None,
) -> tuple[ToolSpec, dict[str, object]]:
    broker_request = ToolBrokerRequest(
        tool_call=tool_call,
        tool_manifest=catalog,
        agent_allowed_tools=active_tool_ids or request.agent_allowed_tools,
        model_allowed_classifications=request.allowed_classifications or ("public", request.data_classification),
        max_tool_output_bytes=request.max_tool_output_bytes,
    )
    spec = published_tool_spec(broker_request)
    return spec, dict(validated_tool_arguments(spec, tool_call.arguments_json))


def _platform_request(
    request: AgentRuntimeFdeToolRequest,
    ai_run_id: str,
    occurred_at: str,
    catalog: tuple[ToolSpec, ...],
    tool_call: ToolCallRequest,
    spec: ToolSpec,
    arguments: dict[str, object],
) -> FdePlatformToolRequest:
    return FdePlatformToolRequest(
        tool_call_id=tool_call.tool_call_id,
        ai_run_id=ai_run_id,
        sequence=tool_call.sequence,
        mode=_fde_mode(request),
        scope_ref=required_fde_scope_ref(request),
        spec=spec,
        catalog=catalog,
        arguments=arguments,
        approved_tool_ids=request.approved_tool_ids,
        max_output_bytes=request.max_tool_output_bytes,
        occurred_at=occurred_at,
    )


def _fde_mode(request: AgentRuntimeFdeToolRequest) -> str:
    mode = request.state_json.get("mode")
    if not isinstance(mode, str) or not mode:
        raise ValueError("AI FDE runtime state is missing mode")
    return mode


def with_loop_counts(
    execution: AgentRuntimeToolExecution | None,
    model_call_count: int,
    tool_call_count: int,
) -> AgentRuntimeToolExecution | None:
    if execution is None:
        return None
    return AgentRuntimeToolExecution(
        result=execution.result,
        followup_messages=execution.followup_messages,
        followup_prompt_hash=execution.followup_prompt_hash,
        followup_prompt_text=execution.followup_prompt_text,
        model_call_count=model_call_count,
        tool_call_count=tool_call_count,
    )
