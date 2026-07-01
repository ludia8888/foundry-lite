"""Application service helpers for transform protocols workflows."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Literal, Protocol, overload

from foundry_lite.application.ports import (
    DatasetCheckConfig,
    DatasetRow,
    DatasetRunKind,
    DatasetVersionRow,
    RuntimeRunType,
    TransactionContext,
)
from foundry_lite.application.primitives import CommitResult
from foundry_lite.domain.context import RequestContext

DatasetCommitMetadataHook = Callable[[TransactionContext, CommitResult], None]


class TransformDatasetRegistry(Protocol):
    """Dataset lookup surface required by transform runs."""

    def get_dataset(self, dataset_ref: str, *, ctx: RequestContext | None = None) -> DatasetRow:
        """Return the active dataset row for a transform input or output ref."""
        ...


class TransformDatasetTransactions(Protocol):
    """Dataset transaction operations required by transform runs."""

    def _open_dataset_transaction(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset: DatasetRow,
        tx_type: str,
        *,
        branch: str = "main",
    ) -> str:
        """Open an output dataset transaction for the transform result."""
        ...

    def _staging_file(self, dataset: DatasetRow, transaction_id: str, file_name: str) -> Path:
        """Return the staging path used before the transform output is committed."""
        ...

    def _version_file_path(self, version: DatasetVersionRow) -> Path:
        """Return the concrete data file path for an input dataset version."""
        ...

    def _version_file_paths(
        self,
        version: DatasetVersionRow,
        *,
        partition_filter: Mapping[str, object] | None = None,
    ) -> list[Path]:
        """Return the concrete data file paths for an input dataset version."""
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
        """Validate and commit the staged transform output atomically."""
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
        """Abort the open dataset transaction and mark the run failed."""
        ...


class TransformDatasetVersions(Protocol):
    """Committed dataset version reads required by transform runs."""

    @overload
    def _latest_version_by_dataset_id(
        self,
        conn: TransactionContext,
        dataset_id: str,
        *,
        allow_missing: Literal[False] = False,
    ) -> DatasetVersionRow:
        """Return the latest committed input version inside the transaction."""
        ...

    @overload
    def _latest_version_by_dataset_id(
        self,
        conn: TransactionContext,
        dataset_id: str,
        *,
        allow_missing: Literal[True],
    ) -> DatasetVersionRow | None:
        """Return the latest committed input version or None when missing."""
        ...

    def _get_version_by_id(self, version_id: str) -> DatasetVersionRow:
        """Return a committed dataset version by id for input file resolution."""
        ...


class TransformRuntimeBoundary(Protocol):
    """Runtime policy, audit, and lineage hooks used by transforms."""

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
    ) -> None:
        """Persist an audit event inside the caller transaction."""
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
        """Persist a lineage edge emitted by a successful transform run."""
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
        """Persist a durable Operations relation emitted by a transform run."""
        ...


def require_transform_write_open(
    runtime_service: TransformRuntimeBoundary,
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
