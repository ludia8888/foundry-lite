"""Application service helpers for materialization protocols workflows."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol

from foundry_lite.application.ports import (
    MATERIALIZATION_RUN_SUCCEEDED,
    DatasetCheckConfig,
    DatasetRow,
    DatasetRunKind,
    MaterializationRepository,
    RuntimeRow,
    RuntimeRowsTable,
    RuntimeRunType,
    TransactionContext,
)
from foundry_lite.application.primitives import CommitResult, _now
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected

DatasetCommitMetadataHook = Callable[[TransactionContext, CommitResult], None]


def mark_materialization_run_succeeded(
    repository: MaterializationRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    run_id: str,
    result: CommitResult,
) -> None:
    updated = repository.update_materialization_run_terminal(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        materialization_run_id=run_id,
        transition=MATERIALIZATION_RUN_SUCCEEDED,
        target_dataset_version_id=result.version_id,
        row_count=result.row_count,
        error=None,
        completed_at=_now(),
    )
    if not updated:
        raise ConflictDetected("materialization run state changed concurrently", details={"run_id": run_id})


class MaterializationDatasetIngest(Protocol):
    """Parquet writer surface required by materialization runs."""

    def _rows_to_parquet(
        self,
        rows: Sequence[Mapping[str, object]],
        target_path: Path,
        fieldnames: list[str],
    ) -> None:
        """Write materialized runtime rows to the staged parquet file."""
        ...


class MaterializationDatasetRegistry(Protocol):
    """Dataset lookup surface required by materialization runs."""

    def get_dataset(self, dataset_ref: str, *, ctx: RequestContext | None = None) -> DatasetRow:
        """Return the target dataset row for a materialization spec."""
        ...


class MaterializationDatasetTransactions(Protocol):
    """Dataset transaction operations required by materialization runs."""

    def _open_dataset_transaction(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset: DatasetRow,
        tx_type: str,
        *,
        branch: str = "main",
    ) -> str:
        """Open the target dataset transaction for materialized output."""
        ...

    def _staging_file(self, dataset: DatasetRow, transaction_id: str, file_name: str) -> Path:
        """Return the staging path used before materialized output is committed."""
        ...

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
        transaction_metadata: Mapping[str, object] | None = None,
        after_persist: DatasetCommitMetadataHook | None = None,
    ) -> CommitResult:
        """Validate and commit the staged materialized output atomically."""
        ...

    def _abort_transaction_after_error(
        self,
        ctx: RequestContext,
        transaction_id: str,
        run_id: str,
        exc: Exception,
        run_kind: DatasetRunKind,
        adapter: str | None = None,
    ) -> None:
        """Abort the open dataset transaction and mark the materialization failed."""
        ...


class MaterializationRuntimeBoundary(Protocol):
    """Runtime policy, row-read, and lineage hooks used by materializations."""

    def _require_write_traffic_open(
        self,
        ctx: RequestContext,
        *,
        operation: str,
        resource_type: str,
        resource_id: str,
    ) -> None:
        """Reject writes while restore mode fences the tenant."""
        ...

    def _require_or_audit(
        self,
        ctx: RequestContext,
        permission: str,
        resource_type: str,
        resource_id: str,
    ) -> None:
        """Require a permission and write denial audit evidence on failure."""
        ...

    def _rows_for_tenant(
        self,
        conn: TransactionContext,
        table: RuntimeRowsTable,
        ctx: RequestContext,
    ) -> Sequence[RuntimeRow]:
        """Return tenant-scoped runtime rows from an allowlisted source table."""
        ...

    def _lineage(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        from_type: str,
        from_id: str,
        to_type: str,
        to_id: str,
        relation: str,
        run_id: str,
    ) -> None:
        """Persist a lineage edge emitted by a successful materialization run."""
        ...

    def _run_relation(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        source_run_type: RuntimeRunType,
        source_run_id: str,
        target_run_type: RuntimeRunType,
        target_run_id: str,
        relation: str,
        resource_type: str,
        resource_id: str,
        metadata: Mapping[str, object] | None = None,
    ) -> bool:
        """Persist a durable Operations relation emitted by a materialization run."""
        ...
