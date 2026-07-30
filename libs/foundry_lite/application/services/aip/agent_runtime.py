"""AIP Agent Runtime read-only loop (P0n, §8.5/§8.6/§8.9/§10.2).

This slice is the first product path that retrieves authorized context,
compiles the governed prompt, calls the Model Gateway, and writes the AI run
ledger that Operations can inspect. The default path is deliberately read-only;
P0z allows one explicitly authorized brokered read-tool loop before the final
governed model call.
"""

from __future__ import annotations

from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.application.services.aip.agent_runtime_citations import (
    AgentRuntimeAnswer,
    CitationResolver,
    resolve_agent_answer_citations,
)
from foundry_lite.application.services.aip.agent_runtime_contracts import (
    AgentRuntimeError,
    AgentRuntimeRequest,
    AgentRuntimeResult,
    RetrievalContentSearch,
    RetrievalObjectQuery,
    aggregate_response,
    compile_request,
    compiled_prompt_text,
    error_payload,
    failed_result,
    model_request,
    retrieve_runtime_context,
    success_result,
    usage_payload,
    validate_request,
)
from foundry_lite.application.services.aip.agent_runtime_ledger import (
    append_events,
    event_record,
    ledger_timestamp,
    mark_execution_run_succeeded,
    message_id,
    message_record,
    new_ai_run_id,
    record_context_items,
    run_record,
    session_record,
    usage_record,
)
from foundry_lite.application.services.aip.agent_runtime_ports import (
    AI_RUN_FAILED,
    ModelResponse,
    RetrievedContextItem,
)
from foundry_lite.application.services.aip.agent_runtime_tools import (
    ActionProposalCreator,
    AgentRuntimeActionProposalExecution,
    AgentRuntimeToolExecution,
    ToolBroker,
    execute_action_proposal_tool_call,
    execute_model_tool_call,
    followup_model_request,
    guard_final_response,
    success_event_sequence,
)
from foundry_lite.application.services.aip.context_compiler import (
    CompiledContext,
    ContextCompilerService,
)
from foundry_lite.application.services.aip.model_gateway import ModelGatewayService, ModelResolution
from foundry_lite.application.services.aip.prompt_artifact_service import PromptArtifactService
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext

__all__ = ("AgentRuntimeError", "AgentRuntimeRequest", "AgentRuntimeResult", "AgentRuntimeService")


def _aggregate_charged(charged: list[ModelResponse]) -> ModelResponse | None:
    """Combine every billed model response so the failure path records full spend."""
    if not charged:
        return None
    aggregated = charged[0]
    for response in charged[1:]:
        aggregated = aggregate_response(aggregated, response)
    return aggregated


class AgentRuntimeService(CoreService):
    """Retrieve context, compile prompt, call model gateway, and write ledger evidence."""

    required_dependencies = ("engine", "ai_run_repository")
    required_collaborators = (
        "context_compiler_service",
        "model_gateway_service",
        "prompt_artifact_service",
        "object_query_service",
        "content_retrieval_service",
        "citation_service",
        "tool_broker_service",
        "action_proposal_service",
    )
    context_compiler_service: ContextCompilerService
    model_gateway_service: ModelGatewayService
    prompt_artifact_service: PromptArtifactService
    object_query_service: RetrievalObjectQuery
    content_retrieval_service: RetrievalContentSearch
    citation_service: CitationResolver
    tool_broker_service: ToolBroker
    action_proposal_service: ActionProposalCreator

    def run(self, ctx: RequestContext, request: AgentRuntimeRequest) -> AgentRuntimeResult:
        ai_run_id: str | None = None
        session_id: str | None = None
        seeded = False
        resolution: ModelResolution | None = None
        # Every charged provider call is appended here, including a follow-up call
        # made inside the tool loop, so a failure after the 2nd call still records
        # the real spend. Capturing only the first response under-billed the run.
        charged: list[ModelResponse] = []
        try:
            validate_request(ctx, request)
            ai_run_id = request.ai_run_id or new_ai_run_id()
            session_id = request.session_id or f"aip-agent-session-{request.agent_run_id}"
            context_items = self._retrieve_context(ctx, request)
            compiled = self.context_compiler_service.compile(ctx, compile_request(request, context_items))
            resolution = self.model_gateway_service.resolve_model(ctx, request.model_alias, request.environment)
            self._seed_ledger(ctx, request, ai_run_id, session_id, context_items, compiled, resolution)
            seeded = True
            aggregated, answer, tool_execution, action_proposal = self._model_turn_and_finish(
                ctx, request, ai_run_id, session_id, context_items, compiled, resolution, charged
            )
        except Exception as exc:
            if seeded and ai_run_id is not None:
                self._fail_seeded_run(ctx, ai_run_id, exc, resolution, _aggregate_charged(charged))
            return failed_result(request, ai_run_id if seeded else None, session_id if seeded else None, exc)
        return success_result(
            request, ai_run_id, session_id, context_items, aggregated, answer, tool_execution, action_proposal
        )

    def _model_turn_and_finish(
        self,
        ctx: RequestContext,
        request: AgentRuntimeRequest,
        ai_run_id: str,
        session_id: str,
        context_items: tuple[RetrievedContextItem, ...],
        compiled: CompiledContext,
        resolution: ModelResolution,
        charged: list[ModelResponse],
    ) -> tuple[
        ModelResponse,
        AgentRuntimeAnswer,
        AgentRuntimeToolExecution | None,
        AgentRuntimeActionProposalExecution | None,
    ]:
        """Invoke the model, run any tool turn, then durably finish the run.

        Finalization stays inside the caller's guarded region: when it was outside,
        a transient failure rolled back the terminal CAS and stranded the run in
        'running'. The response payload is built after the run is durably succeeded.
        """
        self._record_initial_prompt_artifact(ctx, ai_run_id, compiled)
        response = self.model_gateway_service.invoke(ctx, model_request(request, ai_run_id, compiled))
        charged.append(response)
        final_response, answer, tool_execution, action_proposal = self._complete_model_turn(
            ctx, request, ai_run_id, compiled, response, charged
        )
        aggregated = aggregate_response(response, final_response)
        outcome = (aggregated, answer, tool_execution, action_proposal)
        self._finish_success(ctx, request, ai_run_id, session_id, context_items, *outcome, resolution)
        return outcome

    def _retrieve_context(self, ctx: RequestContext, request: AgentRuntimeRequest) -> tuple[RetrievedContextItem, ...]:
        return retrieve_runtime_context(
            ctx,
            self.object_query_service,
            self.content_retrieval_service,
            request,
        )

    def _record_initial_prompt_artifact(self, ctx: RequestContext, ai_run_id: str, compiled: CompiledContext) -> None:
        self.prompt_artifact_service.record_compiled_prompt(
            ctx,
            ai_run_id=ai_run_id,
            compiled_prompt_hash=compiled.compiled_prompt_hash,
            compiled_prompt_text=compiled_prompt_text(compiled),
        )

    def _complete_model_turn(
        self,
        ctx: RequestContext,
        request: AgentRuntimeRequest,
        ai_run_id: str,
        compiled: CompiledContext,
        response: ModelResponse,
        charged: list[ModelResponse],
    ) -> tuple[
        ModelResponse,
        AgentRuntimeAnswer,
        AgentRuntimeToolExecution | None,
        AgentRuntimeActionProposalExecution | None,
    ]:
        action_proposal = self._execute_action_proposal_tool_call(ctx, request, ai_run_id, response)
        if action_proposal is not None:
            answer = AgentRuntimeAnswer(answer=action_proposal.answer, citations=())
            return response, answer, None, action_proposal
        tool_execution = self._execute_model_tool_call(ctx, request, ai_run_id, compiled, response)
        final_response = self._final_model_response(
            ctx, request, ai_run_id, compiled, response, tool_execution, charged
        )
        answer = self._resolve_answer_citations(ctx, request, ai_run_id, final_response)
        return final_response, answer, tool_execution, None

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
        now = ledger_timestamp()
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
        response: ModelResponse,
        answer: AgentRuntimeAnswer,
        tool_execution: AgentRuntimeToolExecution | None,
        action_proposal: AgentRuntimeActionProposalExecution | None,
        resolution: ModelResolution,
    ) -> None:
        usage = usage_payload(response, context_items, tool_execution, action_proposal)
        now = ledger_timestamp()
        with self.engine.begin() as transaction:
            self.ai_run_repository.record_usage(
                transaction=transaction,
                record=usage_record(ctx, ai_run_id, response, now, resolution.pricing_json),
            )
            self.ai_run_repository.insert_message_or_get_existing(
                transaction=transaction,
                record=message_record(ctx, request, session_id, answer.answer, "assistant", now),
            )
            append_events(
                self.ai_run_repository,
                transaction,
                ctx,
                request,
                ai_run_id,
                ("succeeded",),
                now,
                start=success_event_sequence(tool_execution, action_proposal),
            )
            mark_execution_run_succeeded(self.ai_run_repository, transaction, ctx, ai_run_id, usage, now)

    def _resolve_answer_citations(
        self, ctx: RequestContext, request: AgentRuntimeRequest, ai_run_id: str, response: ModelResponse
    ) -> AgentRuntimeAnswer:
        now = ledger_timestamp()
        return resolve_agent_answer_citations(
            ctx,
            self.citation_service,
            ai_run_id=ai_run_id,
            message_id=message_id(request, "assistant"),
            model_content=response.content,
            issued_at=now,
        )

    def _execute_model_tool_call(
        self,
        ctx: RequestContext,
        request: AgentRuntimeRequest,
        ai_run_id: str,
        compiled: CompiledContext,
        response: ModelResponse,
    ) -> AgentRuntimeToolExecution | None:
        tool_execution = execute_model_tool_call(
            ctx=ctx,
            broker=self.tool_broker_service,
            request=request,
            ai_run_id=ai_run_id,
            compiled=compiled,
            response=response,
            occurred_at=ledger_timestamp(),
        )
        if tool_execution is not None:
            self._record_tool_execution(ctx, request, ai_run_id, tool_execution)
        return tool_execution

    def _execute_action_proposal_tool_call(
        self,
        ctx: RequestContext,
        request: AgentRuntimeRequest,
        ai_run_id: str,
        response: ModelResponse,
    ) -> AgentRuntimeActionProposalExecution | None:
        action_proposal = execute_action_proposal_tool_call(
            ctx=ctx,
            proposer=self.action_proposal_service,
            request=request,
            ai_run_id=ai_run_id,
            response=response,
            occurred_at=ledger_timestamp(),
        )
        if action_proposal is not None:
            self._record_action_proposal_tool_call(ctx, request, ai_run_id, action_proposal)
        return action_proposal

    def _record_action_proposal_tool_call(
        self,
        ctx: RequestContext,
        request: AgentRuntimeRequest,
        ai_run_id: str,
        action_proposal: AgentRuntimeActionProposalExecution,
    ) -> None:
        now = ledger_timestamp()
        with self.engine.begin() as transaction:
            self.ai_run_repository.record_tool_call(transaction=transaction, record=action_proposal.ledger_record)
            append_events(
                self.ai_run_repository,
                transaction,
                ctx,
                request,
                ai_run_id,
                ("tool_pending", "waiting_human_review"),
                now,
                start=4,
            )

    def _record_tool_execution(
        self,
        ctx: RequestContext,
        request: AgentRuntimeRequest,
        ai_run_id: str,
        tool_execution: AgentRuntimeToolExecution,
    ) -> None:
        now = ledger_timestamp()
        with self.engine.begin() as transaction:
            self.ai_run_repository.record_tool_call(transaction=transaction, record=tool_execution.result.ledger_record)
            append_events(
                self.ai_run_repository,
                transaction,
                ctx,
                request,
                ai_run_id,
                ("tool_pending", "tool_running", "model_running"),
                now,
                start=4,
            )

    def _final_model_response(
        self,
        ctx: RequestContext,
        request: AgentRuntimeRequest,
        ai_run_id: str,
        compiled: CompiledContext,
        first_response: ModelResponse,
        tool_execution: AgentRuntimeToolExecution | None,
        charged: list[ModelResponse],
    ) -> ModelResponse:
        if tool_execution is None:
            return first_response
        self.prompt_artifact_service.record_compiled_prompt(
            ctx,
            ai_run_id=ai_run_id,
            compiled_prompt_hash=tool_execution.followup_prompt_hash,
            compiled_prompt_text=tool_execution.followup_prompt_text,
            artifact_id=f"{ai_run_id}-compiled-prompt-model-2",
        )
        response = self.model_gateway_service.invoke(ctx, followup_model_request(request, ai_run_id, tool_execution))
        # The follow-up call is billed even if the guard below rejects the response,
        # so record it before guarding so the failure path bills both calls.
        charged.append(response)
        guard_final_response(response)
        return response

    def _fail_seeded_run(
        self,
        ctx: RequestContext,
        ai_run_id: str,
        exc: Exception,
        resolution: ModelResolution | None,
        charged_response: ModelResponse | None,
    ) -> None:
        try:
            with self.engine.begin() as transaction:
                if (
                    self.ai_run_repository.execution_run_by_id(
                        transaction=transaction, tenant_id=ctx.tenant_id, ai_run_id=ai_run_id
                    )
                    is None
                ):
                    return
                self._record_charged_usage(ctx, transaction, ai_run_id, resolution, charged_response)
                self.ai_run_repository.append_execution_event(
                    transaction=transaction,
                    record=event_record(ctx, ai_run_id, 99, "failed", error_payload(exc), ledger_timestamp()),
                )
                self.ai_run_repository.update_execution_run_status(
                    transaction=transaction,
                    tenant_id=ctx.tenant_id,
                    ai_run_id=ai_run_id,
                    transition=AI_RUN_FAILED,
                    usage_json=None,
                    error_json=error_payload(exc),
                    completed_at=ledger_timestamp(),
                )
        except Exception:
            return

    def _record_charged_usage(
        self,
        ctx: RequestContext,
        transaction: TransactionContext,
        ai_run_id: str,
        resolution: ModelResolution | None,
        charged_response: ModelResponse | None,
    ) -> None:
        # The provider was already called and charged; record the spend so accounting matches real
        # usage even though a later step failed the run.
        if charged_response is None or resolution is None:
            return
        self.ai_run_repository.record_usage(
            transaction=transaction,
            record=usage_record(ctx, ai_run_id, charged_response, ledger_timestamp(), resolution.pricing_json),
        )
