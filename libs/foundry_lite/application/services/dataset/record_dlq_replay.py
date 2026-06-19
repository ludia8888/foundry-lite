from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NoReturn, Protocol, cast

from foundry_lite.application.ports import (
    DatasetRow,
    DatasetTransactionRepository,
    DeadLetterRecordRow,
    StreamArchiveConfig,
    StreamEvent,
    StreamSchemaStrategy,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.primitives import CommitResult
from foundry_lite.application.services.dataset.ingest_models import UploadSyncPlan
from foundry_lite.application.services.dataset.protocols import (
    DatasetRegistryLookup,
    DatasetRuntimeBoundary,
    DatasetTransactionManager,
)
from foundry_lite.application.services.dataset.stream_archive import (
    stream_archive_fields,
    stream_event_row,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed


class RecordDlqReplayRuntime(Protocol):
    dataset_transaction_repository: DatasetTransactionRepository
    dataset_registry_service: DatasetRegistryLookup
    dataset_transaction_service: DatasetTransactionManager
    runtime_service: DatasetRuntimeBoundary
    engine: TransactionManager

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

    def _mark_sync_run_committed(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run_id: str,
        result: CommitResult,
    ) -> None: ...

    def _abort_stream_after_error(
        self,
        ctx: RequestContext,
        transaction_id: str,
        run_id: str,
        exc: Exception,
    ) -> NoReturn: ...


def replay_dead_letter_record(
    runtime: RecordDlqReplayRuntime,
    record_id: str,
    *,
    ctx: RequestContext,
) -> DeadLetterRecordRow:
    with runtime.engine.begin() as conn:
        row = _require_row(runtime, conn, ctx, record_id)
    sync = _prepare_replay_sync(runtime, ctx, row)
    return _execute_replay_sync(runtime, ctx, row, sync)


def _prepare_replay_sync(
    runtime: RecordDlqReplayRuntime,
    ctx: RequestContext,
    row: DeadLetterRecordRow,
) -> tuple[DatasetRow, StreamArchiveConfig, UploadSyncPlan, Path]:
    stream = _stream_config(row)
    dataset = runtime.dataset_registry_service.get_dataset(_dataset_ref(row), ctx=ctx)
    replay_run_id = _replay_run_id(row)
    with runtime.engine.begin() as conn:
        plan = runtime._start_connector_sync_run(
            conn,
            ctx,
            dataset,
            "record_dlq",
            stream.stream_name,
            f"record-dlq:{stream.stream_name}:{row['id']}",
            tx_type="APPEND",
            source_type=f"record_dlq.{stream.stream_name}",
            run_id=replay_run_id,
        )
    staged = runtime.dataset_transaction_service._staging_file(dataset, plan.transaction_id, "part-00000.parquet")
    return dataset, stream, plan, staged


def _execute_replay_sync(
    runtime: RecordDlqReplayRuntime,
    ctx: RequestContext,
    row: DeadLetterRecordRow,
    sync: tuple[DatasetRow, StreamArchiveConfig, UploadSyncPlan, Path],
) -> DeadLetterRecordRow:
    dataset, stream, plan, staged = sync
    try:
        replay_row = stream_event_row(_stream_event(row, ctx, stream), stream)
        runtime._rows_to_parquet([replay_row], staged, stream_archive_fields(stream))
        return _finalize_replay(runtime, ctx, row, dataset, stream, plan, staged)
    except Exception as exc:
        return _record_replay_failure(runtime, ctx, row, plan, exc)


def _finalize_replay(
    runtime: RecordDlqReplayRuntime,
    ctx: RequestContext,
    row: DeadLetterRecordRow,
    dataset: DatasetRow,
    stream: StreamArchiveConfig,
    plan: UploadSyncPlan,
    staged: Path,
) -> DeadLetterRecordRow:
    with runtime.engine.begin() as conn:
        result = runtime.dataset_transaction_service._finalize_open_transaction(
            conn,
            ctx,
            dataset=dataset,
            transaction_id=plan.transaction_id,
            staged_parquet=staged,
            run_id=plan.run_id,
            audit_action="record_dlq_replay_commit",
            outbox_event_type="dataset.version.committed",
            transaction_metadata=_replay_transaction_metadata(runtime, conn, ctx, row, dataset, stream, plan),
        )
        runtime._mark_sync_run_committed(conn, ctx, plan.run_id, result)
        updated = _mark_replay_succeeded(runtime, conn, ctx, row, plan, result)
        _audit_replay_succeeded(runtime, conn, ctx, row, plan, result)
        return updated


def _mark_replay_succeeded(
    runtime: RecordDlqReplayRuntime,
    conn: TransactionContext,
    ctx: RequestContext,
    row: DeadLetterRecordRow,
    plan: UploadSyncPlan,
    result: CommitResult,
) -> DeadLetterRecordRow:
    updated = runtime.dataset_transaction_repository.update_dead_letter_record_replay_succeeded(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        record_id=row["id"],
        replay_run_id=plan.run_id,
        metadata=_success_metadata(row, ctx, result),
    )
    if updated is None:
        raise NotFound("dead-letter record not found", details={"record_id": row["id"]})
    return updated


def _audit_replay_succeeded(
    runtime: RecordDlqReplayRuntime,
    conn: TransactionContext,
    ctx: RequestContext,
    row: DeadLetterRecordRow,
    plan: UploadSyncPlan,
    result: CommitResult,
) -> None:
    runtime.runtime_service._audit(
        conn,
        ctx,
        event_type="dead_letter_record.replayed",
        resource_type="dead_letter_record",
        resource_id=row["id"],
        action="operations:retry",
        after_ref={"versionId": result.version_id, "replayRunId": plan.run_id},
        correlation_id=plan.run_id,
    )


def _record_replay_failure(
    runtime: RecordDlqReplayRuntime,
    ctx: RequestContext,
    row: DeadLetterRecordRow,
    plan: UploadSyncPlan,
    exc: Exception,
) -> DeadLetterRecordRow:
    _abort_replay_transaction(runtime, ctx, plan, exc)
    with runtime.engine.begin() as conn:
        updated = runtime.dataset_transaction_repository.update_dead_letter_record_replay_failed(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            record_id=row["id"],
            replay_run_id=plan.run_id,
            metadata=_failure_metadata(row, ctx, exc),
        )
        if updated is None:
            raise NotFound("dead-letter record not found", details={"record_id": row["id"]})
        runtime.runtime_service._audit(
            conn,
            ctx,
            event_type="dead_letter_record.replay_failed",
            resource_type="dead_letter_record",
            resource_id=row["id"],
            action="operations:retry",
            after_ref={"error": str(exc), "replayRunId": plan.run_id},
            correlation_id=plan.run_id,
        )
        return updated


def _abort_replay_transaction(
    runtime: RecordDlqReplayRuntime,
    ctx: RequestContext,
    plan: UploadSyncPlan,
    exc: Exception,
) -> None:
    try:
        runtime._abort_stream_after_error(ctx, plan.transaction_id, plan.run_id, exc)
    except ValidationFailed:
        return


def _require_row(
    runtime: RecordDlqReplayRuntime,
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
    return row


def _replay_transaction_metadata(
    runtime: RecordDlqReplayRuntime,
    conn: TransactionContext,
    ctx: RequestContext,
    row: DeadLetterRecordRow,
    dataset: DatasetRow,
    stream: StreamArchiveConfig,
    plan: UploadSyncPlan,
) -> Mapping[str, object]:
    latest = runtime.dataset_transaction_repository.latest_committed_transaction(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        dataset_id=dataset["id"],
    )
    metadata = dict(latest["metadata"]) if latest is not None else {}
    metadata["recordDlqReplay"] = {
        "deadLetterRecordId": row["id"],
        "originSourceEventId": row["source_event_id"],
        "replayRunId": plan.run_id,
        "stream": stream.stream_name,
    }
    return metadata


def _stream_event(row: DeadLetterRecordRow, ctx: RequestContext, stream: StreamArchiveConfig) -> StreamEvent:
    metadata = row["metadata"]
    return StreamEvent(
        stream_name=stream.stream_name,
        offset=_int_metadata(metadata, "offset"),
        event_type=_str_metadata(metadata, "event_type"),
        tenant_id=ctx.tenant_id,
        request_id=ctx.request_id,
        key=_str_metadata(metadata, "event_key"),
        payload=row["payload"],
    )


def _stream_config(row: DeadLetterRecordRow) -> StreamArchiveConfig:
    metadata = row["metadata"]
    return StreamArchiveConfig(
        stream_name=_str_metadata(metadata, "stream"),
        topic=_str_metadata(metadata, "topic"),
        consumer_group=_str_metadata(metadata, "consumer_group", default="foundry-lite-archive"),
        partition=_int_metadata(metadata, "partition"),
        schema_strategy=_schema_strategy(metadata),
        event_time_field=_str_metadata(metadata, "event_time_field", default="event_time"),
        source_time_field=_str_metadata(metadata, "source_time_field", default="source_time"),
        time_zone=_optional_str_metadata(metadata, "time_zone"),
        allowed_lateness_seconds=_int_metadata(metadata, "allowed_lateness_seconds", default=172800),
        too_late_after_seconds=_int_metadata(metadata, "too_late_after_seconds", default=2592000),
        clock_skew_seconds=_int_metadata(metadata, "clock_skew_seconds", default=300),
    )


def _dataset_ref(row: DeadLetterRecordRow) -> str:
    return _str_metadata(row["metadata"], "dataset_ref")


def _replay_run_id(row: DeadLetterRecordRow) -> str:
    replay_run_id = row.get("replay_run_id")
    if not isinstance(replay_run_id, str) or not replay_run_id:
        raise ValidationFailed("dead-letter record replay run is missing", details={"record_id": row["id"]})
    return replay_run_id


def _success_metadata(row: DeadLetterRecordRow, ctx: RequestContext, result: CommitResult) -> Mapping[str, object]:
    metadata = dict(row["metadata"])
    metadata["lastOperation"] = _operation(ctx, "replay_succeeded")
    metadata["replayResult"] = {
        "datasetVersionId": result.version_id,
        "transactionId": result.transaction_id,
        "rowCount": result.row_count,
    }
    return metadata


def _failure_metadata(row: DeadLetterRecordRow, ctx: RequestContext, exc: Exception) -> Mapping[str, object]:
    metadata = dict(row["metadata"])
    metadata["lastOperation"] = _operation(ctx, "replay_failed")
    metadata["lastReplayError"] = {"type": exc.__class__.__name__, "message": str(exc)}
    return metadata


def _operation(ctx: RequestContext, operation: str) -> Mapping[str, object]:
    return {"operation": operation, "requestId": ctx.request_id, "actorUserId": ctx.actor_user_id}


def _str_metadata(metadata: Mapping[str, object], key: str, *, default: str | None = None) -> str:
    value = metadata.get(key, default)
    if isinstance(value, str) and value:
        return value
    raise ValidationFailed("dead-letter record metadata is missing a string", details={"field": key})


def _optional_str_metadata(metadata: Mapping[str, object], key: str) -> str | None:
    value = metadata.get(key)
    return value if isinstance(value, str) and value else None


def _int_metadata(metadata: Mapping[str, object], key: str, *, default: int | None = None) -> int:
    value = metadata.get(key, default)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise ValidationFailed("dead-letter record metadata is missing an integer", details={"field": key})


def _schema_strategy(metadata: Mapping[str, object]) -> StreamSchemaStrategy:
    value = _str_metadata(metadata, "schema_strategy")
    if value not in {"envelope_json", "cdc_envelope_json"}:
        raise ValidationFailed("dead-letter record schema strategy is invalid", details={"schema_strategy": value})
    return cast(StreamSchemaStrategy, value)
