"""Application service helpers for runtime error payloads workflows."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping

from foundry_lite.application.ports import (
    RuntimeJsonObject,
    RuntimeRepository,
    RuntimeRetryPlan,
    RuntimeRow,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.ports.adapter_failure import AdapterError, adapter_failure_payload
from foundry_lite.application.services.backup_restore_mode import (
    RESTORE_MODE_EVENTS,
)
from foundry_lite.application.services.backup_restore_mode import (
    active_restore_mode_report as active_restore_mode_report,
)
from foundry_lite.application.services.runtime_redaction import redact_sensitive as redact_sensitive
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.error_redaction import scrub_error_mapping, scrub_error_text
from foundry_lite.domain.errors import ConflictDetected, FoundryLiteError, NotFound, ValidationFailed
from foundry_lite.domain.platform.traffic import decide_write_traffic

AuditWriter = Callable[..., None]
RunRelationWriter = Callable[..., bool]
_CLEANUP_FAILURES_ATTRIBUTE = "_foundry_lite_cleanup_failures"
_OPERATIONS_EVIDENCE_ATTRIBUTE = "_foundry_lite_operations_evidence"
_OPERATIONS_PATH_COMPONENT = re.compile(r"[A-Za-z0-9_-]{1,256}\Z")


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


def record_runtime_cleanup_failure(
    primary: Exception,
    *,
    operation: str,
    cleanup_error: Exception,
) -> None:
    """Attach redacted secondary-cleanup evidence without replacing the primary error."""

    current = getattr(primary, _CLEANUP_FAILURES_ATTRIBUTE, ())
    failures = [dict(item) for item in current if isinstance(item, Mapping)]
    failures.append(
        {
            "operation": operation,
            "status": "FAILED",
            "exceptionType": type(cleanup_error).__name__,
        }
    )
    setattr(primary, _CLEANUP_FAILURES_ATTRIBUTE, tuple(failures))


def record_runtime_operations_evidence(
    primary: Exception,
    *,
    run_type: str,
    run_id: str,
) -> None:
    """Attach only navigational coordinates for a durably recorded failed run."""

    if not _is_safe_operations_component(run_type) or not _is_safe_operations_component(run_id):
        return
    setattr(
        primary,
        _OPERATIONS_EVIDENCE_ATTRIBUTE,
        {
            "runType": run_type,
            "runId": run_id,
            "operationsPath": f"/api/operations/runs/{run_type}/{run_id}",
        },
    )


def runtime_operations_evidence(exc: Exception) -> dict[str, str] | None:
    evidence = getattr(exc, _OPERATIONS_EVIDENCE_ATTRIBUTE, None)
    if not isinstance(evidence, Mapping):
        return None
    keys = ("runType", "runId", "operationsPath")
    safe = {key: value for key in keys if isinstance((value := evidence.get(key)), str) and value}
    if len(safe) != len(keys):
        return None
    expected_path = f"/api/operations/runs/{safe['runType']}/{safe['runId']}"
    return safe if safe["operationsPath"] == expected_path else None


def _is_safe_operations_component(value: str) -> bool:
    return _OPERATIONS_PATH_COMPONENT.fullmatch(value) is not None


def audit_dlq_retry(
    audit: AuditWriter,
    conn: TransactionContext,
    ctx: RequestContext,
    *,
    event_id: str,
    outbox_event_id: str,
    event_type: str,
) -> None:
    audit(
        conn,
        ctx,
        event_type="dead_letter_event.retry_requested",
        resource_type="dead_letter_event",
        resource_id=event_id,
        action="operations:retry",
        before_ref={"deadLetterEventId": event_id, "eventType": event_type},
        after_ref={"outboxEventId": outbox_event_id, "status": "pending"},
        correlation_id=ctx.request_id,
    )


def link_dlq_retry(
    run_relation: RunRelationWriter,
    conn: TransactionContext,
    ctx: RequestContext,
    *,
    event_id: str,
    outbox_event_id: str,
    event_type: str,
) -> bool:
    return run_relation(
        conn,
        ctx,
        source_run_type="dead_letter",
        source_run_id=event_id,
        target_run_type="outbox",
        target_run_id=outbox_event_id,
        relation="requeued",
        resource_type="outbox_event",
        resource_id=outbox_event_id,
        metadata={"eventType": event_type},
    )


def _base_error_payload(exc: Exception) -> dict[str, object]:
    if isinstance(exc, AdapterError):
        payload = scrub_error_mapping(adapter_failure_payload(exc))
    elif isinstance(exc, FoundryLiteError):
        payload = {
            "type": exc.code,
            "message": scrub_error_text(str(exc)),
            "details": scrub_error_mapping(exc.details),
        }
    else:
        payload = {"type": exc.__class__.__name__, "message": scrub_error_text(str(exc)), "details": {}}
    return _with_operations_evidence(_with_cleanup_failures(payload, exc), exc)


def _with_operations_evidence(payload: dict[str, object], exc: Exception) -> dict[str, object]:
    evidence = runtime_operations_evidence(exc)
    if evidence is None:
        return payload
    details = payload.get("details")
    merged = dict(details) if isinstance(details, Mapping) else {}
    merged["operationsEvidence"] = evidence
    payload["details"] = merged
    return payload


def _with_cleanup_failures(payload: dict[str, object], exc: Exception) -> dict[str, object]:
    failures = getattr(exc, _CLEANUP_FAILURES_ATTRIBUTE, ())
    safe = [scrub_error_mapping(item) for item in failures if isinstance(item, Mapping)]
    if not safe:
        return payload
    details = payload.get("details")
    merged = dict(details) if isinstance(details, Mapping) else {}
    merged["cleanupFailures"] = safe
    payload["details"] = merged
    return payload


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


def require_outbox_retry_open(
    runtime_repository: RuntimeRepository,
    conn: TransactionContext,
    ctx: RequestContext,
) -> None:
    audit_events = runtime_repository.rows_for_tenant(
        transaction=conn,
        table="audit_events",
        tenant_id=ctx.tenant_id,
        event_types=RESTORE_MODE_EVENTS,
    )
    restore_mode = active_restore_mode_report(audit_events)
    if restore_mode is None:
        return
    raise ConflictDetected(
        "restore mode keeps outbox publisher paused",
        details={
            "restore_id": restore_mode["restoreId"],
            "status": restore_mode["status"],
            "is_outbox_publisher_paused": restore_mode["is_outbox_publisher_paused"],
        },
    )


def require_write_traffic_open(
    engine: TransactionManager,
    runtime_repository: RuntimeRepository,
    ctx: RequestContext,
    *,
    operation: str,
    resource_type: str,
    resource_id: str,
) -> None:
    with engine.begin() as conn:
        audit_events = runtime_repository.rows_for_tenant(
            transaction=conn,
            table="audit_events",
            tenant_id=ctx.tenant_id,
            event_types=RESTORE_MODE_EVENTS,
        )
    decide_write_traffic(
        active_restore_mode_report(audit_events),
        operation=operation,
        resource_type=resource_type,
        resource_id=resource_id,
    )
