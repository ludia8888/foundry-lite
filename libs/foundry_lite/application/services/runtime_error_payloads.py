"""Application service helpers for runtime error payloads workflows."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import cast

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
    active_restore_mode_report as active_restore_mode_report,
)
from foundry_lite.application.services.runtime_redaction import redact_sensitive as redact_sensitive
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, FoundryLiteError, NotFound, ValidationFailed

AuditWriter = Callable[..., None]
RunRelationWriter = Callable[..., bool]
_SECRET_KEY_PARTS = frozenset(
    {
        "authorization",
        "cookie",
        "setcookie",
        "token",
        "accesstoken",
        "refreshtoken",
        "apikey",
        "clientsecret",
        "connectionstring",
        "credentials",
        "databaseurl",
        "dsn",
        "headervalue",
        "hmac",
        "jwt",
        "privatekey",
        "secret",
        "signature",
        "password",
        "plaintext",
        "prompt",
        "compiledprompt",
        "providerrequest",
        "providerresponse",
    }
)
_AUTHORIZATION_PATTERN = re.compile(r"\bauthorization\s*:\s*bearer\s+\S+", re.IGNORECASE)
_BEARER_PATTERN = re.compile(r"\bbearer\s+\S+", re.IGNORECASE)
_ASSIGNMENT_PATTERN = re.compile(
    r"\b(authorization|token|access[_-]?token|refresh[_-]?token|api[_-]?key|client[_-]?secret|"
    r"connection[_-]?string|credentials?|database[_-]?url|dsn|header[_-]?value|hmac|jwt|"
    r"private[_-]?key|secret|signature|password)"
    r"\s*[:=]\s*\S+",
    re.IGNORECASE,
)


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


def scrub_error_mapping(value: Mapping[str, object]) -> dict[str, object]:
    return cast(dict[str, object], _scrub_error_payload(value))


def scrub_error_text(value: str) -> str:
    return _scrub_text(value)


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
        return scrub_error_mapping(adapter_failure_payload(exc))
    if isinstance(exc, FoundryLiteError):
        return {
            "type": exc.code,
            "message": scrub_error_text(str(exc)),
            "details": scrub_error_mapping(exc.details),
        }
    return {"type": exc.__class__.__name__, "message": scrub_error_text(str(exc)), "details": {}}


def _scrub_error_payload(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): "***MASKED***" if _is_secret_key(key) else _scrub_error_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_scrub_error_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_scrub_error_payload(item) for item in value)
    if isinstance(value, str):
        return _scrub_text(value)
    return value


def _scrub_text(value: str) -> str:
    if _contains_secret_term(value):
        return "***MASKED***"
    scrubbed = _AUTHORIZATION_PATTERN.sub("Authorization: Bearer ***MASKED***", value)
    scrubbed = _BEARER_PATTERN.sub("Bearer ***MASKED***", scrubbed)
    return _ASSIGNMENT_PATTERN.sub(lambda match: f"{match.group(1)}=***MASKED***", scrubbed)


def _is_secret_key(key: object) -> bool:
    normalized = _normalize_secret_text(str(key))
    return any(term in normalized for term in _SECRET_KEY_PARTS)


def _contains_secret_term(value: str) -> bool:
    normalized = _normalize_secret_text(value)
    return any(term in normalized for term in _SECRET_KEY_PARTS)


def _normalize_secret_text(value: str) -> str:
    return "".join(char for char in value.casefold() if char.isalnum())


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
    audit_events = runtime_repository.rows_for_tenant(transaction=conn, table="audit_events", tenant_id=ctx.tenant_id)
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
        )
    restore_mode = active_restore_mode_report(audit_events)
    if restore_mode is None:
        return
    raise ConflictDetected(
        "restore mode blocks write traffic",
        details={
            "restore_id": restore_mode["restoreId"],
            "status": restore_mode["status"],
            "operation": operation,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "is_write_traffic_paused": restore_mode["is_write_traffic_paused"],
            "is_serving_traffic_open": restore_mode["is_serving_traffic_open"],
        },
    )
