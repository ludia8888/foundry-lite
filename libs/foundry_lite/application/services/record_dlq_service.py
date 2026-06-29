from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, cast

from foundry_lite.application.ports import (
    AuditEventRecord,
    DeadLetterRecordBackfillPlan,
    DeadLetterRecordBulkRetryFailure,
    DeadLetterRecordBulkRetryResult,
    DeadLetterRecordDiscardResult,
    DeadLetterRecordRetryResult,
    DeadLetterRecordRow,
    DeadLetterRecordStatus,
    TransactionContext,
)
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.runtime_error_payloads import scrub_error_mapping, scrub_error_text
from foundry_lite.application.services.runtime_restore_gates import require_write_traffic_open
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, FoundryLiteError, NotFound, PermissionDenied, ValidationFailed


class RecordDlqReplayExecutor(Protocol):
    def replay_dead_letter_record(
        self,
        record_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> DeadLetterRecordRow: ...


class RecordDlqService(CoreService):
    """Operations service for input-record DLQ inspection, replay requests, and discard."""

    required_dependencies = ("engine", "policy", "dataset_transaction_repository", "runtime_repository")
    required_collaborators = ("dataset_ingest_service",)
    dataset_ingest_service: RecordDlqReplayExecutor

    def list_dead_letter_records(
        self,
        *,
        ctx: RequestContext | None = None,
        status: str | None = None,
    ) -> list[DeadLetterRecordRow]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "operations:read:summary")
        parsed_status = _optional_status(status)
        with self.engine.begin() as conn:
            rows = self.dataset_transaction_repository.list_dead_letter_records(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                status=parsed_status,
            )
        return cast(list[DeadLetterRecordRow], _redact_sensitive(rows, self.policy.sensitive_column_names(ctx)))

    def dead_letter_record(self, record_id: str, *, ctx: RequestContext | None = None) -> DeadLetterRecordRow:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "operations:read:detail")
        with self.engine.begin() as conn:
            row = self._require_row(conn, ctx, record_id)
        return cast(DeadLetterRecordRow, _redact_sensitive(row, self.policy.sensitive_column_names(ctx)))

    def retry_dead_letter_record(
        self,
        record_id: str,
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> DeadLetterRecordRetryResult:
        ctx = ctx or RequestContext()
        key = _required_idempotency_key(idempotency_key)
        self._require_or_audit(ctx, "operations:retry", "dead_letter_record", record_id)
        self._require_write_open(ctx, operation="retry_dead_letter_record", record_id=record_id)
        with self.engine.begin() as conn:
            row, is_idempotent = self._retry_in_transaction(conn, ctx, record_id, key)
        if is_idempotent:
            return _retry_result(row, is_idempotent_replay=True)
        replayed = self.dataset_ingest_service.replay_dead_letter_record(record_id, ctx=ctx)
        return _retry_result(replayed, is_idempotent_replay=False)

    def bulk_retry_dead_letter_records(
        self,
        record_ids: Sequence[str],
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> DeadLetterRecordBulkRetryResult:
        ctx = ctx or RequestContext()
        if not record_ids:
            raise ValidationFailed("record ids are required for bulk replay")
        key = _required_idempotency_key(idempotency_key)
        self._require_or_audit(ctx, "operations:retry", "dead_letter_record_bulk", "bulk")
        items: list[DeadLetterRecordRetryResult] = []
        failed_items: list[DeadLetterRecordBulkRetryFailure] = []
        for record_id in record_ids:
            try:
                items.append(self.retry_dead_letter_record(record_id, idempotency_key=f"{key}:{record_id}", ctx=ctx))
            except FoundryLiteError as exc:
                failed_items.append(self._record_bulk_retry_item_failure(ctx, record_id, exc))
        return _bulk_retry_result(items, failed_items)

    def discard_dead_letter_record(
        self,
        record_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> DeadLetterRecordDiscardResult:
        ctx = ctx or RequestContext()
        self._require_or_audit(ctx, "operations:retry", "dead_letter_record", record_id)
        self._require_write_open(ctx, operation="discard_dead_letter_record", record_id=record_id)
        with self.engine.begin() as conn:
            row = self._discard_in_transaction(conn, ctx, record_id)
        return _discard_result(row)

    def _retry_in_transaction(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        record_id: str,
        idempotency_key: str,
    ) -> tuple[DeadLetterRecordRow, bool]:
        row = self._require_row(conn, ctx, record_id)
        replay_run_id = row.get("replay_run_id")
        if _is_idempotent_replay(row, idempotency_key, replay_run_id):
            return row, True
        if _is_resumable_replay(row, idempotency_key, replay_run_id):
            return row, False
        _ensure_retryable(row, idempotency_key)
        updated = self._request_replay(conn, ctx, row, idempotency_key)
        if updated is None:
            current = self._require_row(conn, ctx, record_id)
            if _is_idempotent_replay(current, idempotency_key, current.get("replay_run_id")):
                return current, True
            if _is_resumable_replay(current, idempotency_key, current.get("replay_run_id")):
                return current, False
            _ensure_retryable(current, idempotency_key)
            raise ConflictDetected("dead-letter record replay changed concurrently", details={"record_id": record_id})
        self._audit_retry(conn, ctx, row, updated)
        return updated, False

    def _discard_in_transaction(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        record_id: str,
    ) -> DeadLetterRecordRow:
        row = self._require_row(conn, ctx, record_id)
        if row["status"] == "DISCARDED":
            return row
        if row["status"] in {"REPLAY_REQUESTED", "REPLAYING"}:
            raise ConflictDetected("dead-letter record replay is already requested", details={"record_id": record_id})
        discarded_at = _now()
        updated = self.dataset_transaction_repository.discard_dead_letter_record(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            record_id=record_id,
            discarded_at=discarded_at,
            metadata=_operation_metadata(row, ctx, operation="discard", at=discarded_at),
        )
        if updated is None:
            raise NotFound("dead-letter record not found", details={"record_id": record_id})
        self._audit_discard(conn, ctx, row, updated)
        return updated

    def _request_replay(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        row: DeadLetterRecordRow,
        idempotency_key: str,
    ) -> DeadLetterRecordRow | None:
        requested_at = _now()
        replay_run_id = _new_id("sync_run")
        return self.dataset_transaction_repository.update_dead_letter_record_replay_requested(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            record_id=row["id"],
            replay_run_id=replay_run_id,
            replay_idempotency_key=idempotency_key,
            replay_requested_at=requested_at,
            metadata=_operation_metadata(row, ctx, operation="retry", at=requested_at),
            backfill_plan=dict(_backfill_plan(row)),
        )

    def _require_row(self, conn: TransactionContext, ctx: RequestContext, record_id: str) -> DeadLetterRecordRow:
        row = self.dataset_transaction_repository.dead_letter_record_by_id(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            record_id=record_id,
        )
        if row is None:
            raise NotFound("dead-letter record not found", details={"record_id": record_id})
        return row

    def _require_write_open(self, ctx: RequestContext, *, operation: str, record_id: str) -> None:
        require_write_traffic_open(
            self.engine,
            self.runtime_repository,
            ctx,
            operation=operation,
            resource_type="dead_letter_record",
            resource_id=record_id,
        )

    def _require_or_audit(
        self,
        ctx: RequestContext,
        permission: str,
        resource_type: str,
        resource_id: str,
    ) -> None:
        try:
            self.policy.require(ctx, permission)
        except PermissionDenied:
            with self.engine.begin() as conn:
                self._audit(
                    conn,
                    ctx,
                    event_type="permission.denied",
                    resource_type=resource_type,
                    resource_id=resource_id,
                    action=permission,
                    decision="deny",
                    after_ref={"decision": "deny"},
                )
            raise

    def _audit_retry(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        before: DeadLetterRecordRow,
        after: DeadLetterRecordRow,
    ) -> None:
        self._audit(
            conn,
            ctx,
            event_type="dead_letter_record.replay_requested",
            resource_type="dead_letter_record",
            resource_id=before["id"],
            action="operations:retry",
            before_ref={"status": before["status"], "replayStatus": before["replay_status"]},
            after_ref={
                "status": after["status"],
                "replayStatus": after["replay_status"],
                "replayRunId": after["replay_run_id"],
            },
        )

    def _audit_discard(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        before: DeadLetterRecordRow,
        after: DeadLetterRecordRow,
    ) -> None:
        self._audit(
            conn,
            ctx,
            event_type="dead_letter_record.discarded",
            resource_type="dead_letter_record",
            resource_id=before["id"],
            action="operations:retry",
            before_ref={"status": before["status"], "replayStatus": before["replay_status"]},
            after_ref={"status": after["status"], "replayStatus": after["replay_status"]},
        )

    def _audit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        event_type: str,
        resource_type: str,
        resource_id: str | None,
        action: str,
        decision: str = "allow",
        before_ref: Mapping[str, object] | None = None,
        after_ref: Mapping[str, object] | None = None,
    ) -> None:
        self.runtime_repository.insert_audit_event(
            transaction=conn,
            record=AuditEventRecord(
                event_id=_new_id("audit"),
                tenant_id=ctx.tenant_id,
                actor_user_id=ctx.actor_user_id,
                event_type=event_type,
                resource_type=resource_type,
                resource_id=resource_id,
                action=action,
                decision=decision,
                policy_decision={},
                before_ref=dict(before_ref or {}),
                after_ref=dict(after_ref or {}),
                correlation_id=ctx.request_id,
                request_id=ctx.request_id,
                metadata={},
                created_at=_now(),
            ),
        )

    def _record_bulk_retry_item_failure(
        self,
        ctx: RequestContext,
        record_id: str,
        exc: FoundryLiteError,
    ) -> DeadLetterRecordBulkRetryFailure:
        failure = _bulk_retry_failure(record_id, exc)
        with self.engine.begin() as conn:
            self._audit(
                conn,
                ctx,
                event_type="dead_letter_record.bulk_replay_item_failed",
                resource_type="dead_letter_record",
                resource_id=record_id,
                action="operations:retry",
                after_ref=failure,
            )
        return failure


def _optional_status(status: str | None) -> DeadLetterRecordStatus | None:
    if status is None:
        return None
    allowed = {"QUARANTINED", "REPLAY_REQUESTED", "REPLAYING", "RESOLVED", "DISCARDED"}
    if status not in allowed:
        raise ValidationFailed("invalid dead-letter record status", details={"status": status})
    return cast(DeadLetterRecordStatus, status)


def _required_idempotency_key(idempotency_key: str) -> str:
    if not idempotency_key:
        raise ValidationFailed("Idempotency-Key header is required")
    return idempotency_key


def _is_idempotent_replay(row: DeadLetterRecordRow, idempotency_key: str, replay_run_id: str | None) -> bool:
    return (
        row.get("replay_idempotency_key") == idempotency_key
        and replay_run_id is not None
        and row["replay_status"] in {"SUCCEEDED", "FAILED"}
    )


def _is_resumable_replay(row: DeadLetterRecordRow, idempotency_key: str, replay_run_id: str | None) -> bool:
    return (
        row.get("replay_idempotency_key") == idempotency_key
        and replay_run_id is not None
        and row["replay_status"] in {"REQUESTED", "REPLAYING"}
    )


def _ensure_retryable(row: DeadLetterRecordRow, idempotency_key: str) -> None:
    if _record_dlq_kind(row) == "data_quality_contract":
        raise ConflictDetected(
            "data quality quarantine records require contract-specific remediation",
            details={"record_id": row["id"]},
        )
    existing_key = row.get("replay_idempotency_key")
    if existing_key is not None and existing_key != idempotency_key and row["replay_status"] != "FAILED":
        raise ConflictDetected(
            "dead-letter record replay already has a different key",
            details={"record_id": row["id"]},
        )
    if row["status"] in {"RESOLVED", "DISCARDED"}:
        raise ConflictDetected("dead-letter record is already closed", details={"record_id": row["id"]})
    if row["status"] != "QUARANTINED":
        raise ConflictDetected("dead-letter record is not retryable", details={"record_id": row["id"]})


def _record_dlq_kind(row: DeadLetterRecordRow) -> str | None:
    metadata = row.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    kind = metadata.get("recordDlqKind")
    return kind if isinstance(kind, str) else None


def _backfill_plan(row: DeadLetterRecordRow) -> DeadLetterRecordBackfillPlan:
    return {
        "affectedDatasetVersionId": row["source_dataset_version_id"],
        "is_closed_partition_affected": row["is_closed_partition_affected"],
        "nextStep": "Replay worker must re-ingest this record and backfill downstream consumers.",
    }


def _operation_metadata(
    row: DeadLetterRecordRow,
    ctx: RequestContext,
    *,
    operation: str,
    at: str,
) -> Mapping[str, object]:
    metadata = dict(row["metadata"])
    metadata["lastOperation"] = {
        "operation": operation,
        "requestId": ctx.request_id,
        "actorUserId": ctx.actor_user_id,
        "at": at,
    }
    return metadata


def _retry_result(row: DeadLetterRecordRow, *, is_idempotent_replay: bool) -> DeadLetterRecordRetryResult:
    replay_run_id = row.get("replay_run_id")
    if not isinstance(replay_run_id, str):
        raise ValidationFailed("dead-letter record replay run is missing", details={"record_id": row["id"]})
    return {
        "deadLetterRecordId": row["id"],
        "status": row["status"],
        "replayStatus": row["replay_status"],
        "replayRunId": replay_run_id,
        "originDeadLetterRecordId": row["id"],
        "is_idempotent_replay": is_idempotent_replay,
        "replayDatasetVersionId": _replay_dataset_version_id(row),
        "rowCount": _replay_row_count(row),
        "error": _replay_error(row),
        "downstreamBackfillPlan": _backfill_plan(row),
    }


def _replay_dataset_version_id(row: DeadLetterRecordRow) -> str | None:
    result = _metadata_mapping(row, "replayResult")
    value = result.get("datasetVersionId") if result is not None else None
    return value if isinstance(value, str) else None


def _replay_row_count(row: DeadLetterRecordRow) -> int | None:
    result = _metadata_mapping(row, "replayResult")
    value = result.get("rowCount") if result is not None else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _replay_error(row: DeadLetterRecordRow) -> Mapping[str, object] | None:
    return _metadata_mapping(row, "lastReplayError")


def _metadata_mapping(row: DeadLetterRecordRow, key: str) -> Mapping[str, object] | None:
    value = row["metadata"].get(key)
    return value if isinstance(value, Mapping) else None


def _discard_result(row: DeadLetterRecordRow) -> DeadLetterRecordDiscardResult:
    discarded_at = row.get("discarded_at")
    if not isinstance(discarded_at, str):
        raise ValidationFailed("dead-letter record discard timestamp is missing", details={"record_id": row["id"]})
    return {
        "deadLetterRecordId": row["id"],
        "status": row["status"],
        "replayStatus": row["replay_status"],
        "discardedAt": discarded_at,
    }


def _bulk_retry_result(
    items: list[DeadLetterRecordRetryResult],
    failed_items: list[DeadLetterRecordBulkRetryFailure],
) -> DeadLetterRecordBulkRetryResult:
    return {
        "status": _bulk_retry_status(items, failed_items),
        "items": items,
        "failedItems": failed_items,
        "succeededCount": len(items),
        "failedCount": len(failed_items),
    }


def _bulk_retry_status(
    items: list[DeadLetterRecordRetryResult],
    failed_items: list[DeadLetterRecordBulkRetryFailure],
) -> str:
    if failed_items and items:
        return "PARTIAL_SUCCESS"
    return "FAILED" if failed_items else "SUCCEEDED"


def _bulk_retry_failure(record_id: str, exc: FoundryLiteError) -> DeadLetterRecordBulkRetryFailure:
    return {
        "deadLetterRecordId": record_id,
        "code": exc.code,
        "message": scrub_error_text(exc.message),
        "details": scrub_error_mapping(exc.details),
    }


def _redact_sensitive(value: object, sensitive: set[str]) -> object:
    if isinstance(value, Mapping):
        return {
            key: "***MASKED***" if key in sensitive else _redact_sensitive(item, sensitive)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive(item, sensitive) for item in value]
    return value
