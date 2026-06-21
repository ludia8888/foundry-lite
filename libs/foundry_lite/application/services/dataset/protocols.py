from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Literal, Protocol, overload

from foundry_lite.application.ports import (
    SYNC_RUN_COMMITTED,
    DatasetCheckConfig,
    DatasetCheckResult,
    DatasetManifest,
    DatasetRow,
    DatasetRunKind,
    DatasetSchemaJson,
    DatasetSchemaReference,
    DatasetSchemaRow,
    DatasetTransactionMetadata,
    DatasetTransactionRepository,
    DatasetVersionRow,
    TransactionContext,
)
from foundry_lite.application.primitives import CommitResult, StagedFileStats, _now
from foundry_lite.application.services.dataset.schema_evolution import DatasetSchemaEvolutionResult
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected

DatasetCommitMetadataHook = Callable[[TransactionContext, CommitResult], None]


def mark_sync_run_committed(
    repository: DatasetTransactionRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    run_id: str,
    result: CommitResult,
) -> None:
    updated = repository.update_sync_run_terminal(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        sync_run_id=run_id,
        transition=SYNC_RUN_COMMITTED,
        committed_version_id=result.version_id,
        completed_at=_now(),
    )
    if not updated:
        raise ConflictDetected("sync run terminal state changed concurrently", details={"run_id": run_id})


class DatasetRegistryLookup(Protocol):
    def get_dataset(self, dataset_ref: str, *, ctx: RequestContext | None = None) -> DatasetRow: ...


class DatasetTransactionManager(Protocol):
    def _open_dataset_transaction(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset: DatasetRow,
        tx_type: str,
        *,
        branch: str = "main",
    ) -> str: ...

    def _staging_file(self, dataset: DatasetRow, transaction_id: str, file_name: str) -> Path: ...

    def _version_file_path(self, version: DatasetVersionRow) -> Path: ...

    def _load_manifest(self, manifest_uri: str) -> DatasetManifest: ...

    def _finalize_open_transaction(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        dataset: DatasetRow,
        transaction_id: str,
        staged_parquet: Path,
        run_id: str,
        audit_action: str,
        outbox_event_type: str,
        extra_checks: Sequence[DatasetCheckConfig] | None = None,
        transaction_metadata: DatasetTransactionMetadata | None = None,
        after_persist: DatasetCommitMetadataHook | None = None,
    ) -> CommitResult: ...

    def _abort_transaction_after_error(
        self,
        ctx: RequestContext,
        transaction_id: str,
        run_id: str,
        exc: Exception,
        run_kind: DatasetRunKind,
        adapter: str | None = None,
    ) -> None: ...


class DatasetVersionLookup(Protocol):
    def _next_dataset_version_number(self, conn: TransactionContext, dataset_id: str) -> int: ...

    def _schema_for_version(self, dataset_id: str, schema_version: int) -> DatasetSchemaRow: ...

    def _get_version(
        self,
        dataset_id: str,
        version: str,
        *,
        ctx: RequestContext,
    ) -> DatasetVersionRow: ...

    @overload
    def _latest_version_by_dataset_id(
        self,
        conn: TransactionContext,
        dataset_id: str,
        *,
        allow_missing: Literal[False] = False,
    ) -> DatasetVersionRow: ...

    @overload
    def _latest_version_by_dataset_id(
        self,
        conn: TransactionContext,
        dataset_id: str,
        *,
        allow_missing: Literal[True],
    ) -> DatasetVersionRow | None: ...


class DatasetQualityBoundary(Protocol):
    def _inspect_parquet(self, parquet_path: Path, primary_key: list[str]) -> StagedFileStats: ...

    def _ensure_schema(
        self,
        conn: TransactionContext,
        dataset: DatasetRow,
        schema_json: DatasetSchemaJson,
        schema_hash: str,
    ) -> int: ...

    def _ensure_schema_reference(
        self,
        conn: TransactionContext,
        dataset: DatasetRow,
        schema_json: DatasetSchemaJson,
        schema_hash: str,
    ) -> DatasetSchemaReference: ...

    def _schema_compatibility_error(
        self,
        conn: TransactionContext,
        dataset: DatasetRow,
        next_schema: DatasetSchemaJson,
    ) -> DatasetCheckResult | None: ...

    def _schema_evolution_result(
        self,
        conn: TransactionContext,
        dataset: DatasetRow,
        next_schema: DatasetSchemaJson,
        target_schema: DatasetSchemaReference,
    ) -> DatasetSchemaEvolutionResult: ...

    def _run_dataset_checks(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset: DatasetRow,
        parquet_path: Path,
        row_count: int,
        run_id: str,
        transaction_id: str,
        *,
        extra_checks: Sequence[DatasetCheckConfig],
        checked_manifest_hash: str,
        validated_against_schema_version_id: str,
        validated_against_schema_version: int,
    ) -> list[DatasetCheckResult]: ...


class DatasetRuntimeBoundary(Protocol):
    def _require_write_traffic_open(
        self,
        ctx: RequestContext,
        *,
        operation: str,
        resource_type: str,
        resource_id: str,
    ) -> None: ...

    def _require_or_audit(
        self,
        ctx: RequestContext,
        permission: str,
        resource_type: str,
        resource_id: str,
    ) -> None: ...

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
        policy_decision: Mapping[str, object] | None = None,
        before_ref: Mapping[str, object] | None = None,
        after_ref: Mapping[str, object] | None = None,
        correlation_id: str | None = None,
    ) -> None: ...

    def _outbox(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: Mapping[str, object],
        *,
        idempotency_key: str,
        correlation_id: str,
    ) -> None: ...

    def _error_payload(
        self,
        exc: Exception,
        ctx: RequestContext | None = None,
        *,
        run_id: str | None = None,
        correlation_id: str | None = None,
        adapter: str | None = None,
    ) -> Mapping[str, object]: ...


def require_dataset_write_open(
    runtime_service: DatasetRuntimeBoundary,
    ctx: RequestContext,
    operation: str,
    resource_type: str,
    resource_id: str,
) -> None:
    runtime_service._require_write_traffic_open(
        ctx,
        operation=operation,
        resource_type=resource_type,
        resource_id=resource_id,
    )
