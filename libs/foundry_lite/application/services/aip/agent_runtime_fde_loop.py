"""Bounded multi-tool loop for the governed AI FDE runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from foundry_lite.application.ports.language_model import ModelMessage, ModelRequest, ModelResponse
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.aip.agent_runtime_contracts import AgentRuntimeRequest, aggregate_response
from foundry_lite.application.services.aip.agent_runtime_fde_tools import (
    FdePlatformToolExecutor,
    execute_fde_tool_call,
    with_loop_counts,
)
from foundry_lite.application.services.aip.agent_runtime_tools import (
    AgentRuntimeToolExecution,
    AgentRuntimeToolLoopError,
    followup_model_request,
)
from foundry_lite.application.services.aip.context_compiler import CompiledContext
from foundry_lite.domain.context import RequestContext

__all__ = ("FdeLoopResult", "FdePlatformToolExecutor", "run_fde_tool_loop")


@dataclass(frozen=True)
class FdeLoopResult:
    aggregated_response: ModelResponse
    final_response: ModelResponse
    last_tool_execution: AgentRuntimeToolExecution | None


def run_fde_tool_loop(
    *,
    ctx: RequestContext,
    executor: FdePlatformToolExecutor,
    request: AgentRuntimeRequest,
    ai_run_id: str,
    compiled: CompiledContext,
    first_response: ModelResponse,
    invoke_model: Callable[[ModelRequest], ModelResponse],
    record_tool: Callable[[AgentRuntimeToolExecution], None],
    record_prompt: Callable[[AgentRuntimeToolExecution, int], None],
    charged: list[ModelResponse],
) -> FdeLoopResult:
    responses = [first_response]
    response = first_response
    last_execution: AgentRuntimeToolExecution | None = None
    consumed_write_approvals: set[str] = set()
    active_tool_ids = set(request.agent_allowed_tools)
    messages = (*compiled.messages, _assistant_message(response))
    for sequence in range(1, request.max_tool_calls + 1):
        if not response.normalized_tool_calls:
            return _result(responses, response, last_execution, sequence - 1)
        _guard_write_approval_reuse(response, consumed_write_approvals)
        execution = _execute(
            ctx, executor, request, ai_run_id, compiled, response, messages, sequence, tuple(active_tool_ids)
        )
        _consume_write_approval(execution, consumed_write_approvals)
        _activate_lazy_tools(execution, active_tool_ids)
        record_tool(execution)
        record_prompt(execution, sequence + 1)
        response = _invoke_followup(request, ai_run_id, execution, sequence, active_tool_ids, invoke_model)
        charged.append(response)
        responses.append(response)
        messages = (*execution.followup_messages, _assistant_message(response))
        last_execution = execution
    if response.normalized_tool_calls:
        raise AgentRuntimeToolLoopError("tool_call_loop_limit_exceeded", "AI FDE exhausted its tool-call budget")
    return _result(responses, response, last_execution, request.max_tool_calls)


def _invoke_followup(
    request: AgentRuntimeRequest,
    ai_run_id: str,
    execution: AgentRuntimeToolExecution,
    sequence: int,
    active_tool_ids: set[str],
    invoke_model: Callable[[ModelRequest], ModelResponse],
) -> ModelResponse:
    followup = followup_model_request(
        request,
        ai_run_id,
        execution,
        sequence + 1,
        available_tools=request.tool_catalog or request.tool_manifest,
        active_tool_ids=tuple(active_tool_ids),
    )
    return invoke_model(followup)


def _execute(
    ctx: RequestContext,
    executor: FdePlatformToolExecutor,
    request: AgentRuntimeRequest,
    ai_run_id: str,
    compiled: CompiledContext,
    response: ModelResponse,
    messages: tuple[ModelMessage, ...],
    sequence: int,
    active_tool_ids: tuple[str, ...],
) -> AgentRuntimeToolExecution:
    execution = execute_fde_tool_call(
        ctx=ctx,
        executor=executor,
        request=request,
        ai_run_id=ai_run_id,
        compiled=compiled,
        response=response,
        occurred_at=_now(),
        base_messages=messages,
        tool_sequence=sequence,
        active_tool_ids=active_tool_ids,
    )
    if execution is None:
        raise AgentRuntimeToolLoopError("missing_tool_execution", "AI FDE tool response was not executable")
    return execution


def _result(
    responses: list[ModelResponse],
    final_response: ModelResponse,
    last_execution: AgentRuntimeToolExecution | None,
    tool_count: int,
) -> FdeLoopResult:
    execution = with_loop_counts(last_execution, len(responses), tool_count)
    return FdeLoopResult(_aggregate(responses), final_response, execution)


def _aggregate(responses: list[ModelResponse]) -> ModelResponse:
    aggregated = responses[0]
    for response in responses[1:]:
        aggregated = aggregate_response(aggregated, response)
    return aggregated


def _assistant_message(response: ModelResponse) -> ModelMessage:
    return ModelMessage(role="assistant", content=response.content or "AI FDE requested a governed tool call.")


def _guard_write_approval_reuse(response: ModelResponse, consumed: set[str]) -> None:
    tool_id = response.normalized_tool_calls[0].tool_name.split("@", maxsplit=1)[0]
    if tool_id in consumed:
        raise AgentRuntimeToolLoopError(
            "tool_approval_consumed",
            f"explicit approval for {tool_id} can authorize only one mutating call per AI FDE request",
        )


def _consume_write_approval(execution: AgentRuntimeToolExecution, consumed: set[str]) -> None:
    if execution.result.ledger_record.effect != "READ":
        consumed.add(execution.result.ledger_record.tool_id)


def _activate_lazy_tools(execution: AgentRuntimeToolExecution, active: set[str]) -> None:
    if execution.result.ledger_record.tool_id != "fde.tools.search":
        return
    value = execution.result.output_json.get("activatedToolIds")
    if isinstance(value, list):
        active.update(item for item in value if isinstance(item, str))
