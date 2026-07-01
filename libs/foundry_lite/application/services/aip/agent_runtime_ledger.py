"""Application service helpers for agent runtime ledger workflows."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from foundry_lite.application.ports import (
    AiContextItemRecord,
    AiExecutionEventRecord,
    AiExecutionRunRecord,
    AiMessageRecord,
    AiModelCallRecord,
    AiRunRepository,
    AiSessionRecord,
    AiUsageLedgerRecord,
)
from foundry_lite.application.ports.context_provider import RetrievedContextItem
from foundry_lite.application.ports.language_model import ModelResponse
from foundry_lite.application.ports.transaction_context import AI_RUN_SUCCEEDED, TransactionContext
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.aip.context_compiler import CompiledContext
from foundry_lite.application.services.aip.eval_identifiers import hash_json
from foundry_lite.application.services.aip.model_gateway import ModelResolution
from foundry_lite.domain.context import RequestContext

JsonObject = Mapping[str, object]


class AgentRuntimeRequestShape(Protocol):
    @property
    def agent_run_id(self) -> str: ...

    @property
    def agent_version_id(self) -> str: ...

    @property
    def model_alias(self) -> str: ...

    @property
    def prompt_version_id(self) -> str: ...

    @property
    def ontology_version_id(self) -> str: ...

    @property
    def policy_version(self) -> str: ...

    @property
    def max_context_items(self) -> int: ...

    @property
    def max_context_tokens(self) -> int: ...

    @property
    def max_model_calls(self) -> int: ...

    @property
    def max_loop_iterations(self) -> int: ...

    @property
    def max_tool_calls(self) -> int: ...

    @property
    def max_tool_output_bytes(self) -> int: ...

    @property
    def max_output_tokens(self) -> int: ...


def new_ai_run_id() -> str:
    return f"aip-agent-run-{_new_id('agent')}"


def ledger_timestamp() -> str:
    return _now()


def session_record(
    ctx: RequestContext, request: AgentRuntimeRequestShape, session_id: str, now: str
) -> AiSessionRecord:
    return AiSessionRecord(
        id=session_id,
        tenant_id=ctx.tenant_id,
        agent_version_id=request.agent_version_id,
        actor_user_id=ctx.actor_user_id,
        status="active",
        created_at=now,
        last_activity_at=now,
    )


def message_record(
    ctx: RequestContext,
    request: AgentRuntimeRequestShape,
    session_id: str,
    content: str,
    role: str,
    now: str,
) -> AiMessageRecord:
    return AiMessageRecord(
        id=message_id(request, role),
        tenant_id=ctx.tenant_id,
        session_id=session_id,
        role=role,
        client_message_id=f"{request.agent_run_id}:{role}",
        content_ref=f"aip-agent://{request.agent_run_id}/{role}-message",
        content_hash=hash_json({"role": role, "content": content}),
        created_at=now,
    )


def message_id(request: AgentRuntimeRequestShape, role: str) -> str:
    return f"aip-agent-message-{request.agent_run_id}-{role}"


def run_record(
    ctx: RequestContext,
    request: AgentRuntimeRequestShape,
    ai_run_id: str,
    session_id: str,
    compiled: CompiledContext,
    resolution: ModelResolution,
    now: str,
) -> AiExecutionRunRecord:
    return AiExecutionRunRecord(
        id=ai_run_id,
        tenant_id=ctx.tenant_id,
        session_id=session_id,
        agent_version_id=request.agent_version_id,
        actor_user_id=ctx.actor_user_id,
        request_id=ctx.request_id,
        trace_id=f"aip-agent-trace-{request.agent_run_id}",
        status="running",
        ontology_version_id=request.ontology_version_id,
        model_alias_version=request.model_alias,
        resolved_model_id=resolution.model_id,
        resolved_model_revision=resolution.revision,
        prompt_version_id=request.prompt_version_id,
        compiled_prompt_hash=compiled.compiled_prompt_hash,
        tool_manifest_hash=compiled.tool_manifest_hash,
        context_manifest_hash=compiled.context_manifest_hash,
        state_snapshot_hash=compiled.state_snapshot_hash,
        policy_snapshot_hash=compiled.policy_snapshot_hash,
        budget_json=_budget_payload(request),
        usage_json=None,
        error_json=None,
        started_at=now,
        completed_at=None,
    )


def record_context_items(
    repository: AiRunRepository,
    transaction: TransactionContext,
    ctx: RequestContext,
    ai_run_id: str,
    items: tuple[RetrievedContextItem, ...],
) -> None:
    for index, item in enumerate(items, start=1):
        repository.record_context_item(
            transaction=transaction,
            record=_context_item_record(ctx, ai_run_id, item, index),
        )


def append_events(
    repository: AiRunRepository,
    transaction: TransactionContext,
    ctx: RequestContext,
    request: AgentRuntimeRequestShape,
    ai_run_id: str,
    event_types: tuple[str, ...],
    now: str,
    start: int = 1,
) -> None:
    for offset, event_type in enumerate(event_types, start=start):
        repository.append_execution_event(
            transaction=transaction,
            record=event_record(ctx, ai_run_id, offset, event_type, _event_payload(request, event_type), now),
        )


def mark_execution_run_succeeded(
    repository: AiRunRepository,
    transaction: TransactionContext,
    ctx: RequestContext,
    ai_run_id: str,
    usage: JsonObject,
    now: str,
) -> None:
    repository.update_execution_run_status(
        transaction=transaction,
        tenant_id=ctx.tenant_id,
        ai_run_id=ai_run_id,
        transition=AI_RUN_SUCCEEDED,
        usage_json=usage,
        error_json=None,
        completed_at=now,
    )


def event_record(
    ctx: RequestContext, ai_run_id: str, sequence: int, event_type: str, payload: JsonObject, now: str
) -> AiExecutionEventRecord:
    return AiExecutionEventRecord(
        id=f"{ai_run_id}-event-{sequence}",
        tenant_id=ctx.tenant_id,
        ai_run_id=ai_run_id,
        sequence=sequence,
        event_type=event_type,
        payload_ref=f"aip-agent://{ai_run_id}/events/{sequence}",
        payload_hash=hash_json(payload),
        redacted_preview=str(event_type)[:120],
        created_at=now,
    )


def model_call(
    ctx: RequestContext, ai_run_id: str, compiled: CompiledContext, response: ModelResponse
) -> AiModelCallRecord:
    return AiModelCallRecord(
        id=f"{ai_run_id}-model-1",
        tenant_id=ctx.tenant_id,
        ai_run_id=ai_run_id,
        attempt=1,
        provider=response.provider,
        model_revision=response.resolved_model_revision,
        request_hash=compiled.compiled_prompt_hash,
        response_hash=hash_json({"finishReason": response.finish_reason, "contentHash": hash_json(response.content)}),
        provider_request_id=response.provider_request_id,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        latency_ms=response.latency_ms,
        status="succeeded",
        error_json=None,
    )


def usage_record(ctx: RequestContext, ai_run_id: str, response: ModelResponse, now: str) -> AiUsageLedgerRecord:
    return AiUsageLedgerRecord(
        id=f"{ai_run_id}-usage-1",
        tenant_id=ctx.tenant_id,
        ai_run_id=ai_run_id,
        provider=response.provider,
        model_revision=response.resolved_model_revision,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        estimated_cost=0.0,
        currency="USD",
        recorded_at=now,
    )


def operations_ref(ai_run_id: str | None) -> dict[str, object] | None:
    if ai_run_id is None:
        return None
    return {"runType": "ai", "runId": ai_run_id, "detailPath": f"/api/operations/runs/ai/{ai_run_id}"}


def _context_item_record(
    ctx: RequestContext, ai_run_id: str, item: RetrievedContextItem, index: int
) -> AiContextItemRecord:
    return AiContextItemRecord(
        id=f"{ai_run_id}-context-{index}",
        tenant_id=ctx.tenant_id,
        ai_run_id=ai_run_id,
        context_id=item.context_id,
        kind=item.kind,
        source_resource_type=_source_resource_type(item.kind),
        source_resource_id=item.source_ref,
        source_version=item.source_version,
        content_hash=item.content_hash,
        retrieval_method=item.retrieval_method,
        relevance_score=item.relevance_score,
        security_partition=item.security_partition,
        token_estimate=item.token_estimate,
        is_selected=True,
        omission_reason=None,
    )


def _budget_payload(request: AgentRuntimeRequestShape) -> dict[str, object]:
    return {
        "maxContextItems": request.max_context_items,
        "maxContextTokens": request.max_context_tokens,
        "maxModelCalls": request.max_model_calls,
        "maxLoopIterations": request.max_loop_iterations,
        "maxToolCalls": request.max_tool_calls,
        "maxToolOutputBytes": request.max_tool_output_bytes,
        "maxOutputTokens": request.max_output_tokens,
    }


def _event_payload(request: AgentRuntimeRequestShape, event_type: str) -> dict[str, object]:
    return {
        "eventType": event_type,
        "agentRunId": request.agent_run_id,
        "agentVersionId": request.agent_version_id,
        "policyVersion": request.policy_version,
    }


def _source_resource_type(kind: str) -> str:
    if kind == "document":
        return "content_unit"
    if kind == "function":
        return "tool_result"
    return "object"
