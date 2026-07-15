"""Application service helpers for connector snapshot ingest workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NoReturn, Protocol

from foundry_lite.application.ports import (
    ConnectorAdapter,
    ConnectorNetworkRoute,
    ConnectorSnapshot,
    ConnectorSnapshotRequest,
    DatasetRow,
    DatasetTransactionRepository,
    RestSourceConfig,
    SyncRunRecord,
    SyncRunRow,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.primitives import CommitResult, _new_id, _now
from foundry_lite.application.services.dataset.ingest_models import ConnectorSnapshotSync, UploadSyncPlan
from foundry_lite.application.services.dataset.protocols import (
    DatasetRegistryLookup,
    DatasetRuntimeBoundary,
    DatasetTransactionManager,
    DatasetVersionLookup,
)
from foundry_lite.application.services.write_traffic_gate import require_write_open
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, DatasetCommitBlocked, InvariantViolation, ValidationFailed


class ConnectorSnapshotIngestRuntime(Protocol):
    dataset_transaction_repository: DatasetTransactionRepository
    dataset_registry_service: DatasetRegistryLookup
    dataset_transaction_service: DatasetTransactionManager
    dataset_version_service: DatasetVersionLookup
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
    network_route: ConnectorNetworkRoute | None = None,
    tx_type: str = "SNAPSHOT",
    run_id: str | None = None,
) -> CommitResult:
    ctx = ctx or RequestContext()
    dataset = _connector_snapshot_dataset(runtime, ctx, dataset_ref)
    if existing := _committed_sync_result(runtime, ctx, dataset_ref, dataset, run_id):
        return existing
    sync = _prepare_connector_snapshot_sync(
        runtime, ctx, dataset, connector_name, resource_name, sync_name, cursor, tx_type, run_id
    )
    return _commit_connector_snapshot(
        runtime,
        connector_adapter,
        ctx,
        sync,
        connector_name,
        resource_name,
        rest,
        network_route,
    )


def start_connector_sync_run(
    transaction_service: DatasetTransactionManager,
    repository: DatasetTransactionRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    dataset: DatasetRow,
    connector_name: str,
    resource_name: str,
    sync_name: str | None,
    *,
    tx_type: str,
    source_type: str | None,
    run_id: str | None,
) -> UploadSyncPlan:
    if existing := resumable_sync_plan(repository, conn, ctx.tenant_id, run_id):
        return existing
    transaction_id = transaction_service._open_dataset_transaction(conn, ctx, dataset, tx_type)
    resolved_run_id = run_id or _new_id("sync_run")
    repository.insert_sync_run(
        transaction=conn,
        record=SyncRunRecord(
            sync_run_id=resolved_run_id,
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
    return UploadSyncPlan(transaction_id=transaction_id, run_id=resolved_run_id)


def resumable_sync_plan(
    repository: DatasetTransactionRepository,
    conn: TransactionContext,
    tenant_id: str,
    run_id: str | None,
) -> UploadSyncPlan | None:
    if run_id is None:
        return None
    row = repository.sync_run_by_id(transaction=conn, tenant_id=tenant_id, sync_run_id=run_id)
    if row is None:
        return None
    return _resumable_sync_plan(row, run_id)


def _prepare_connector_snapshot_sync(
    runtime: ConnectorSnapshotIngestRuntime,
    ctx: RequestContext,
    dataset: DatasetRow,
    connector_name: str,
    resource_name: str,
    sync_name: str | None,
    cursor: Mapping[str, object] | None,
    tx_type: str,
    run_id: str | None,
) -> ConnectorSnapshotSync:
    resume_cursor = cursor or _committed_connector_cursor(runtime, ctx, dataset, connector_name, resource_name)
    with runtime.engine.begin() as conn:
        plan = runtime._start_connector_sync_run(
            conn, ctx, dataset, connector_name, resource_name, sync_name, tx_type=tx_type, run_id=run_id
        )
    staged = runtime.dataset_transaction_service._staging_file(dataset, plan.transaction_id, "part-00000.parquet")
    return ConnectorSnapshotSync(dataset=dataset, plan=plan, staged=staged, resume_cursor=resume_cursor)


def _connector_snapshot_dataset(
    runtime: ConnectorSnapshotIngestRuntime,
    ctx: RequestContext,
    dataset_ref: str,
) -> DatasetRow:
    runtime.runtime_service._require_or_audit(ctx, "dataset:write", "dataset", dataset_ref)
    require_write_open(runtime.runtime_service, ctx, "sync_connector_snapshot", "dataset", dataset_ref)
    return runtime.dataset_registry_service.get_dataset(dataset_ref, ctx=ctx)


def _committed_sync_result(
    runtime: ConnectorSnapshotIngestRuntime,
    ctx: RequestContext,
    dataset_ref: str,
    dataset: DatasetRow,
    run_id: str | None,
) -> CommitResult | None:
    if run_id is None:
        return None
    with runtime.engine.begin() as conn:
        row = runtime.dataset_transaction_repository.sync_run_by_id(
            transaction=conn, tenant_id=ctx.tenant_id, sync_run_id=run_id
        )
    if row is None or row.get("status") != "COMMITTED":
        return None
    _require_committed_sync_dataset(row, dataset, run_id)
    return _committed_sync_commit_result(runtime, ctx, dataset_ref, dataset, row)


def _resumable_sync_plan(row: SyncRunRow, run_id: str) -> UploadSyncPlan:
    transaction_id = row.get("transaction_id")
    if row.get("status") != "EXTRACTING" or not isinstance(transaction_id, str) or not transaction_id:
        raise ConflictDetected(
            "sync run is not resumable",
            details={
                "sync_run_id": run_id,
                "status": row.get("status"),
                "transaction_id": transaction_id,
            },
        )
    return UploadSyncPlan(transaction_id=transaction_id, run_id=run_id)


def _require_committed_sync_dataset(row: SyncRunRow, dataset: DatasetRow, run_id: str) -> None:
    if row.get("output_dataset_id") == dataset["id"]:
        return
    raise ConflictDetected(
        "sync run belongs to a different dataset",
        details={"sync_run_id": run_id, "dataset_id": dataset["id"]},
    )


def _committed_sync_commit_result(
    runtime: ConnectorSnapshotIngestRuntime,
    ctx: RequestContext,
    dataset_ref: str,
    dataset: DatasetRow,
    row: SyncRunRow,
) -> CommitResult:
    version_id = row.get("committed_version_id")
    if not isinstance(version_id, str) or not version_id:
        raise InvariantViolation("committed sync run is missing committed version id")
    dataset_id = str(dataset["id"])
    version = runtime.dataset_version_service._get_version(dataset_id, version_id, ctx=ctx)
    schema = runtime.dataset_version_service._schema_for_version(dataset_id, version["schema_version"])
    return CommitResult(
        dataset_id=dataset_id,
        dataset_ref=dataset_ref,
        transaction_id=str(version["transaction_id"]),
        version_id=str(version["id"]),
        version_number=int(version["version_number"]),
        row_count=int(version["row_count"]),
        manifest_uri=str(version["manifest_uri"]),
        schema_hash=str(schema["schema_hash"]),
    )


def _commit_connector_snapshot(
    runtime: ConnectorSnapshotIngestRuntime,
    connector_adapter: ConnectorAdapter,
    ctx: RequestContext,
    sync: ConnectorSnapshotSync,
    connector_name: str,
    resource_name: str,
    rest: RestSourceConfig | None,
    network_route: ConnectorNetworkRoute | None,
) -> CommitResult:
    try:
        request = _connector_snapshot_request(ctx, sync, connector_name, resource_name, rest, network_route)
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
    blocked: DatasetCommitBlocked | None = None
    with runtime.engine.begin() as conn:
        try:
            return runtime.dataset_transaction_service._finalize_open_transaction(
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
                    snapshot.network_evidence,
                ),
                after_persist=lambda commit_conn, result: runtime._mark_sync_run_committed(
                    commit_conn, ctx, sync.plan.run_id, result
                ),
            )
        except DatasetCommitBlocked as exc:
            blocked = exc
    if blocked is not None:
        raise blocked
    raise InvariantViolation("connector snapshot finalization did not return a commit result")


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
    network_route: ConnectorNetworkRoute | None,
) -> ConnectorSnapshotRequest:
    return ConnectorSnapshotRequest(
        connector_name,
        resource_name,
        ctx.tenant_id,
        ctx.request_id,
        sync.resume_cursor,
        rest,
        network_route,
    )


def _connector_transaction_metadata(
    connector_name: str,
    resource_name: str,
    request_cursor: Mapping[str, object] | None,
    next_cursor: Mapping[str, object] | None,
    network_evidence: Mapping[str, object],
) -> Mapping[str, object]:
    return {
        "connectorCursor": {
            "connectorName": connector_name,
            "resourceName": resource_name,
            "requestCursor": dict(request_cursor) if request_cursor is not None else None,
            "nextCursor": dict(next_cursor) if next_cursor is not None else None,
        },
        "connectorNetworkEvidence": dict(network_evidence),
    }


def _connector_cursor_metadata(metadata: Mapping[str, object] | None) -> Mapping[str, object] | None:
    if metadata is None:
        return None
    cursor = metadata.get("connectorCursor")
    return cursor if isinstance(cursor, Mapping) else None


def _connector_cursor_matches(metadata: Mapping[str, object], connector_name: str, resource_name: str) -> bool:
    return metadata.get("connectorName") == connector_name and metadata.get("resourceName") == resource_name
