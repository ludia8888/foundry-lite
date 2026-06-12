from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from foundry_lite.application.ports import (
    DatasetCheckConfig,
    DatasetRow,
    DatasetRunKind,
    RuntimeRow,
    RuntimeRowsTable,
    TransactionContext,
)
from foundry_lite.application.primitives import CommitResult
from foundry_lite.domain.context import RequestContext


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
