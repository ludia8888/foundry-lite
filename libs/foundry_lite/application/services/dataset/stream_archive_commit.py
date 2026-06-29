"""Stream archive commit helpers for dataset ingest."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol

from foundry_lite.application.ports import (
    DatasetRow,
    DatasetTransactionRow,
    DeadLetterRecord,
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
from foundry_lite.domain.errors import ConflictDetected, DatasetCommitBlocked, InvariantViolation, ValidationFailed

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
    "commit_stream_archive",
    "ensure_stream_cursor_not_superseded",
    "finalize_stream_archive_commit",
    "insert_stream_dead_letters",
    "lock_stream_cursor_for_commit",
    "record_stream_read_failure",
    "write_stream_archive_batch",
]


class StreamArchiveCommitBoundary(Protocol):
    engine: TransactionManager
    dataset_transaction_repository: DatasetTransactionRepository
    dataset_transaction_service: DatasetTransactionManager
    runtime_service: DatasetRuntimeBoundary

    def _start_connector_sync_run(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset: DatasetRow,
        connector_name: str,
        resource_name: str,
        sync_name: str | None,
        *,
        tx_type: str = "SNAPSHOT",
        source_type: str | None = None,
        run_id: str | None = None,
    ) -> UploadSyncPlan: ...

    def _rows_to_parquet(
        self, rows: Sequence[Mapping[str, object]], target_path: Path, fieldnames: list[str]
    ) -> None: ...

    def _abort_stream_after_error(
        self, ctx: RequestContext, transaction_id: str, run_id: str, exc: Exception
    ) -> None: ...


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


def commit_stream_archive(
    service: StreamArchiveCommitBoundary,
    ctx: RequestContext,
    dataset: DatasetRow,
    stream: StreamArchiveConfig,
    events: Sequence[StreamEvent],
    sync_name: str | None,
    committed_transaction: DatasetTransactionRow | None,
) -> CommitResult:
    with service.engine.begin() as conn:
        plan = service._start_connector_sync_run(
            conn,
            ctx,
            dataset,
            "stream",
            stream.stream_name,
            sync_name or f"stream:{stream.stream_name}:{stream.consumer_group}",
            tx_type="APPEND",
            source_type=f"stream.{stream.stream_name}",
        )
    staged = service.dataset_transaction_service._staging_file(dataset, plan.transaction_id, "part-00000.parquet")
    return write_stream_archive_batch(service, ctx, dataset, stream, plan, staged, events, committed_transaction)


def write_stream_archive_batch(
    service: StreamArchiveCommitBoundary,
    ctx: RequestContext,
    dataset: DatasetRow,
    stream: StreamArchiveConfig,
    plan: UploadSyncPlan,
    staged: Path,
    events: Sequence[StreamEvent],
    committed_transaction: DatasetTransactionRow | None,
) -> CommitResult:
    try:
        committed_metadata = committed_transaction["metadata"] if committed_transaction is not None else {}
        batch = prepare_stream_archive_batch(events, stream, committed_metadata, expected_tenant_id=ctx.tenant_id)
        try:
            ensure_stream_archive_batch_writable(batch, stream, len(events))
        except ValidationFailed:
            persist_stream_dead_letters(service, ctx, dataset, stream, plan, batch.dead_letters)
            raise
        service._rows_to_parquet(batch.rows, staged, stream_archive_fields(stream))
        metadata = stream_commit_metadata(dataset, stream, events, committed_transaction, batch.rows)
        return finalize_stream_archive_commit(
            engine=service.engine,
            repository=service.dataset_transaction_repository,
            transaction_service=service.dataset_transaction_service,
            insert_dead_letters=stream_dead_letter_inserter(service),
            ctx=ctx,
            dataset=dataset,
            stream=stream,
            plan=plan,
            staged=staged,
            dead_letters=batch.dead_letters,
            metadata=metadata,
            events=events,
            committed_transaction=committed_transaction,
        )
    except Exception as exc:
        service._abort_stream_after_error(ctx, plan.transaction_id, plan.run_id, exc)
        raise


def stream_dead_letter_inserter(service: StreamArchiveCommitBoundary) -> InsertStreamDeadLetters:
    def insert(
        conn: TransactionContext,
        insert_ctx: RequestContext,
        insert_dataset: DatasetRow,
        insert_stream: StreamArchiveConfig,
        insert_plan: UploadSyncPlan,
        dead_letters: Sequence[StreamArchiveDeadLetter],
    ) -> None:
        insert_stream_dead_letters(service, conn, insert_ctx, insert_dataset, insert_stream, insert_plan, dead_letters)

    return insert


def persist_stream_dead_letters(
    service: StreamArchiveCommitBoundary,
    ctx: RequestContext,
    dataset: DatasetRow,
    stream: StreamArchiveConfig,
    plan: UploadSyncPlan,
    dead_letters: Sequence[StreamArchiveDeadLetter],
) -> None:
    with service.engine.begin() as conn:
        insert_stream_dead_letters(service, conn, ctx, dataset, stream, plan, dead_letters)


def insert_stream_dead_letters(
    service: StreamArchiveCommitBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    dataset: DatasetRow,
    stream: StreamArchiveConfig,
    plan: UploadSyncPlan,
    dead_letters: Sequence[StreamArchiveDeadLetter],
) -> None:
    failed_at = _now()
    for dead_letter in dead_letters:
        record = stream_dead_letter_record(ctx, dataset, stream, plan, dead_letter, failed_at)
        inserted = service.dataset_transaction_repository.insert_dead_letter_record(transaction=conn, record=record)
        if inserted:
            audit_stream_dead_letter(service, conn, ctx, record)


def audit_stream_dead_letter(
    service: StreamArchiveCommitBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    record: DeadLetterRecord,
) -> None:
    service.runtime_service._audit(
        conn,
        ctx,
        event_type="dead_letter_record.quarantined",
        resource_type="dead_letter_record",
        resource_id=record.dead_letter_record_id,
        action="record_dlq_quarantine",
        after_ref={
            "source_event_id": record.source_event_id,
            "source_run_id": record.source_run_id,
            "status": record.status,
        },
        correlation_id=ctx.request_id,
    )


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
