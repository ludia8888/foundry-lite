"""Application service helpers for transform record dlq replay workflows."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from foundry_lite.application.ports import (
    DatasetTransactionRepository,
    DeadLetterRecordRow,
    TransactionContext,
    TransactionManager,
    TransformExecutionResult,
    TransformRecordDlqRetryResult,
    TransformRepository,
)
from foundry_lite.application.primitives import CommitResult, _new_id, _now
from foundry_lite.application.services.transform_protocols import (
    TransformDatasetRegistry,
    TransformDatasetTransactions,
    TransformDatasetVersions,
    TransformRuntimeBoundary,
)
from foundry_lite.application.services.transform_record_dlq import (
    TRANSFORM_RECORD_DLQ_KIND,
)
from foundry_lite.application.services.transform_record_dlq_replay_helpers import (
    _abort_plan,
    _backfill_plan,
    _failure_metadata,
    _is_terminal_idempotent,
    _lease_expires_at,
    _lease_metadata,
    _lease_token,
    _optional_lease_token,
    _origin_transform_run_id,
    _record_kind,
    _replay_run_id,
    _request_metadata,
    _required_idempotency_key,
    _retry_result,
    _row_from_result,
    _safe_error_message,
    _success_metadata,
    _transform_api_name,
    _transform_mode,
)
from foundry_lite.application.services.transform_runs import (
    TransformRunPlan,
    start_transform_run,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, ValidationFailed

TransformCommitHook = Callable[[TransactionContext, CommitResult], None]


class TransformRecordDlqReplayRuntime(Protocol):
    engine: TransactionManager
    dataset_transaction_repository: DatasetTransactionRepository
    dataset_registry_service: TransformDatasetRegistry
    dataset_transaction_service: TransformDatasetTransactions
    dataset_version_service: TransformDatasetVersions
    runtime_service: TransformRuntimeBoundary
    transform_repository: TransformRepository

    def _execute_transform_plan(self, plan: TransformRunPlan, staged: Path) -> TransformExecutionResult: ...

    def _finalize_executed_transform(
        self,
        ctx: RequestContext,
        plan: TransformRunPlan,
        staged: Path,
        execution: TransformExecutionResult,
        dead_letter_ids: Sequence[str],
        *,
        should_audit_retry: bool = False,
        after_transform_commit: TransformCommitHook | None = None,
    ) -> CommitResult: ...

    def _abort_transform_run(self, ctx: RequestContext, plan: TransformRunPlan, exc: Exception) -> None: ...


@dataclass(frozen=True)
class TransformRecordDlqReplayStart:
    row: DeadLetterRecordRow
    is_terminal_replay: bool


def retry_transform_record_dlq(
    runtime: TransformRecordDlqReplayRuntime,
    record_id: str,
    *,
    idempotency_key: str,
    ctx: RequestContext,
) -> TransformRecordDlqRetryResult:
    start = _request_transform_replay(runtime, record_id, _required_idempotency_key(idempotency_key), ctx)
    if start.is_terminal_replay:
        return _retry_result(start.row, is_idempotent_replay=True)
    if start.row["replay_status"] == "FAILED":
        return _retry_result(start.row, is_idempotent_replay=False)
    with runtime.engine.begin() as conn:
        claimed = _claim_replay(runtime, conn, ctx, start.row)
    return _execute_replay(runtime, ctx, claimed)


def _request_transform_replay(
    runtime: TransformRecordDlqReplayRuntime,
    record_id: str,
    idempotency_key: str,
    ctx: RequestContext,
) -> TransformRecordDlqReplayStart:
    with runtime.engine.begin() as conn:
        row = _require_transform_row(runtime, conn, ctx, record_id)
        if _is_terminal_idempotent(row, idempotency_key):
            return TransformRecordDlqReplayStart(row, True)
        _ensure_requestable(row, idempotency_key)
        requested = _mark_requested(runtime, conn, ctx, row, idempotency_key)
        _audit_requested(runtime, conn, ctx, row, requested)
    try:
        return _fail_unsupported_mode(runtime, ctx, requested) or TransformRecordDlqReplayStart(requested, False)
    except Exception as exc:  # noqa: BLE001 - prepare failures must close the requested replay row.
        failed = _record_failure(runtime, ctx, requested, exc, None)
        return TransformRecordDlqReplayStart(_row_from_result(runtime, ctx, failed), False)


def _execute_replay(
    runtime: TransformRecordDlqReplayRuntime,
    ctx: RequestContext,
    row: DeadLetterRecordRow,
) -> TransformRecordDlqRetryResult:
    plan: TransformRunPlan | None = None
    try:
        plan = _open_current_transform_run(runtime, ctx, row)
        staged = runtime.dataset_transaction_service._staging_file(
            plan.output_dataset, plan.transaction_id, "part-00000.parquet"
        )
        execution = runtime._execute_transform_plan(plan, staged)
        _reject_replayed_dead_letters(runtime, ctx, plan, execution)
        return _finalize_success(runtime, ctx, row, plan, staged, execution)
    except Exception as exc:  # noqa: BLE001 - replay failures are converted into durable DLQ evidence.
        return _record_failure(runtime, ctx, row, exc, plan)


def _open_current_transform_run(
    runtime: TransformRecordDlqReplayRuntime,
    ctx: RequestContext,
    row: DeadLetterRecordRow,
) -> TransformRunPlan:
    with runtime.engine.begin() as conn:
        return start_transform_run(
            conn=conn,
            ctx=ctx,
            api_name=_transform_api_name(row),
            dataset_registry_service=runtime.dataset_registry_service,
            dataset_transaction_service=runtime.dataset_transaction_service,
            dataset_version_service=runtime.dataset_version_service,
            transform_repository=runtime.transform_repository,
        )


def _reject_replayed_dead_letters(
    runtime: TransformRecordDlqReplayRuntime,
    ctx: RequestContext,
    plan: TransformRunPlan,
    execution: TransformExecutionResult,
) -> None:
    if not execution.dead_letters:
        return
    exc = ValidationFailed(
        "transform DLQ replay produced new row quarantines",
        details={"transform_run_id": plan.run_id, "dead_letter_count": len(execution.dead_letters)},
    )
    _abort_plan(runtime, ctx, plan, exc)
    raise exc


def _finalize_success(
    runtime: TransformRecordDlqReplayRuntime,
    ctx: RequestContext,
    row: DeadLetterRecordRow,
    plan: TransformRunPlan,
    staged: Path,
    execution: TransformExecutionResult,
) -> TransformRecordDlqRetryResult:
    updated: DeadLetterRecordRow | None = None

    def after_commit(conn: TransactionContext, result: CommitResult) -> None:
        nonlocal updated
        updated = _mark_success(runtime, conn, ctx, row, plan, result)
        _audit_success(runtime, conn, ctx, row, plan, result)

    runtime._finalize_executed_transform(ctx, plan, staged, execution, (), after_transform_commit=after_commit)
    if updated is None:
        raise ValidationFailed("transform DLQ replay did not close the dead-letter record")
    return _retry_result(updated, is_idempotent_replay=False)


def _mark_success(
    runtime: TransformRecordDlqReplayRuntime,
    conn: TransactionContext,
    ctx: RequestContext,
    row: DeadLetterRecordRow,
    plan: TransformRunPlan,
    result: CommitResult,
) -> DeadLetterRecordRow:
    updated = runtime.dataset_transaction_repository.update_dead_letter_record_replay_succeeded(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        record_id=row["id"],
        replay_run_id=_replay_run_id(row),
        replay_lease_token=_lease_token(row),
        metadata=_success_metadata(row, ctx, plan, result),
    )
    if updated is None:
        raise NotFound("dead-letter record not found", details={"record_id": row["id"]})
    return updated


def _record_failure(
    runtime: TransformRecordDlqReplayRuntime,
    ctx: RequestContext,
    row: DeadLetterRecordRow,
    exc: Exception,
    plan: TransformRunPlan | None,
) -> TransformRecordDlqRetryResult:
    if plan is not None:
        _abort_plan(runtime, ctx, plan, exc)
    with runtime.engine.begin() as conn:
        updated = runtime.dataset_transaction_repository.update_dead_letter_record_replay_failed(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            record_id=row["id"],
            replay_run_id=_replay_run_id(row),
            replay_lease_token=_optional_lease_token(row),
            metadata=_failure_metadata(row, ctx, exc, plan),
        )
        if updated is None:
            raise NotFound("dead-letter record not found", details={"record_id": row["id"]})
        _audit_failure(runtime, conn, ctx, row, exc, plan)
    return _retry_result(updated, is_idempotent_replay=False)


def _fail_unsupported_mode(
    runtime: TransformRecordDlqReplayRuntime,
    ctx: RequestContext,
    row: DeadLetterRecordRow,
) -> TransformRecordDlqReplayStart | None:
    mode = _transform_mode(runtime, ctx, row)
    if mode == "snapshot":
        return None
    exc = ValidationFailed("append/incremental transform DLQ replay requires a duplicate-safe merge policy")
    failed = _record_failure(runtime, ctx, row, exc, None)
    return TransformRecordDlqReplayStart(_row_from_result(runtime, ctx, failed), False)


def _mark_requested(
    runtime: TransformRecordDlqReplayRuntime,
    conn: TransactionContext,
    ctx: RequestContext,
    row: DeadLetterRecordRow,
    idempotency_key: str,
) -> DeadLetterRecordRow:
    replay_run_id = _new_id("transform_dlq_replay")
    requested = runtime.dataset_transaction_repository.update_dead_letter_record_replay_requested(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        record_id=row["id"],
        replay_run_id=replay_run_id,
        replay_idempotency_key=idempotency_key,
        replay_requested_at=_now(),
        metadata=_request_metadata(row, ctx),
        backfill_plan=_backfill_plan(row),
    )
    if requested is None:
        raise ConflictDetected("dead-letter record replay changed concurrently", details={"record_id": row["id"]})
    return requested


def _claim_replay(
    runtime: TransformRecordDlqReplayRuntime,
    conn: TransactionContext,
    ctx: RequestContext,
    row: DeadLetterRecordRow,
) -> DeadLetterRecordRow:
    token = _new_id("dlq_lease")
    started_at = _now()
    claimed = runtime.dataset_transaction_repository.claim_dead_letter_record_replay(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        record_id=row["id"],
        replay_run_id=_replay_run_id(row),
        replay_lease_token=token,
        replay_started_at=started_at,
        replay_lease_expires_at=_lease_expires_at(),
        metadata=_lease_metadata(row, ctx, token, started_at),
    )
    if claimed is None:
        raise ConflictDetected("transform DLQ replay is already running", details={"record_id": row["id"]})
    return claimed


def _require_transform_row(
    runtime: TransformRecordDlqReplayRuntime,
    conn: TransactionContext,
    ctx: RequestContext,
    record_id: str,
) -> DeadLetterRecordRow:
    row = runtime.dataset_transaction_repository.dead_letter_record_by_id(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        record_id=record_id,
    )
    if row is None:
        raise NotFound("dead-letter record not found", details={"record_id": record_id})
    if _record_kind(row) != TRANSFORM_RECORD_DLQ_KIND:
        raise ConflictDetected("dead-letter record is not a transform quarantine", details={"record_id": record_id})
    return row


def _ensure_requestable(row: DeadLetterRecordRow, idempotency_key: str) -> None:
    existing_key = row.get("replay_idempotency_key")
    if existing_key is not None and existing_key != idempotency_key and row["replay_status"] != "FAILED":
        raise ConflictDetected("transform DLQ replay already has a different key", details={"record_id": row["id"]})
    if row["status"] in {"RESOLVED", "DISCARDED"}:
        raise ConflictDetected("dead-letter record is already closed", details={"record_id": row["id"]})
    if row["status"] != "QUARANTINED":
        raise ConflictDetected("dead-letter record replay is already requested", details={"record_id": row["id"]})


def _audit_requested(
    runtime: TransformRecordDlqReplayRuntime,
    conn: TransactionContext,
    ctx: RequestContext,
    before: DeadLetterRecordRow,
    after: DeadLetterRecordRow,
) -> None:
    runtime.runtime_service._audit(
        conn,
        ctx,
        event_type="dead_letter_record.transform_replay_requested",
        resource_type="dead_letter_record",
        resource_id=before["id"],
        action="operations:retry",
        before_ref={"status": before["status"], "replayStatus": before["replay_status"]},
        after_ref={
            "status": after["status"],
            "replayStatus": after["replay_status"],
            "replayRunId": _replay_run_id(after),
        },
        correlation_id=_replay_run_id(after),
    )


def _audit_success(
    runtime: TransformRecordDlqReplayRuntime,
    conn: TransactionContext,
    ctx: RequestContext,
    row: DeadLetterRecordRow,
    plan: TransformRunPlan,
    result: CommitResult,
) -> None:
    runtime.runtime_service._audit(
        conn,
        ctx,
        event_type="dead_letter_record.transform_replayed",
        resource_type="dead_letter_record",
        resource_id=row["id"],
        action="operations:retry",
        after_ref={"transformRunId": plan.run_id, "versionId": result.version_id},
        correlation_id=plan.run_id,
    )
    runtime.runtime_service._run_relation(
        conn,
        ctx,
        source_run_type="transform",
        source_run_id=_origin_transform_run_id(row),
        target_run_type="transform",
        target_run_id=plan.run_id,
        relation="record_dlq_replay_of",
        resource_type="dead_letter_record",
        resource_id=row["id"],
        metadata={"replayDatasetVersionId": result.version_id},
    )


def _audit_failure(
    runtime: TransformRecordDlqReplayRuntime,
    conn: TransactionContext,
    ctx: RequestContext,
    row: DeadLetterRecordRow,
    exc: Exception,
    plan: TransformRunPlan | None,
) -> None:
    runtime.runtime_service._audit(
        conn,
        ctx,
        event_type="dead_letter_record.transform_replay_failed",
        resource_type="dead_letter_record",
        resource_id=row["id"],
        action="operations:retry",
        after_ref={"error": _safe_error_message(exc), "transformRunId": plan.run_id if plan is not None else None},
        correlation_id=plan.run_id if plan is not None else _replay_run_id(row),
    )
