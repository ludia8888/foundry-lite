from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from foundry_lite.application.services.aip.agent_runtime_citations import (
    AgentRuntimeAnswer,
    citation_error_payload,
)
from foundry_lite.application.services.aip.agent_runtime_ledger import operations_ref
from foundry_lite.application.services.aip.agent_runtime_ports import (
    ContextCompilationError,
    ContextRetrievalError,
    ContextRetrievalRequest,
    ModelRequest,
    ModelResponse,
    RetrievedContextItem,
)
from foundry_lite.application.services.aip.agent_runtime_tools import (
    AgentRuntimeActionProposalExecution,
    AgentRuntimeToolExecution,
    ToolSpec,
    messages_prompt_text,
    model_tool_names,
    tool_definitions,
    tool_error_payload,
)
from foundry_lite.application.services.aip.context_compiler import (
    CompiledContext,
    ContextCompileRequest,
)
from foundry_lite.application.services.aip.retrieval_orchestrator import (
    RetrievalContentSearch,
    RetrievalObjectQuery,
    RetrievalOrchestrator,
)
from foundry_lite.domain.context import RequestContext

JsonObject = Mapping[str, object]


class AgentRuntimeError(Exception):
    """Raised when the Agent Runtime must fail closed."""


@dataclass(frozen=True)
class AgentRuntimeRequest:
    """One bounded Agent Runtime turn."""

    agent_run_id: str
    agent_version_id: str
    model_alias: str
    prompt_version_id: str
    user_message: str
    agent_instruction: str
    security_partition: str
    allowed_security_partitions: tuple[str, ...]
    state_json: JsonObject
    output_schema: JsonObject | None = None
    ai_run_id: str | None = None
    session_id: str | None = None
    ontology_version_id: str = "active-ontology"
    data_classification: str = "internal"
    allowed_classifications: tuple[str, ...] | None = None
    region_requirement: str | None = None
    max_context_items: int = 4
    max_context_tokens: int = 1200
    max_model_calls: int = 1
    max_loop_iterations: int = 1
    max_tool_calls: int = 0
    max_tool_output_bytes: int = 4096
    max_output_tokens: int = 512
    policy_version: str = "policy-v1"
    tool_manifest: tuple[ToolSpec, ...] = ()
    agent_allowed_tools: tuple[str, ...] = ()
    agent_allowed_actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentRuntimeResult:
    """Public result for a bounded Agent Runtime run."""

    agent_run_id: str
    ai_run_id: str | None
    session_id: str | None
    run_status: str
    answer: str | None
    context_ids: tuple[str, ...]
    citations: tuple[JsonObject, ...] = ()
    usage: JsonObject | None = None
    error: JsonObject | None = None

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "agentRunId": self.agent_run_id,
            "aiRunId": self.ai_run_id,
            "sessionId": self.session_id,
            "runStatus": self.run_status,
            "answer": self.answer,
            "contextIds": list(self.context_ids),
            "citations": [dict(citation) for citation in self.citations],
            "operations": operations_ref(self.ai_run_id),
        }
        if self.usage is not None:
            payload["usage"] = dict(self.usage)
        if self.error is not None:
            payload["error"] = dict(self.error)
        return payload


def validate_request(ctx: RequestContext, request: AgentRuntimeRequest) -> None:
    if not request.agent_run_id or not request.user_message or not request.model_alias:
        raise AgentRuntimeError("invalid_request", "agent_run_id, user_message, and model_alias are required")
    if (request.max_model_calls, request.max_loop_iterations) not in {(1, 1), (2, 2)}:
        raise AgentRuntimeError("unsupported_budget", "agent runtime supports one model turn or one tool loop")
    if request.max_tool_calls not in (0, 1):
        raise AgentRuntimeError("unsupported_budget", "agent runtime supports at most one tool call in this slice")
    if request.max_tool_calls and request.max_model_calls < 2:
        raise AgentRuntimeError("unsupported_budget", "tool loops require two model calls in this slice")
    if request.security_partition not in request.allowed_security_partitions:
        raise AgentRuntimeError("security_partition_mismatch", "security partition must be explicitly allowlisted")
    if not request.security_partition.startswith(f"{ctx.tenant_id}:"):
        raise AgentRuntimeError("security_partition_mismatch", "security partition is outside the tenant boundary")


def retrieval_request(ctx: RequestContext, request: AgentRuntimeRequest) -> ContextRetrievalRequest:
    return ContextRetrievalRequest(
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.actor_user_id,
        query=request.user_message,
        agent_version_id=request.agent_version_id,
        ontology_version_id=request.ontology_version_id,
        max_context_items=request.max_context_items,
        max_context_tokens=request.max_context_tokens,
        security_partition=request.security_partition,
        state_json=request.state_json,
        allowed_classifications=request.allowed_classifications,
    )


def retrieve_runtime_context(
    ctx: RequestContext,
    object_query_service: RetrievalObjectQuery,
    content_retrieval_service: RetrievalContentSearch,
    request: AgentRuntimeRequest,
) -> tuple[RetrievedContextItem, ...]:
    items = RetrievalOrchestrator(
        object_query_service,
        content_retrieval_service=content_retrieval_service,
    ).retrieve_context(ctx=ctx, request=retrieval_request(ctx, request))
    guard_context_budget(request, items)
    return items


def compile_request(
    request: AgentRuntimeRequest, context_items: tuple[RetrievedContextItem, ...]
) -> ContextCompileRequest:
    return ContextCompileRequest(
        agent_instruction=request.agent_instruction,
        user_message=request.user_message,
        state_json=dict(request.state_json),
        retrieved_context=context_items,
        tool_definitions=tool_definitions(request.tool_manifest),
        allowed_security_partitions=request.allowed_security_partitions,
        output_schema=dict(request.output_schema or {}),
    )


def model_request(request: AgentRuntimeRequest, ai_run_id: str, compiled: CompiledContext) -> ModelRequest:
    return ModelRequest(
        model_alias=request.model_alias,
        messages=compiled.messages,
        tools=model_tool_names(request),
        response_schema=_response_schema(request.output_schema),
        max_output_tokens=request.max_output_tokens,
        request_id=request.agent_run_id,
        ai_run_id=ai_run_id,
        request_hash=compiled.compiled_prompt_hash,
        model_call_attempt=1,
        data_classification=request.data_classification,
        region_requirement=request.region_requirement,
    )


def compiled_prompt_text(compiled: CompiledContext) -> str:
    return messages_prompt_text(compiled.messages)


def guard_context_budget(request: AgentRuntimeRequest, items: tuple[RetrievedContextItem, ...]) -> None:
    token_total = sum(item.token_estimate for item in items)
    if len(items) > request.max_context_items:
        raise AgentRuntimeError("context_item_budget_exceeded", "retrieved context exceeded max_context_items")
    if token_total > request.max_context_tokens:
        raise AgentRuntimeError("context_token_budget_exceeded", "retrieved context exceeded max_context_tokens")


def usage_payload(
    response: ModelResponse,
    context_items: tuple[RetrievedContextItem, ...],
    tool_execution: AgentRuntimeToolExecution | None = None,
    action_proposal_execution: AgentRuntimeActionProposalExecution | None = None,
) -> dict[str, object]:
    has_tool_call = tool_execution is not None or action_proposal_execution is not None
    payload: dict[str, object] = {
        "inputTokens": response.input_tokens,
        "outputTokens": response.output_tokens,
        "modelCallCount": 2 if tool_execution is not None else 1,
        "toolCallCount": 1 if has_tool_call else 0,
        "contextItemCount": len(context_items),
        "finishReason": response.finish_reason,
    }
    if action_proposal_execution is not None:
        payload["actionProposalCount"] = 1
    return payload


def aggregate_response(first: ModelResponse, final: ModelResponse) -> ModelResponse:
    if first is final:
        return final
    return ModelResponse(
        provider=final.provider,
        resolved_model_id=final.resolved_model_id,
        resolved_model_revision=final.resolved_model_revision,
        content=final.content,
        finish_reason=final.finish_reason,
        input_tokens=first.input_tokens + final.input_tokens,
        output_tokens=first.output_tokens + final.output_tokens,
        normalized_tool_calls=(),
        provider_request_id=final.provider_request_id,
        latency_ms=first.latency_ms + final.latency_ms,
        model_hash=final.model_hash,
        prompt_hash=final.prompt_hash,
    )


def error_payload(exc: Exception) -> dict[str, object]:
    if isinstance(exc, AgentRuntimeError):
        return {"reason": _exception_arg(exc, 0), "detail": _exception_arg(exc, 1)}
    if isinstance(exc, ContextCompilationError):
        return {"reason": exc.reason, "detail": exc.detail}
    if isinstance(exc, ContextRetrievalError):
        return {"reason": exc.reason, "detail": exc.detail}
    tool_payload = tool_error_payload(exc)
    if tool_payload is not None:
        return tool_payload
    citation_payload = citation_error_payload(exc)
    if citation_payload is not None:
        return citation_payload
    adapter_payload = _adapter_error_payload(exc)
    if adapter_payload is not None:
        return adapter_payload
    return {"reason": exc.__class__.__name__, "detail": str(exc)[:240]}


def success_result(
    request: AgentRuntimeRequest,
    ai_run_id: str,
    session_id: str,
    context_items: tuple[RetrievedContextItem, ...],
    response: ModelResponse,
    answer: AgentRuntimeAnswer,
    tool_execution: AgentRuntimeToolExecution | None = None,
    action_proposal_execution: AgentRuntimeActionProposalExecution | None = None,
) -> AgentRuntimeResult:
    return AgentRuntimeResult(
        agent_run_id=request.agent_run_id,
        ai_run_id=ai_run_id,
        session_id=session_id,
        run_status="succeeded",
        answer=answer.answer,
        context_ids=tuple(item.context_id for item in context_items),
        usage=usage_payload(response, context_items, tool_execution, action_proposal_execution),
        citations=answer.citations,
    )


def failed_result(
    request: AgentRuntimeRequest, ai_run_id: str | None, session_id: str | None, exc: Exception
) -> AgentRuntimeResult:
    return AgentRuntimeResult(
        agent_run_id=request.agent_run_id,
        ai_run_id=ai_run_id,
        session_id=session_id,
        run_status="failed",
        answer=None,
        context_ids=(),
        error=error_payload(exc),
    )


def _response_schema(output_schema: JsonObject | None) -> str | None:
    if not output_schema:
        return None
    return json.dumps(output_schema, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _adapter_error_payload(exc: Exception) -> dict[str, object] | None:
    failure = getattr(exc, "failure", None)
    if exc.__class__.__name__ != "AdapterError" or failure is None:
        return None
    details = getattr(failure, "details", {})
    reason = exc.__class__.__name__
    if isinstance(details, Mapping):
        reason = str(details.get("reason", getattr(failure, "kind", reason)))
    return {"reason": str(reason), "detail": str(getattr(failure, "operator_message", str(exc)))}


def _exception_arg(exc: Exception, index: int) -> str:
    return str(exc.args[index]) if len(exc.args) > index else exc.__class__.__name__
