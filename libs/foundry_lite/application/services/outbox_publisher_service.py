"""Application service helpers for outbox publisher service workflows."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Literal, TypedDict

from foundry_lite.application.ports import (
    OUTBOX_PUBLISH_FAILED,
    OUTBOX_PUBLISH_RETRY_PENDING,
    OUTBOX_PUBLISHED,
    OUTBOX_PUBLISHING,
    OUTBOX_PUBLISHING_RECLAIM,
    DeadLetterEventRecord,
    RuntimeJsonObject,
    RuntimeRow,
    StreamPublishRequest,
    TransactionContext,
)
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.runtime_service import RuntimeService
from foundry_lite.domain.context import DEMO_ADMIN_ROLES, RequestContext
from foundry_lite.security.tenant_context import tenant_context

DEFAULT_OUTBOX_STREAM_NAME = "foundry-lite-outbox"
MAX_OUTBOX_PUBLISH_BATCH_SIZE = 500
MAX_OUTBOX_PUBLISH_ATTEMPTS = 5
# A worker that crashes between claiming an event (pending -> publishing) and marking it
# published/failed leaves the row stranded in publishing forever. Each publish cycle first
# requeues rows whose claim is older than this lease so no event is silently lost. The
# window is generous relative to a single publish so a still-in-flight claim is not reclaimed
# out from under a live worker. Delivery is at-least-once: a crash after the external publish
# succeeds but before the row is marked published re-publishes the event on reclaim, so
# consumers must dedupe on the event id. mark_published/mark_failed are fenced by the claim
# timestamp, so a worker whose lease was reclaimed cannot mark a row another worker now owns.
OUTBOX_PUBLISH_LEASE_TIMEOUT_SECONDS = 300


class OutboxPublishBatchResult(TypedDict):
    status: Literal["completed"]
    streamName: str
    requested: int
    published: int
    failed: int
    retrying: int
    skipped: int
    eventIds: list[str]
    deadLetterEventIds: list[str]


class OutboxTenantPublishBatchResult(OutboxPublishBatchResult):
    tenantId: str


class OutboxPublishAllResult(OutboxPublishBatchResult):
    tenantResults: list[OutboxTenantPublishBatchResult]


class OutboxPublisherService(CoreService):
    """Publish pending runtime outbox rows through the configured stream adapter."""

    required_dependencies = ("engine", "metadata_repository", "runtime_repository", "stream_adapter")
    required_collaborators = ("runtime_service",)
    runtime_service: RuntimeService

    def publish_all_pending_outbox(
        self,
        *,
        actor_user_id: str,
        request_id: str,
        stream_name: str = DEFAULT_OUTBOX_STREAM_NAME,
        limit: int = 100,
    ) -> OutboxPublishAllResult:
        """Run one bounded publish pass for every known tenant under its own RLS context."""

        results = [
            self._publish_pending_for_tenant(
                tenant_id=tenant_id,
                actor_user_id=actor_user_id,
                request_id=request_id,
                stream_name=stream_name,
                limit=limit,
            )
            for tenant_id in self.metadata_repository.list_tenant_ids()
        ]
        return _all_tenant_result(stream_name=stream_name, tenant_results=results)

    def publish_pending_outbox(
        self,
        *,
        ctx: RequestContext | None = None,
        stream_name: str = DEFAULT_OUTBOX_STREAM_NAME,
        limit: int = 100,
    ) -> OutboxPublishBatchResult:
        resolved_ctx = ctx or RequestContext()
        bounded_limit = _bounded_limit(limit)
        self.runtime_service._require_or_audit(resolved_ctx, "operations:retry", "outbox", stream_name)
        self._reclaim_stale_publishing(resolved_ctx)
        pending = self._pending_events(resolved_ctx, bounded_limit)
        result = _empty_result(stream_name=stream_name, requested=len(pending))
        for row in pending:
            self._publish_one(row=row, ctx=resolved_ctx, stream_name=stream_name, result=result)
        return result

    def _publish_pending_for_tenant(
        self,
        *,
        tenant_id: str,
        actor_user_id: str,
        request_id: str,
        stream_name: str,
        limit: int,
    ) -> OutboxTenantPublishBatchResult:
        ctx = _worker_context(tenant_id=tenant_id, actor_user_id=actor_user_id, request_id=request_id)
        with tenant_context(tenant_id):
            result = self.publish_pending_outbox(ctx=ctx, stream_name=stream_name, limit=limit)
        return {"tenantId": tenant_id, **result}

    def _reclaim_stale_publishing(self, ctx: RequestContext) -> int:
        with self.engine.begin() as conn:
            self.runtime_service._require_outbox_retry_open(conn, ctx)
            return self.runtime_repository.reclaim_stale_publishing_events(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                transition=OUTBOX_PUBLISHING_RECLAIM,
                claimed_before=_lease_cutoff(),
            )

    def _pending_events(self, ctx: RequestContext, limit: int) -> list[RuntimeRow]:
        with self.engine.begin() as conn:
            self.runtime_service._require_outbox_retry_open(conn, ctx)
            return self.runtime_repository.pending_outbox_events(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                limit=limit,
            )

    def _publish_one(
        self,
        *,
        row: RuntimeRow,
        ctx: RequestContext,
        stream_name: str,
        result: OutboxPublishBatchResult,
    ) -> None:
        event_id = _row_text(row, "id")
        claimed = self._claim_event(ctx, event_id)
        if claimed is None:
            result["skipped"] += 1
            return
        fence = _row_text(claimed, "claimed_at")
        try:
            self.stream_adapter.publish_event(_stream_request(claimed, ctx=ctx, stream_name=stream_name))
        except Exception as exc:  # noqa: BLE001 - classified into bounded retry or durable DLQ evidence.
            outcome, dead_letter_id = self._record_failure(ctx=ctx, row=claimed, fence=fence, exc=exc)
            result[outcome] += 1
            if dead_letter_id is not None:
                result["deadLetterEventIds"].append(dead_letter_id)
            return
        if self._mark_published(ctx, claimed, fence=fence):
            result["published"] += 1
            result["eventIds"].append(event_id)
        else:
            # Our claim was superseded before mark_published landed; the row is
            # owned by another worker. Counting it as published would be a lie.
            result["skipped"] += 1

    def _record_failure(
        self, *, ctx: RequestContext, row: RuntimeRow, fence: str, exc: Exception
    ) -> tuple[Literal["retrying", "failed", "skipped"], str | None]:
        error = self.runtime_service._error_payload(
            exc,
            ctx,
            run_id=_row_text(row, "id"),
            correlation_id=_row_text(row, "correlation_id"),
            adapter=self.stream_adapter.profile_name,
        )
        if _row_attempts(row) < MAX_OUTBOX_PUBLISH_ATTEMPTS:
            is_requeued = self._mark_retry_pending(ctx=ctx, row=row, fence=fence, error=error)
            return ("retrying", None) if is_requeued else ("skipped", None)
        dead_letter_id = self._mark_failed(ctx=ctx, row=row, fence=fence, error=error)
        return ("failed", dead_letter_id) if dead_letter_id is not None else ("skipped", None)

    def _claim_event(self, ctx: RequestContext, event_id: str) -> RuntimeRow | None:
        with self.engine.begin() as conn:
            self.runtime_service._require_outbox_retry_open(conn, ctx)
            return self.runtime_repository.mark_outbox_event_publishing(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                event_id=event_id,
                transition=OUTBOX_PUBLISHING,
                claimed_at=_lease_now(),
            )

    def _mark_published(self, ctx: RequestContext, row: RuntimeRow, *, fence: str) -> bool:
        event_id = _row_text(row, "id")
        with self.engine.begin() as conn:
            published = self.runtime_repository.mark_outbox_event_published(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                event_id=event_id,
                transition=OUTBOX_PUBLISHED,
                published_at=_now(),
                claimed_at=fence,
            )
            if published is not None:
                self._audit_publish(conn, ctx, published, status="published")
                return True
        return False

    def _mark_retry_pending(
        self, *, ctx: RequestContext, row: RuntimeRow, fence: str, error: Mapping[str, object]
    ) -> bool:
        event_id = _row_text(row, "id")
        with self.engine.begin() as conn:
            pending = self.runtime_repository.mark_outbox_event_retry_pending(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                event_id=event_id,
                transition=OUTBOX_PUBLISH_RETRY_PENDING,
                claimed_at=fence,
            )
            if pending is not None:
                self._audit_publish(conn, ctx, pending, status="retry_scheduled", error=error)
                return True
        return False

    def _mark_failed(
        self, *, ctx: RequestContext, row: RuntimeRow, fence: str, error: Mapping[str, object]
    ) -> str | None:
        event_id = _row_text(row, "id")
        failed_at = _now()
        with self.engine.begin() as conn:
            failed = self.runtime_repository.mark_outbox_event_failed(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                event_id=event_id,
                transition=OUTBOX_PUBLISH_FAILED,
                claimed_at=fence,
            )
            if failed is not None:
                dead_letter_id = self._insert_dead_letter(conn, row=failed, ctx=ctx, error=error, failed_at=failed_at)
                self._audit_publish(conn, ctx, failed, status="failed", error=error)
                return dead_letter_id
        return None

    def _insert_dead_letter(
        self,
        conn: TransactionContext,
        *,
        row: RuntimeRow,
        ctx: RequestContext,
        error: Mapping[str, object],
        failed_at: str,
    ) -> str:
        event_id = _row_text(row, "id")
        dead_letter_id = _dead_letter_id(event_id)
        self.runtime_repository.insert_dead_letter_event(
            transaction=conn,
            record=DeadLetterEventRecord(
                event_id=dead_letter_id,
                tenant_id=ctx.tenant_id,
                source_event_id=event_id,
                event_type=_row_text(row, "event_type"),
                payload=_row_payload(row),
                error=error,
                failed_at=failed_at,
                retry_after=None,
            ),
        )
        return dead_letter_id

    def _audit_publish(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        row: RuntimeRow,
        *,
        status: str,
        error: Mapping[str, object] | None = None,
    ) -> None:
        event_id = _row_text(row, "id")
        self.runtime_service._audit(
            conn,
            ctx,
            event_type=f"outbox_event.{status}",
            resource_type="outbox_event",
            resource_id=event_id,
            action="operations:retry",
            before_ref={"eventId": event_id, "eventType": _row_text(row, "event_type")},
            after_ref={"status": status, "error": dict(error or {})},
            correlation_id=_row_text(row, "correlation_id"),
        )


def _lease_now() -> str:
    """Canonical UTC timestamp used for the outbox claim time.

    Both the claim and the staleness cutoff use this UTC ISO-8601 form so the
    repository's lexicographic ``claimed_at < cutoff`` comparison is also a
    chronological comparison regardless of the host timezone. The prior
    local-offset representation broke ordering across hosts in different zones
    and across DST transitions, reclaiming live claims early or never.
    """
    return datetime.now(UTC).isoformat()


def _lease_cutoff() -> str:
    """UTC timestamp before which a publishing claim is considered stale and requeued."""
    return (datetime.now(UTC) - timedelta(seconds=OUTBOX_PUBLISH_LEASE_TIMEOUT_SECONDS)).isoformat()


def _bounded_limit(limit: int) -> int:
    if limit < 1:
        return 1
    return min(limit, MAX_OUTBOX_PUBLISH_BATCH_SIZE)


def _empty_result(*, stream_name: str, requested: int) -> OutboxPublishBatchResult:
    return {
        "status": "completed",
        "streamName": stream_name,
        "requested": requested,
        "published": 0,
        "failed": 0,
        "retrying": 0,
        "skipped": 0,
        "eventIds": [],
        "deadLetterEventIds": [],
    }


def _all_tenant_result(
    *,
    stream_name: str,
    tenant_results: list[OutboxTenantPublishBatchResult],
) -> OutboxPublishAllResult:
    return {
        "status": "completed",
        "streamName": stream_name,
        "requested": sum(result["requested"] for result in tenant_results),
        "published": sum(result["published"] for result in tenant_results),
        "failed": sum(result["failed"] for result in tenant_results),
        "retrying": sum(result["retrying"] for result in tenant_results),
        "skipped": sum(result["skipped"] for result in tenant_results),
        "eventIds": [event_id for result in tenant_results for event_id in result["eventIds"]],
        "deadLetterEventIds": [event_id for result in tenant_results for event_id in result["deadLetterEventIds"]],
        "tenantResults": tenant_results,
    }


def _worker_context(*, tenant_id: str, actor_user_id: str, request_id: str) -> RequestContext:
    return RequestContext(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        request_id=f"{request_id}:tenant:{tenant_id}",
        roles=DEMO_ADMIN_ROLES,
    )


def _stream_request(row: RuntimeRow, *, ctx: RequestContext, stream_name: str) -> StreamPublishRequest:
    event_id = _row_text(row, "id")
    return StreamPublishRequest(
        stream_name=stream_name,
        event_type=_row_text(row, "event_type"),
        tenant_id=ctx.tenant_id,
        request_id=_row_text(row, "correlation_id"),
        key=event_id,
        payload=_stream_payload(row),
    )


def _stream_payload(row: RuntimeRow) -> RuntimeJsonObject:
    return {
        "eventId": _row_text(row, "id"),
        "eventType": _row_text(row, "event_type"),
        "aggregateType": _row_text(row, "aggregate_type"),
        "aggregateId": _row_text(row, "aggregate_id"),
        "idempotencyKey": _row_text(row, "idempotency_key"),
        "correlationId": _row_text(row, "correlation_id"),
        "payload": _row_payload(row),
    }


def _row_payload(row: RuntimeRow) -> RuntimeJsonObject:
    payload = row.get("payload")
    if not isinstance(payload, Mapping):
        return {}
    return {str(key): value for key, value in payload.items()}


def _row_text(row: RuntimeRow, key: str) -> str:
    value = row.get(key)
    return "" if value is None else str(value)


def _row_attempts(row: RuntimeRow) -> int:
    value = row.get("attempts")
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise RuntimeError("outbox_publish_attempts_invalid")
    return value


def _dead_letter_id(event_id: str) -> str:
    return f"dlq_{event_id}" if event_id else _new_id("dlq")
