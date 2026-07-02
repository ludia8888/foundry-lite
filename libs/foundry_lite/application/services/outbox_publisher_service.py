"""Application service helpers for outbox publisher service workflows."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Literal, TypedDict

from foundry_lite.application.ports import (
    OUTBOX_PUBLISH_FAILED,
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
from foundry_lite.domain.context import RequestContext

DEFAULT_OUTBOX_STREAM_NAME = "foundry-lite-outbox"
MAX_OUTBOX_PUBLISH_BATCH_SIZE = 500
# A worker that crashes between claiming an event (pending -> publishing) and marking it
# published/failed leaves the row stranded in publishing forever. Each publish cycle first
# requeues rows whose claim is older than this lease so no event is silently lost. The
# window is generous relative to a single publish so a still-in-flight claim is not reclaimed
# out from under a live worker; downstream idempotency absorbs an at-least-once re-publish.
OUTBOX_PUBLISH_LEASE_TIMEOUT_SECONDS = 300


class OutboxPublishBatchResult(TypedDict):
    status: Literal["completed"]
    streamName: str
    requested: int
    published: int
    failed: int
    skipped: int
    eventIds: list[str]
    deadLetterEventIds: list[str]


class OutboxPublisherService(CoreService):
    """Publish pending runtime outbox rows through the configured stream adapter."""

    required_dependencies = ("engine", "runtime_repository", "stream_adapter")
    required_collaborators = ("runtime_service",)
    runtime_service: RuntimeService

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
        try:
            self.stream_adapter.publish_event(_stream_request(claimed, ctx=ctx, stream_name=stream_name))
        except Exception as exc:  # noqa: BLE001 - failures become durable DLQ evidence for operators.
            dead_letter_id = self._mark_failed(ctx=ctx, row=claimed, exc=exc)
            result["failed"] += 1
            if dead_letter_id is not None:
                result["deadLetterEventIds"].append(dead_letter_id)
            return
        self._mark_published(ctx, claimed)
        result["published"] += 1
        result["eventIds"].append(event_id)

    def _claim_event(self, ctx: RequestContext, event_id: str) -> RuntimeRow | None:
        with self.engine.begin() as conn:
            self.runtime_service._require_outbox_retry_open(conn, ctx)
            return self.runtime_repository.mark_outbox_event_publishing(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                event_id=event_id,
                transition=OUTBOX_PUBLISHING,
                claimed_at=_now(),
            )

    def _mark_published(self, ctx: RequestContext, row: RuntimeRow) -> None:
        event_id = _row_text(row, "id")
        with self.engine.begin() as conn:
            published = self.runtime_repository.mark_outbox_event_published(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                event_id=event_id,
                transition=OUTBOX_PUBLISHED,
                published_at=_now(),
            )
            if published is not None:
                self._audit_publish(conn, ctx, published, status="published")

    def _mark_failed(self, *, ctx: RequestContext, row: RuntimeRow, exc: Exception) -> str | None:
        event_id = _row_text(row, "id")
        failed_at = _now()
        error = self.runtime_service._error_payload(
            exc,
            ctx,
            run_id=event_id,
            correlation_id=_row_text(row, "correlation_id"),
            adapter=self.stream_adapter.profile_name,
        )
        with self.engine.begin() as conn:
            failed = self.runtime_repository.mark_outbox_event_failed(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                event_id=event_id,
                transition=OUTBOX_PUBLISH_FAILED,
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


def _lease_cutoff() -> str:
    """ISO timestamp before which a publishing claim is considered stale and requeued.

    Matches the _now() representation (local-offset ISO 8601) so the repository can compare
    it against stored claimed_at values as ordered strings on the same host.
    """
    return (datetime.now().astimezone() - timedelta(seconds=OUTBOX_PUBLISH_LEASE_TIMEOUT_SECONDS)).isoformat()


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
        "skipped": 0,
        "eventIds": [],
        "deadLetterEventIds": [],
    }


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


def _dead_letter_id(event_id: str) -> str:
    return f"dlq_{event_id}" if event_id else _new_id("dlq")
