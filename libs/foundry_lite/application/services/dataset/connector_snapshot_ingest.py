from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NoReturn, Protocol

from foundry_lite.application.ports import (
    ConnectorAdapter,
    ConnectorSnapshot,
    ConnectorSnapshotRequest,
    DatasetRow,
    DatasetTransactionRepository,
    RestSourceConfig,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.primitives import CommitResult
from foundry_lite.application.services.dataset.ingest_models import ConnectorSnapshotSync, UploadSyncPlan
from foundry_lite.application.services.dataset.protocols import (
    DatasetRegistryLookup,
    DatasetRuntimeBoundary,
    DatasetTransactionManager,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed


class ConnectorSnapshotIngestRuntime(Protocol):
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

    def _abort_connector_after_error(
        self,
        ctx: RequestContext,
        transaction_id: str,
        run_id: str,
        exc: Exception,
    ) -> NoReturn: ...


def sync_connector_snapshot(
    runtime: ConnectorSnapshotIngestRuntime,
    dataset_ref: str,
    *,
    connector_adapter: ConnectorAdapter,
    connector_name: str,
    resource_name: str,
    ctx: RequestContext | None = None,
    sync_name: str | None = None,
    cursor: Mapping[str, object] | None = None,
    rest: RestSourceConfig | None = None,
) -> CommitResult:
    ctx = ctx or RequestContext()
    sync = _prepare_connector_snapshot_sync(runtime, ctx, dataset_ref, connector_name, resource_name, sync_name, cursor)
    return _commit_connector_snapshot(runtime, connector_adapter, ctx, sync, connector_name, resource_name, rest)


def _prepare_connector_snapshot_sync(
    runtime: ConnectorSnapshotIngestRuntime,
    ctx: RequestContext,
    dataset_ref: str,
    connector_name: str,
    resource_name: str,
    sync_name: str | None,
    cursor: Mapping[str, object] | None,
) -> ConnectorSnapshotSync:
    runtime.runtime_service._require_or_audit(ctx, "dataset:write", "dataset", dataset_ref)
    dataset = runtime.dataset_registry_service.get_dataset(dataset_ref, ctx=ctx)
    resume_cursor = cursor or _committed_connector_cursor(runtime, ctx, dataset, connector_name, resource_name)
    with runtime.engine.begin() as conn:
        plan = runtime._start_connector_sync_run(conn, ctx, dataset, connector_name, resource_name, sync_name)
    staged = runtime.dataset_transaction_service._staging_file(dataset, plan.transaction_id, "part-00000.parquet")
    return ConnectorSnapshotSync(dataset=dataset, plan=plan, staged=staged, resume_cursor=resume_cursor)


def _commit_connector_snapshot(
    runtime: ConnectorSnapshotIngestRuntime,
    connector_adapter: ConnectorAdapter,
    ctx: RequestContext,
    sync: ConnectorSnapshotSync,
    connector_name: str,
    resource_name: str,
    rest: RestSourceConfig | None,
) -> CommitResult:
    try:
        request = _connector_snapshot_request(ctx, sync, connector_name, resource_name, rest)
        snapshot = connector_adapter.snapshot(request)
        rows = list(snapshot.rows)
        runtime._rows_to_parquet(rows, sync.staged, _connector_fieldnames(snapshot.schema, rows))
        return _finalize_connector_snapshot(runtime, ctx, sync, connector_name, resource_name, snapshot)
    except Exception as exc:
        runtime._abort_connector_after_error(ctx, sync.plan.transaction_id, sync.plan.run_id, exc)


def _finalize_connector_snapshot(
    runtime: ConnectorSnapshotIngestRuntime,
    ctx: RequestContext,
    sync: ConnectorSnapshotSync,
    connector_name: str,
    resource_name: str,
    snapshot: ConnectorSnapshot,
) -> CommitResult:
    with runtime.engine.begin() as conn:
        result = runtime.dataset_transaction_service._finalize_open_transaction(
            conn,
            ctx,
            dataset=sync.dataset,
            transaction_id=sync.plan.transaction_id,
            staged_parquet=sync.staged,
            run_id=sync.plan.run_id,
            audit_action="connector_snapshot_commit",
            outbox_event_type="dataset.version.committed",
            transaction_metadata=_connector_transaction_metadata(
                connector_name,
                resource_name,
                sync.resume_cursor,
                snapshot.cursor,
            ),
        )
        runtime._mark_sync_run_committed(conn, ctx, sync.plan.run_id, result)
        return result


def _committed_connector_cursor(
    runtime: ConnectorSnapshotIngestRuntime,
    ctx: RequestContext,
    dataset: DatasetRow,
    connector_name: str,
    resource_name: str,
) -> Mapping[str, object] | None:
    with runtime.engine.begin() as conn:
        tx = runtime.dataset_transaction_repository.latest_committed_transaction(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            dataset_id=dataset["id"],
        )
    metadata = _connector_cursor_metadata(tx["metadata"] if tx else None)
    if metadata is None or not _connector_cursor_matches(metadata, connector_name, resource_name):
        return None
    cursor = metadata.get("nextCursor")
    return dict(cursor) if isinstance(cursor, Mapping) else None


def _connector_fieldnames(schema: Mapping[str, object], rows: Sequence[Mapping[str, object]]) -> list[str]:
    columns = schema.get("columns")
    if isinstance(columns, Sequence) and not isinstance(columns, str):
        names = [column for column in columns if isinstance(column, str)]
        if len(names) == len(columns) and names:
            return names
    if rows:
        return [str(name) for name in rows[0]]
    raise ValidationFailed("connector snapshot returned no schema columns")


def _connector_snapshot_request(
    ctx: RequestContext,
    sync: ConnectorSnapshotSync,
    connector_name: str,
    resource_name: str,
    rest: RestSourceConfig | None,
) -> ConnectorSnapshotRequest:
    return ConnectorSnapshotRequest(
        connector_name,
        resource_name,
        ctx.tenant_id,
        ctx.request_id,
        sync.resume_cursor,
        rest,
    )


def _connector_transaction_metadata(
    connector_name: str,
    resource_name: str,
    request_cursor: Mapping[str, object] | None,
    next_cursor: Mapping[str, object] | None,
) -> Mapping[str, object]:
    return {
        "connectorCursor": {
            "connectorName": connector_name,
            "resourceName": resource_name,
            "requestCursor": dict(request_cursor) if request_cursor is not None else None,
            "nextCursor": dict(next_cursor) if next_cursor is not None else None,
        }
    }


def _connector_cursor_metadata(metadata: Mapping[str, object] | None) -> Mapping[str, object] | None:
    if metadata is None:
        return None
    cursor = metadata.get("connectorCursor")
    return cursor if isinstance(cursor, Mapping) else None


def _connector_cursor_matches(metadata: Mapping[str, object], connector_name: str, resource_name: str) -> bool:
    return metadata.get("connectorName") == connector_name and metadata.get("resourceName") == resource_name
