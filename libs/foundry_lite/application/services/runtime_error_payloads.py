from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import (
    RuntimeJsonObject,
    RuntimeRepository,
    RuntimeRetryPlan,
    RuntimeRow,
    TransactionContext,
)
from foundry_lite.application.ports.adapter_failure import AdapterError, adapter_failure_payload
from foundry_lite.application.services.backup_restore_mode import (
    active_restore_mode_report as active_restore_mode_report,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import FoundryLiteError, NotFound, ValidationFailed


def runtime_error_payload(
    exc: Exception,
    ctx: RequestContext | None = None,
    *,
    run_id: str | None = None,
    correlation_id: str | None = None,
    adapter: str | None = None,
) -> dict[str, object]:
    payload = _base_error_payload(exc)
    trace = _error_trace(ctx, run_id=run_id, correlation_id=correlation_id, adapter=adapter)
    if trace:
        payload["trace"] = trace
    return payload


def _base_error_payload(exc: Exception) -> dict[str, object]:
    if isinstance(exc, AdapterError):
        return adapter_failure_payload(exc)
    if isinstance(exc, FoundryLiteError):
        return {
            "type": exc.code,
            "message": str(exc),
            "details": exc.details,
        }
    return {"type": exc.__class__.__name__, "message": str(exc), "details": {}}


def _error_trace(
    ctx: RequestContext | None,
    *,
    run_id: str | None,
    correlation_id: str | None,
    adapter: str | None,
) -> dict[str, str]:
    trace: dict[str, str] = {}
    if ctx is not None:
        trace.update(
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.actor_user_id,
            request_id=ctx.request_id,
        )
    if run_id is not None:
        trace["run_id"] = run_id
    resolved_correlation_id = _trace_correlation_id(ctx, run_id, correlation_id)
    if resolved_correlation_id is not None:
        trace["correlation_id"] = resolved_correlation_id
    if adapter is not None:
        trace["adapter"] = adapter
    return trace


def _trace_correlation_id(
    ctx: RequestContext | None,
    run_id: str | None,
    correlation_id: str | None,
) -> str | None:
    if correlation_id is not None:
        return correlation_id
    if run_id is not None:
        return run_id
    if ctx is not None:
        return ctx.request_id
    return None


def dead_letter_retry_plan(
    runtime_repository: RuntimeRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    event_id: str,
) -> RuntimeRetryPlan:
    dead_letter = runtime_repository.dead_letter_event_by_id(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        event_id=event_id,
    )
    if dead_letter is None:
        raise NotFound("dead-letter event not found", details={"event_id": event_id})
    outbox_event_id = _required_string(dead_letter, "source_event_id", event_id)
    _require_source_outbox(runtime_repository, conn, ctx, outbox_event_id)
    return {
        "deadLetterEventId": event_id,
        "outboxEventId": outbox_event_id,
        "eventType": _required_string(dead_letter, "event_type", event_id),
        "payload": _required_payload(dead_letter, event_id),
    }


def _required_string(row: RuntimeRow, key: str, event_id: str) -> str:
    value = row.get(key)
    if isinstance(value, str) and value:
        return value
    raise ValidationFailed("dead-letter event is not retryable", details={"event_id": event_id, "field": key})


def _require_source_outbox(
    runtime_repository: RuntimeRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    event_id: str,
) -> None:
    outbox_rows = runtime_repository.rows_for_tenant(transaction=conn, table="outbox_events", tenant_id=ctx.tenant_id)
    if not any(row.get("id") == event_id for row in outbox_rows):
        raise NotFound("source outbox event not found", details={"event_id": event_id})


def _required_payload(row: RuntimeRow, event_id: str) -> RuntimeJsonObject:
    value = row.get("payload")
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    raise ValidationFailed("dead-letter event is not retryable", details={"event_id": event_id, "field": "payload"})
