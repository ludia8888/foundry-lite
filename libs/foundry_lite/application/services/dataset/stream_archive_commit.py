from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from foundry_lite.application.ports import (
    DatasetRow,
    DatasetTransactionRow,
    StreamArchiveConfig,
    StreamEvent,
    SyncRunRecord,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.ports.dataset_transaction_repository import DatasetTransactionRepository
from foundry_lite.application.primitives import CommitResult, _new_id, _now
from foundry_lite.application.services.dataset.ingest_models import UploadSyncPlan
from foundry_lite.application.services.dataset.late_data_reprocessing import stream_commit_metadata
from foundry_lite.application.services.dataset.protocols import (
    DatasetCommitMetadataHook,
    DatasetRuntimeBoundary,
    DatasetTransactionManager,
    mark_sync_run_committed,
)
from foundry_lite.application.services.dataset.stream_archive import (
    StreamArchiveDeadLetter,
    ensure_stream_archive_batch_writable,
    prepare_stream_archive_batch,
    read_stream_archive_events,
    stream_archive_fields,
    stream_cursor_offset,
    stream_dead_letter_record,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, DatasetCommitBlocked, InvariantViolation

InsertStreamDeadLetters = Callable[
    [
        TransactionContext,
        RequestContext,
        DatasetRow,
        StreamArchiveConfig,
        UploadSyncPlan,
        Sequence[StreamArchiveDeadLetter],
    ],
    None,
]

__all__ = [
    "StreamArchiveDeadLetter",
    "ensure_stream_archive_batch_writable",
    "prepare_stream_archive_batch",
    "read_stream_archive_events",
    "stream_archive_fields",
    "stream_commit_metadata",
    "stream_cursor_offset",
    "stream_dead_letter_record",
    "ensure_stream_cursor_not_superseded",
    "finalize_stream_archive_commit",
    "lock_stream_cursor_for_commit",
    "record_stream_read_failure",
]


def finalize_stream_archive_commit(
    *,
    engine: TransactionManager,
    repository: DatasetTransactionRepository,
    transaction_service: DatasetTransactionManager,
    insert_dead_letters: InsertStreamDeadLetters,
    ctx: RequestContext,
    dataset: DatasetRow,
    stream: StreamArchiveConfig,
    plan: UploadSyncPlan,
    staged: Path,
    dead_letters: Sequence[StreamArchiveDeadLetter],
    metadata: Mapping[str, object],
    events: Sequence[StreamEvent],
    committed_transaction: DatasetTransactionRow | None,
) -> CommitResult:
    """Persist a stream archive commit only if its resume cursor is still current."""
    blocked: DatasetCommitBlocked | None = None
    with engine.begin() as conn:
        lock_stream_cursor_for_commit(
            repository=repository,
            conn=conn,
            ctx=ctx,
            dataset=dataset,
            stream=stream,
            events=events,
            committed_transaction=committed_transaction,
        )
        insert_dead_letters(conn, ctx, dataset, stream, plan, dead_letters)
        try:
            return _finalize_locked_stream_transaction(
                transaction_service, repository, conn, ctx, dataset, plan, staged, metadata
            )
        except DatasetCommitBlocked as exc:
            blocked = exc
    if blocked is not None:
        raise blocked
    raise InvariantViolation("stream archive finalization did not return a commit result")


def _finalize_locked_stream_transaction(
    transaction_service: DatasetTransactionManager,
    repository: DatasetTransactionRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    dataset: DatasetRow,
    plan: UploadSyncPlan,
    staged: Path,
    metadata: Mapping[str, object],
) -> CommitResult:
    return transaction_service._finalize_open_transaction(
        conn,
        ctx,
        dataset=dataset,
        transaction_id=plan.transaction_id,
        staged_parquet=staged,
        run_id=plan.run_id,
        audit_action="stream_archive_append_commit",
        outbox_event_type="dataset.version.committed",
        transaction_metadata=metadata,
        after_persist=_sync_run_commit_hook(repository, ctx, plan.run_id),
    )


def _sync_run_commit_hook(
    repository: DatasetTransactionRepository,
    ctx: RequestContext,
    run_id: str,
) -> DatasetCommitMetadataHook:
    def hook(commit_conn: TransactionContext, result: CommitResult) -> None:
        mark_sync_run_committed(repository, commit_conn, ctx, run_id, result)

    return hook


def lock_stream_cursor_for_commit(
    *,
    repository: DatasetTransactionRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    dataset: DatasetRow,
    stream: StreamArchiveConfig,
    events: Sequence[StreamEvent],
    committed_transaction: DatasetTransactionRow | None,
) -> None:
    """Lock the dataset head and reject stale stream cursor append attempts."""
    repository.lock_dataset_for_version_allocation(transaction=conn, tenant_id=ctx.tenant_id, dataset_id=dataset["id"])
    ensure_stream_cursor_not_superseded(
        repository=repository,
        conn=conn,
        ctx=ctx,
        dataset=dataset,
        stream=stream,
        events=events,
        committed_transaction=committed_transaction,
    )


def ensure_stream_cursor_not_superseded(
    *,
    repository: DatasetTransactionRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    dataset: DatasetRow,
    stream: StreamArchiveConfig,
    events: Sequence[StreamEvent],
    committed_transaction: DatasetTransactionRow | None,
) -> None:
    """Reject a stream append if another worker advanced the durable cursor."""
    latest = repository.latest_committed_transaction(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        dataset_id=dataset["id"],
    )
    previous_id = committed_transaction["id"] if committed_transaction is not None else None
    latest_id = latest["id"] if latest is not None else None
    if latest_id == previous_id:
        return
    previous_metadata = committed_transaction["metadata"] if committed_transaction is not None else {}
    latest_metadata = latest["metadata"] if latest is not None else {}
    raise ConflictDetected(
        "stream archive cursor advanced during commit",
        details={
            "dataset_id": dataset["id"],
            "stream": stream.stream_name,
            "topic": stream.topic,
            "partition": stream.partition,
            "consumer_group": stream.consumer_group,
            "expected_previous_transaction_id": previous_id,
            "current_transaction_id": latest_id,
            "expected_previous_offset": stream_cursor_offset(previous_metadata, stream),
            "current_offset": stream_cursor_offset(latest_metadata, stream),
            "batch_first_offset": events[0].offset,
            "batch_last_offset": events[-1].offset,
        },
    )


def record_stream_read_failure(
    engine: TransactionManager,
    repository: DatasetTransactionRepository,
    runtime_service: DatasetRuntimeBoundary,
    ctx: RequestContext,
    dataset: DatasetRow,
    stream: StreamArchiveConfig,
    sync_name: str | None,
    exc: Exception,
) -> None:
    run_id = _new_id("sync_run")
    error = runtime_service._error_payload(exc, ctx, run_id=run_id, adapter="stream_archive_reader")
    now = _now()
    with engine.begin() as conn:
        repository.insert_sync_run(
            transaction=conn,
            record=SyncRunRecord(
                sync_run_id=run_id,
                tenant_id=ctx.tenant_id,
                sync_name=sync_name or f"stream:{stream.stream_name}:{stream.consumer_group}",
                source_type=f"stream.{stream.stream_name}",
                output_dataset_id=str(dataset["id"]),
                transaction_id=None,
                committed_version_id=None,
                status="FAILED",
                error=error,
                created_at=now,
                completed_at=now,
            ),
        )
