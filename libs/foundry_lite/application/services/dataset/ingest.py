"""Application service helpers for ingest workflows."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import NoReturn

from foundry_lite.application.ports import (
    ConnectorAdapter,
    ConnectorNetworkRoute,
    DatasetRow,
    DeadLetterRecordRow,
    RestSourceConfig,
    StreamAdapter,
    StreamArchiveConfig,
    StreamEvent,
    SyncRunRecord,
    TransactionContext,
)
from foundry_lite.application.primitives import CommitResult, _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.dataset import connector_snapshot_ingest, record_dlq_replay, webhook_ingest
from foundry_lite.application.services.dataset.ingest_models import UploadSyncPlan
from foundry_lite.application.services.dataset.protocols import (
    DatasetRegistryLookup,
    DatasetRuntimeBoundary,
    DatasetTransactionManager,
    DatasetVersionLookup,
    mark_sync_run_committed,
    require_dataset_write_open,
)
from foundry_lite.application.services.dataset.stream_archive_commit import (
    commit_stream_archive,
    latest_committed_stream_transaction,
    read_stream_archive_events,
    record_stream_pre_commit_failure,
    record_stream_read_failure,
    stream_cursor_offset,
)
from foundry_lite.application.upload_limits import require_csv_size_limit
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ConflictDetected,
    DatasetCommitBlocked,
    FoundryLiteError,
    InvariantViolation,
    NotFound,
    ValidationFailed,
)


class DatasetIngestService(CoreService):
    required_dependencies = (
        "engine",
        "compute_adapter",
        "connector_adapter",
        "stream_adapter",
        "dataset_transaction_repository",
    )
    required_collaborators = (
        "dataset_registry_service",
        "dataset_transaction_service",
        "dataset_version_service",
        "runtime_service",
    )
    connector_adapter: ConnectorAdapter
    stream_adapter: StreamAdapter
    dataset_registry_service: DatasetRegistryLookup
    dataset_transaction_service: DatasetTransactionManager
    dataset_version_service: DatasetVersionLookup
    runtime_service: DatasetRuntimeBoundary

    def upload_csv(
        self,
        dataset_ref: str,
        csv_path: str | Path,
        *,
        ctx: RequestContext | None = None,
        sync_name: str | None = None,
    ) -> CommitResult:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "dataset:write", "dataset", dataset_ref)
        require_dataset_write_open(self.runtime_service, ctx, "upload_csv", "dataset", dataset_ref)
        dataset = self.dataset_registry_service.get_dataset(dataset_ref, ctx=ctx)
        source_path = Path(csv_path).resolve()
        if not source_path.exists():
            raise NotFound("csv file not found", details={"path": str(source_path)})
        require_csv_size_limit(source_path)

        with self.engine.begin() as conn:
            plan = self._start_upload_sync_run(conn, ctx, dataset, dataset_ref, sync_name)

        staged = self.dataset_transaction_service._staging_file(dataset, plan.transaction_id, "part-00000.parquet")
        try:
            self._csv_to_parquet(source_path, staged)
        except Exception as exc:
            self._abort_upload_after_error(ctx, plan.transaction_id, plan.run_id, exc, "compute_adapter.csv_to_parquet")
        try:
            return self._finalize_upload_csv(ctx, dataset, plan, staged)
        except Exception as exc:
            self._abort_upload_after_error(ctx, plan.transaction_id, plan.run_id, exc, "dataset_transaction.finalize")

    def _finalize_upload_csv(
        self,
        ctx: RequestContext,
        dataset: DatasetRow,
        plan: UploadSyncPlan,
        staged: Path,
    ) -> CommitResult:
        blocked: DatasetCommitBlocked | None = None
        with self.engine.begin() as conn:
            try:
                return self.dataset_transaction_service._finalize_open_transaction(
                    conn,
                    ctx,
                    dataset=dataset,
                    transaction_id=plan.transaction_id,
                    staged_parquet=staged,
                    run_id=plan.run_id,
                    audit_action="csv_upload_commit",
                    outbox_event_type="dataset.version.committed",
                    after_persist=lambda commit_conn, result: mark_sync_run_committed(
                        self.dataset_transaction_repository, commit_conn, ctx, plan.run_id, result
                    ),
                )
            except DatasetCommitBlocked as exc:
                blocked = exc
        if blocked is not None:
            raise blocked
        raise InvariantViolation("dataset upload finalization did not return a commit result")

    def _finalize_rows_batch(
        self,
        ctx: RequestContext,
        dataset: DatasetRow,
        plan: UploadSyncPlan,
        staged: Path,
        transaction_metadata: Mapping[str, object],
    ) -> CommitResult:
        blocked: DatasetCommitBlocked | None = None
        with self.engine.begin() as conn:
            try:
                return self.dataset_transaction_service._finalize_open_transaction(
                    conn,
                    ctx,
                    dataset=dataset,
                    transaction_id=plan.transaction_id,
                    staged_parquet=staged,
                    run_id=plan.run_id,
                    audit_action="source_rows_batch_commit",
                    outbox_event_type="dataset.version.committed",
                    transaction_metadata=transaction_metadata,
                    after_persist=lambda commit_conn, result: mark_sync_run_committed(
                        self.dataset_transaction_repository, commit_conn, ctx, plan.run_id, result
                    ),
                )
            except DatasetCommitBlocked as exc:
                blocked = exc
        if blocked is not None:
            raise blocked
        raise InvariantViolation("source rows batch finalization did not return a commit result")

    def sync_connector_snapshot(
        self,
        dataset_ref: str,
        *,
        connector_name: str,
        resource_name: str,
        ctx: RequestContext | None = None,
        sync_name: str | None = None,
        cursor: Mapping[str, object] | None = None,
        rest: RestSourceConfig | None = None,
        network_route: ConnectorNetworkRoute | None = None,
        tx_type: str = "SNAPSHOT",
        run_id: str | None = None,
    ) -> CommitResult:
        return connector_snapshot_ingest.sync_connector_snapshot(
            self,
            dataset_ref,
            connector_adapter=self.connector_adapter,
            connector_name=connector_name,
            resource_name=resource_name,
            ctx=ctx,
            sync_name=sync_name,
            cursor=cursor,
            rest=rest,
            network_route=network_route,
            tx_type=tx_type,
            run_id=run_id,
        )

    def sync_rows_batch(
        self,
        dataset_ref: str,
        rows: Sequence[Mapping[str, object]],
        *,
        fieldnames: Sequence[str],
        ctx: RequestContext | None = None,
        sync_name: str | None = None,
        tx_type: str = "APPEND",
        source_type: str = "source.batch",
        transaction_metadata: Mapping[str, object] | None = None,
    ) -> CommitResult | None:
        ctx = ctx or RequestContext()
        if not rows:
            return None
        self.runtime_service._require_or_audit(ctx, "dataset:write", "dataset", dataset_ref)
        require_dataset_write_open(self.runtime_service, ctx, "sync_rows_batch", "dataset", dataset_ref)
        dataset = self.dataset_registry_service.get_dataset(dataset_ref, ctx=ctx)
        with self.engine.begin() as conn:
            plan = self._start_connector_sync_run(
                conn,
                ctx,
                dataset,
                "source",
                sync_name or dataset_ref,
                sync_name,
                tx_type=tx_type,
                source_type=source_type,
            )
        staged = self.dataset_transaction_service._staging_file(dataset, plan.transaction_id, "part-00000.parquet")
        try:
            self._rows_to_parquet(rows, staged, [str(field) for field in fieldnames])
            return self._finalize_rows_batch(ctx, dataset, plan, staged, transaction_metadata or {})
        except Exception as exc:
            self._abort_upload_after_error(ctx, plan.transaction_id, plan.run_id, exc, "source.sync_rows_batch")

    def ingest_webhook_event(
        self,
        dataset_ref: str,
        *,
        connector_name: str,
        resource_name: str,
        payload: Mapping[str, object],
        raw_body: bytes,
        signature: str,
        signature_timestamp: str,
        secret: str,
        event_id: str | None = None,
        require_service_principal_signature: bool = False,
        ctx: RequestContext | None = None,
    ) -> CommitResult:
        ctx = ctx or RequestContext()
        return webhook_ingest.ingest_webhook_event(
            self,
            dataset_ref,
            dataset_version_service=self.dataset_version_service,
            connector_name=connector_name,
            resource_name=resource_name,
            payload=payload,
            raw_body=raw_body,
            signature=signature,
            signature_timestamp=signature_timestamp,
            secret=secret,
            event_id=event_id,
            signature_context=_webhook_signature_context(
                ctx,
                dataset_ref=dataset_ref,
                connector_name=connector_name,
                resource_name=resource_name,
                require_service_principal_signature=require_service_principal_signature,
            ),
            ctx=ctx,
        )

    def archive_stream_events(
        self,
        dataset_ref: str,
        *,
        stream: StreamArchiveConfig,
        ctx: RequestContext | None = None,
        after_offset: int | None = None,
        sync_name: str | None = None,
        pre_commit_check: Callable[[Sequence[StreamEvent]], None] | None = None,
        stream_adapter: StreamAdapter | None = None,
    ) -> CommitResult | None:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "dataset:write", "dataset", dataset_ref)
        require_dataset_write_open(self.runtime_service, ctx, "archive_stream_events", "dataset", dataset_ref)
        dataset = self.dataset_registry_service.get_dataset(dataset_ref, ctx=ctx)
        committed_transaction = latest_committed_stream_transaction(
            self.engine, self.dataset_transaction_repository, ctx, dataset
        )
        committed_metadata = committed_transaction["metadata"] if committed_transaction is not None else {}
        resume_offset = after_offset if after_offset is not None else stream_cursor_offset(committed_metadata, stream)
        events = self._read_stream_archive_events(
            ctx, dataset, stream, sync_name, resume_offset, stream_adapter or self.stream_adapter
        )
        if not events:
            return None
        self._run_stream_pre_commit_check(ctx, dataset, stream, sync_name, events, pre_commit_check)
        return commit_stream_archive(self, ctx, dataset, stream, events, sync_name, committed_transaction)

    def _read_stream_archive_events(
        self,
        ctx: RequestContext,
        dataset: DatasetRow,
        stream: StreamArchiveConfig,
        sync_name: str | None,
        resume_offset: int | None,
        stream_adapter: StreamAdapter,
    ) -> Sequence[StreamEvent]:
        try:
            return read_stream_archive_events(stream_adapter, stream, resume_offset)
        except Exception as exc:
            repository = self.dataset_transaction_repository
            record_stream_read_failure(
                self.engine, repository, self.runtime_service, ctx, dataset, stream, sync_name, exc
            )
            if isinstance(exc, FoundryLiteError):
                raise
            raise ValidationFailed("stream archive read failed", details={"error": str(exc)}) from exc

    def _run_stream_pre_commit_check(
        self,
        ctx: RequestContext,
        dataset: DatasetRow,
        stream: StreamArchiveConfig,
        sync_name: str | None,
        events: Sequence[StreamEvent],
        pre_commit_check: Callable[[Sequence[StreamEvent]], None] | None,
    ) -> None:
        try:
            if pre_commit_check is not None:
                pre_commit_check(events)
        except Exception as exc:
            record_stream_pre_commit_failure(
                self.engine,
                self.dataset_transaction_repository,
                self.runtime_service,
                ctx,
                dataset,
                stream,
                sync_name,
                exc,
            )
            raise

    def replay_dead_letter_record(
        self,
        record_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> DeadLetterRecordRow:
        return record_dlq_replay.replay_dead_letter_record(self, record_id, ctx=ctx or RequestContext())

    def _start_upload_sync_run(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset: DatasetRow,
        dataset_ref: str,
        sync_name: str | None,
    ) -> UploadSyncPlan:
        transaction_id = self.dataset_transaction_service._open_dataset_transaction(conn, ctx, dataset, "SNAPSHOT")
        run_id = _new_id("sync_run")
        self.dataset_transaction_repository.insert_sync_run(
            transaction=conn,
            record=SyncRunRecord(
                sync_run_id=run_id,
                tenant_id=ctx.tenant_id,
                sync_name=sync_name or f"upload:{dataset_ref}",
                source_type="file.csv",
                output_dataset_id=str(dataset["id"]),
                transaction_id=transaction_id,
                committed_version_id=None,
                status="EXTRACTING",
                error=None,
                created_at=_now(),
                completed_at=None,
            ),
        )
        return UploadSyncPlan(transaction_id=transaction_id, run_id=run_id)

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
    ) -> UploadSyncPlan:
        return connector_snapshot_ingest.start_connector_sync_run(
            self.dataset_transaction_service,
            self.dataset_transaction_repository,
            conn,
            ctx,
            dataset,
            connector_name,
            resource_name,
            sync_name,
            tx_type=tx_type,
            source_type=source_type,
            run_id=run_id,
        )

    def _csv_to_parquet(self, source_path: Path, target_path: Path) -> None:
        self.compute_adapter.csv_to_parquet(source_path, target_path)

    def _abort_upload_after_error(
        self,
        ctx: RequestContext,
        transaction_id: str,
        run_id: str,
        exc: Exception,
        adapter: str | None,
    ) -> NoReturn:
        self.dataset_transaction_service._abort_transaction_after_error(
            ctx,
            transaction_id,
            run_id,
            exc,
            "sync",
            adapter=adapter,
        )
        if isinstance(exc, ValidationFailed | NotFound | ConflictDetected | InvariantViolation):
            raise exc
        raise ValidationFailed("csv upload failed", details={"error": str(exc)}) from exc

    def _abort_connector_after_error(
        self,
        ctx: RequestContext,
        transaction_id: str,
        run_id: str,
        exc: Exception,
    ) -> NoReturn:
        self.dataset_transaction_service._abort_transaction_after_error(
            ctx,
            transaction_id,
            run_id,
            exc,
            "sync",
            adapter="connector_adapter.snapshot",
        )
        if isinstance(exc, ValidationFailed | NotFound | ConflictDetected | InvariantViolation):
            raise exc
        raise ValidationFailed("connector snapshot sync failed", details={"error": str(exc)}) from exc

    def _abort_stream_after_error(
        self,
        ctx: RequestContext,
        transaction_id: str,
        run_id: str,
        exc: Exception,
    ) -> NoReturn:
        self.dataset_transaction_service._abort_transaction_after_error(
            ctx,
            transaction_id,
            run_id,
            exc,
            "sync",
            adapter="stream_archive_writer",
        )
        if isinstance(exc, ValidationFailed | NotFound | ConflictDetected | InvariantViolation):
            raise exc
        raise ValidationFailed("stream archive failed", details={"error": str(exc)}) from exc

    def _mark_sync_run_committed(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run_id: str,
        result: CommitResult,
    ) -> None:
        mark_sync_run_committed(self.dataset_transaction_repository, conn, ctx, run_id, result)

    def _rows_to_parquet(self, rows: Sequence[Mapping[str, object]], target_path: Path, fieldnames: list[str]) -> None:
        self.compute_adapter.rows_to_parquet(rows, target_path, fieldnames)


def _webhook_signature_context(
    ctx: RequestContext,
    *,
    dataset_ref: str,
    connector_name: str,
    resource_name: str,
    require_service_principal_signature: bool,
) -> webhook_ingest.WebhookSignatureContext | None:
    if not require_service_principal_signature:
        return None
    return webhook_ingest.WebhookSignatureContext(
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.actor_user_id,
        dataset_ref=dataset_ref,
        connector_name=connector_name,
        resource_name=resource_name,
    )
