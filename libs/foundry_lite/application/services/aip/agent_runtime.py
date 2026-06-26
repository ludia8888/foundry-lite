"""AIP Agent Runtime read-only loop (P0n, §8.5/§8.6/§8.9/§10.2).

This slice is the first product path that retrieves authorized context,
compiles the governed prompt, calls the Model Gateway, and writes the AI run
ledger that Operations can inspect. It is deliberately read-only: model tool
calls are recorded as unsupported instead of executed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from foundry_lite.application.ports.context_provider import (
    ContextCompilationError,
    ContextRetrievalError,
    ContextRetrievalRequest,
    RetrievedContextItem,
)
from foundry_lite.application.ports.language_model import ModelRequest, ModelResponse
from foundry_lite.application.ports.transaction_context import AI_RUN_FAILED, AI_RUN_SUCCEEDED
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.aip.agent_runtime_ledger import (
    append_events,
    event_record,
    message_record,
    model_call,
    operations_ref,
    record_context_items,
    run_record,
    session_record,
    usage_record,
)
from foundry_lite.application.services.aip.context_compiler import (
    CompiledContext,
    ContextCompileRequest,
    ContextCompilerService,
)
from foundry_lite.application.services.aip.model_gateway import ModelGatewayService, ModelResolution
from foundry_lite.application.services.aip.retrieval_orchestrator import (
    RetrievalContentSearch,
    RetrievalObjectQuery,
    RetrievalOrchestrator,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext

JsonObject = Mapping[str, object]


class AgentRuntimeError(Exception):
    """Raised when the read-only Agent Runtime must fail closed."""


@dataclass(frozen=True)
class AgentRuntimeRequest:
    """One bounded, read-only Agent Runtime turn."""

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
    max_output_tokens: int = 512
    policy_version: str = "policy-v1"


@dataclass(frozen=True)
class AgentRuntimeResult:
    """Public result for a read-only Agent Runtime run."""

    agent_run_id: str
    ai_run_id: str | None
    session_id: str | None
    run_status: str
    answer: str | None
    context_ids: tuple[str, ...]
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
            "operations": operations_ref(self.ai_run_id),
        }
        if self.usage is not None:
            payload["usage"] = dict(self.usage)
        if self.error is not None:
            payload["error"] = dict(self.error)
        return payload


class AgentRuntimeService(CoreService):
    """Retrieve context, compile prompt, call model gateway, and write ledger evidence."""

    required_dependencies = ("engine", "ai_run_repository")
    required_collaborators = (
        "context_compiler_service",
        "model_gateway_service",
        "object_query_service",
        "content_retrieval_service",
    )
    context_compiler_service: ContextCompilerService
    model_gateway_service: ModelGatewayService
    object_query_service: RetrievalObjectQuery
    content_retrieval_service: RetrievalContentSearch

    def run(self, ctx: RequestContext, request: AgentRuntimeRequest) -> AgentRuntimeResult:
        ai_run_id: str | None = None
        session_id: str | None = None
        seeded = False
        try:
            _validate_request(ctx, request)
            ai_run_id = request.ai_run_id or f"aip-agent-run-{_new_id('agent')}"
            session_id = request.session_id or f"aip-agent-session-{request.agent_run_id}"
            context_items = self._retrieve_context(ctx, request)
            compiled = self.context_compiler_service.compile(ctx, _compile_request(request, context_items))
            resolution = self.model_gateway_service.resolve_model(ctx, request.model_alias)
            self._seed_ledger(ctx, request, ai_run_id, session_id, context_items, compiled, resolution)
            seeded = True
            response = self.model_gateway_service.invoke(ctx, _model_request(request, ai_run_id, compiled))
            _guard_readonly_response(response)
        except Exception as exc:
            if seeded and ai_run_id is not None:
                self._fail_seeded_run(ctx, ai_run_id, exc)
            return _failed_result(request, ai_run_id if seeded else None, session_id if seeded else None, exc)
        assert ai_run_id is not None
        assert session_id is not None
        self._finish_success(ctx, request, ai_run_id, session_id, context_items, compiled, response)
        return _success_result(request, ai_run_id, session_id, context_items, response)

    def _retrieve_context(self, ctx: RequestContext, request: AgentRuntimeRequest) -> tuple[RetrievedContextItem, ...]:
        items = RetrievalOrchestrator(
            self.object_query_service,
            content_retrieval_service=self.content_retrieval_service,
        ).retrieve_context(ctx=ctx, request=_retrieval_request(ctx, request))
        _guard_context_budget(request, items)
        return items

    def _seed_ledger(
        self,
        ctx: RequestContext,
        request: AgentRuntimeRequest,
        ai_run_id: str,
        session_id: str,
        context_items: tuple[RetrievedContextItem, ...],
        compiled: CompiledContext,
        resolution: ModelResolution,
    ) -> None:
        now = _now()
        with self.engine.begin() as transaction:
            self.ai_run_repository.create_session(
                transaction=transaction,
                record=session_record(ctx, request, session_id, now),
            )
            self.ai_run_repository.insert_message_or_get_existing(
                transaction=transaction,
                record=message_record(ctx, request, session_id, request.user_message, "user", now),
            )
            self.ai_run_repository.create_execution_run(
                transaction=transaction,
                record=run_record(ctx, request, ai_run_id, session_id, compiled, resolution, now),
            )
            record_context_items(
                self.ai_run_repository,
                transaction,
                ctx,
                ai_run_id,
                context_items,
            )
            append_events(
                self.ai_run_repository,
                transaction,
                ctx,
                request,
                ai_run_id,
                ("received", "retrieving_context", "model_running"),
                now,
            )

    def _finish_success(
        self,
        ctx: RequestContext,
        request: AgentRuntimeRequest,
        ai_run_id: str,
        session_id: str,
        context_items: tuple[RetrievedContextItem, ...],
        compiled: CompiledContext,
        response: ModelResponse,
    ) -> None:
        usage = _usage_payload(response, context_items)
        now = _now()
        with self.engine.begin() as transaction:
            self.ai_run_repository.record_model_call(
                transaction=transaction,
                record=model_call(ctx, ai_run_id, compiled, response),
            )
            self.ai_run_repository.record_usage(
                transaction=transaction,
                record=usage_record(ctx, ai_run_id, response, now),
            )
            self.ai_run_repository.insert_message_or_get_existing(
                transaction=transaction,
                record=message_record(ctx, request, session_id, response.content, "assistant", now),
            )
            append_events(self.ai_run_repository, transaction, ctx, request, ai_run_id, ("succeeded",), now, start=4)
            self.ai_run_repository.update_execution_run_status(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                ai_run_id=ai_run_id,
                transition=AI_RUN_SUCCEEDED,
                usage_json=usage,
                error_json=None,
                completed_at=now,
            )

    def _fail_seeded_run(self, ctx: RequestContext, ai_run_id: str, exc: Exception) -> None:
        try:
            with self.engine.begin() as transaction:
                if (
                    self.ai_run_repository.execution_run_by_id(
                        transaction=transaction, tenant_id=ctx.tenant_id, ai_run_id=ai_run_id
                    )
                    is None
                ):
                    return
                self.ai_run_repository.append_execution_event(
                    transaction=transaction,
                    record=event_record(ctx, ai_run_id, 99, "failed", _error_payload(exc), _now()),
                )
                self.ai_run_repository.update_execution_run_status(
                    transaction=transaction,
                    tenant_id=ctx.tenant_id,
                    ai_run_id=ai_run_id,
                    transition=AI_RUN_FAILED,
                    usage_json=None,
                    error_json=_error_payload(exc),
                    completed_at=_now(),
                )
        except Exception:
            return


def _validate_request(ctx: RequestContext, request: AgentRuntimeRequest) -> None:
    if not request.agent_run_id or not request.user_message or not request.model_alias:
        raise AgentRuntimeError("invalid_request", "agent_run_id, user_message, and model_alias are required")
    if request.max_model_calls != 1 or request.max_loop_iterations != 1:
        raise AgentRuntimeError("unsupported_budget", "read-only runtime supports exactly one model call and loop")
    if request.security_partition not in request.allowed_security_partitions:
        raise AgentRuntimeError("security_partition_mismatch", "security partition must be explicitly allowlisted")
    if not request.security_partition.startswith(f"{ctx.tenant_id}:"):
        raise AgentRuntimeError("security_partition_mismatch", "security partition is outside the tenant boundary")


def _retrieval_request(ctx: RequestContext, request: AgentRuntimeRequest) -> ContextRetrievalRequest:
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


def _compile_request(
    request: AgentRuntimeRequest, context_items: tuple[RetrievedContextItem, ...]
) -> ContextCompileRequest:
    return ContextCompileRequest(
        agent_instruction=request.agent_instruction,
        user_message=request.user_message,
        state_json=dict(request.state_json),
        retrieved_context=context_items,
        allowed_security_partitions=request.allowed_security_partitions,
        output_schema=dict(request.output_schema or {}),
    )


def _model_request(request: AgentRuntimeRequest, ai_run_id: str, compiled: CompiledContext) -> ModelRequest:
    return ModelRequest(
        model_alias=request.model_alias,
        messages=compiled.messages,
        max_output_tokens=request.max_output_tokens,
        request_id=request.agent_run_id,
        ai_run_id=ai_run_id,
        data_classification=request.data_classification,
        region_requirement=request.region_requirement,
    )


def _guard_context_budget(request: AgentRuntimeRequest, items: tuple[RetrievedContextItem, ...]) -> None:
    token_total = sum(item.token_estimate for item in items)
    if len(items) > request.max_context_items:
        raise AgentRuntimeError("context_item_budget_exceeded", "retrieved context exceeded max_context_items")
    if token_total > request.max_context_tokens:
        raise AgentRuntimeError("context_token_budget_exceeded", "retrieved context exceeded max_context_tokens")


def _guard_readonly_response(response: ModelResponse) -> None:
    if response.normalized_tool_calls:
        raise AgentRuntimeError("tool_calls_not_supported_in_readonly_runtime", "model returned tool calls")


def _usage_payload(response: ModelResponse, context_items: tuple[RetrievedContextItem, ...]) -> dict[str, object]:
    return {
        "inputTokens": response.input_tokens,
        "outputTokens": response.output_tokens,
        "modelCallCount": 1,
        "contextItemCount": len(context_items),
        "finishReason": response.finish_reason,
    }


def _error_payload(exc: Exception) -> dict[str, object]:
    if isinstance(exc, AgentRuntimeError):
        return {"reason": _exception_arg(exc, 0), "detail": _exception_arg(exc, 1)}
    if isinstance(exc, ContextCompilationError):
        return {"reason": exc.reason, "detail": exc.detail}
    if isinstance(exc, ContextRetrievalError):
        return {"reason": exc.reason, "detail": exc.detail}
    adapter_payload = _adapter_error_payload(exc)
    if adapter_payload is not None:
        return adapter_payload
    return {"reason": exc.__class__.__name__, "detail": str(exc)[:240]}


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


def _success_result(
    request: AgentRuntimeRequest,
    ai_run_id: str,
    session_id: str,
    context_items: tuple[RetrievedContextItem, ...],
    response: ModelResponse,
) -> AgentRuntimeResult:
    return AgentRuntimeResult(
        agent_run_id=request.agent_run_id,
        ai_run_id=ai_run_id,
        session_id=session_id,
        run_status="succeeded",
        answer=response.content,
        context_ids=tuple(item.context_id for item in context_items),
        usage=_usage_payload(response, context_items),
    )


def _failed_result(
    request: AgentRuntimeRequest, ai_run_id: str | None, session_id: str | None, exc: Exception
) -> AgentRuntimeResult:
    return AgentRuntimeResult(
        agent_run_id=request.agent_run_id,
        ai_run_id=ai_run_id,
        session_id=session_id,
        run_status="failed",
        answer=None,
        context_ids=(),
        error=_error_payload(exc),
    )
