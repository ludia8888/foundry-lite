from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NoReturn

from foundry_lite.application.ports import (
    ConnectorAdapter,
    DatasetRow,
    RestSourceConfig,
    StreamAdapter,
    StreamArchiveConfig,
    StreamEvent,
    SyncRunRecord,
    TransactionContext,
)
from foundry_lite.application.primitives import CommitResult, _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.dataset import connector_snapshot_ingest, webhook_ingest
from foundry_lite.application.services.dataset.ingest_models import UploadSyncPlan
from foundry_lite.application.services.dataset.protocols import (
    DatasetRegistryLookup,
    DatasetRuntimeBoundary,
    DatasetTransactionManager,
    DatasetVersionLookup,
)
from foundry_lite.application.services.dataset.stream_archive import (
    read_stream_archive_events,
    stream_archive_fields,
    stream_cursor_offset,
    stream_event_row,
    stream_transaction_metadata,
)
from foundry_lite.application.upload_limits import require_csv_size_limit
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ConflictDetected,
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
            with self.engine.begin() as conn:
                result = self.dataset_transaction_service._finalize_open_transaction(
                    conn,
                    ctx,
                    dataset=dataset,
                    transaction_id=plan.transaction_id,
                    staged_parquet=staged,
                    run_id=plan.run_id,
                    audit_action="csv_upload_commit",
                    outbox_event_type="dataset.version.committed",
                )
                self._mark_sync_run_committed(conn, ctx, plan.run_id, result)
                return result
        except Exception as exc:
            self._abort_upload_after_error(ctx, plan.transaction_id, plan.run_id, exc)

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
        )

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
        ctx: RequestContext | None = None,
    ) -> CommitResult:
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
    ) -> CommitResult | None:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "dataset:write", "dataset", dataset_ref)
        dataset = self.dataset_registry_service.get_dataset(dataset_ref, ctx=ctx)
        resume_offset = (
            after_offset if after_offset is not None else self._committed_stream_offset(ctx, dataset, stream)
        )
        try:
            events = read_stream_archive_events(self.stream_adapter, stream, resume_offset)
        except Exception as exc:
            self._record_stream_read_failure(ctx, dataset, stream, sync_name, exc)
            if isinstance(exc, FoundryLiteError):
                raise
            raise ValidationFailed("stream archive read failed", details={"error": str(exc)}) from exc
        if not events:
            return None
        return self._commit_stream_archive(ctx, dataset, stream, events, sync_name)

    def _start_upload_sync_run(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset: DatasetRow,
        dataset_ref: str,
        sync_name: str | None,
    ) -> UploadSyncPlan:
        """Open the upload transaction and persist the EXTRACTING sync run."""
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
    ) -> UploadSyncPlan:
        transaction_id = self.dataset_transaction_service._open_dataset_transaction(conn, ctx, dataset, tx_type)
        run_id = _new_id("sync_run")
        self.dataset_transaction_repository.insert_sync_run(
            transaction=conn,
            record=SyncRunRecord(
                sync_run_id=run_id,
                tenant_id=ctx.tenant_id,
                sync_name=sync_name or f"connector:{connector_name}:{resource_name}",
                source_type=source_type or f"connector.{connector_name}",
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

    def _committed_stream_offset(
        self,
        ctx: RequestContext,
        dataset: DatasetRow,
        stream: StreamArchiveConfig,
    ) -> int | None:
        with self.engine.begin() as conn:
            tx = self.dataset_transaction_repository.latest_committed_transaction(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                dataset_id=dataset["id"],
            )
        return stream_cursor_offset(tx["metadata"], stream) if tx else None

    def _record_stream_read_failure(
        self,
        ctx: RequestContext,
        dataset: DatasetRow,
        stream: StreamArchiveConfig,
        sync_name: str | None,
        exc: Exception,
    ) -> None:
        run_id = _new_id("sync_run")
        error = self.runtime_service._error_payload(exc, ctx, run_id=run_id, adapter="stream_archive_reader")
        now = _now()
        with self.engine.begin() as conn:
            self.dataset_transaction_repository.insert_sync_run(
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

    def _csv_to_parquet(self, source_path: Path, target_path: Path) -> None:
        self.compute_adapter.csv_to_parquet(source_path, target_path)

    def _abort_upload_after_error(
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
            adapter="compute_adapter.csv_to_parquet",
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

    def _commit_stream_archive(
        self,
        ctx: RequestContext,
        dataset: DatasetRow,
        stream: StreamArchiveConfig,
        events: Sequence[StreamEvent],
        sync_name: str | None,
    ) -> CommitResult:
        with self.engine.begin() as conn:
            plan = self._start_connector_sync_run(
                conn,
                ctx,
                dataset,
                "stream",
                stream.stream_name,
                sync_name or f"stream:{stream.stream_name}:{stream.consumer_group}",
                tx_type="APPEND",
                source_type=f"stream.{stream.stream_name}",
            )
        staged = self.dataset_transaction_service._staging_file(dataset, plan.transaction_id, "part-00000.parquet")
        return self._write_stream_archive_batch(ctx, dataset, stream, plan, staged, events)

    def _write_stream_archive_batch(
        self,
        ctx: RequestContext,
        dataset: DatasetRow,
        stream: StreamArchiveConfig,
        plan: UploadSyncPlan,
        staged: Path,
        events: Sequence[StreamEvent],
    ) -> CommitResult:
        try:
            rows = [stream_event_row(event, stream) for event in events]
            self._rows_to_parquet(rows, staged, stream_archive_fields(stream))
            with self.engine.begin() as conn:
                result = self.dataset_transaction_service._finalize_open_transaction(
                    conn,
                    ctx,
                    dataset=dataset,
                    transaction_id=plan.transaction_id,
                    staged_parquet=staged,
                    run_id=plan.run_id,
                    audit_action="stream_archive_append_commit",
                    outbox_event_type="dataset.version.committed",
                    transaction_metadata=stream_transaction_metadata(stream, events),
                )
                self._mark_sync_run_committed(conn, ctx, plan.run_id, result)
                return result
        except Exception as exc:
            self._abort_stream_after_error(ctx, plan.transaction_id, plan.run_id, exc)

    def _mark_sync_run_committed(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run_id: str,
        result: CommitResult,
    ) -> None:
        """Close the sync run with the committed dataset version under the tenant."""
        self.dataset_transaction_repository.update_sync_run_terminal(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            sync_run_id=run_id,
            status="COMMITTED",
            committed_version_id=result.version_id,
            completed_at=_now(),
        )

    def _rows_to_parquet(self, rows: Sequence[Mapping[str, object]], target_path: Path, fieldnames: list[str]) -> None:
        self.compute_adapter.rows_to_parquet(rows, target_path, fieldnames)
