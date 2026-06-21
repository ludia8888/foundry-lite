from __future__ import annotations

from foundry_lite.application.ports import DatasetRow, StreamArchiveConfig, SyncRunRecord, TransactionManager
from foundry_lite.application.ports.dataset_transaction_repository import DatasetTransactionRepository
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.dataset.late_data_reprocessing import stream_commit_metadata
from foundry_lite.application.services.dataset.protocols import DatasetRuntimeBoundary
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

__all__ = [
    "StreamArchiveDeadLetter",
    "ensure_stream_archive_batch_writable",
    "prepare_stream_archive_batch",
    "read_stream_archive_events",
    "stream_archive_fields",
    "stream_commit_metadata",
    "stream_cursor_offset",
    "stream_dead_letter_record",
    "record_stream_read_failure",
]


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
