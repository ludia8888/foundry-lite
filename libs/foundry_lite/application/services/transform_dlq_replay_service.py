"""Transform record DLQ replay use cases."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from foundry_lite.application.ports import (
    DatasetTransactionRepository,
    TransactionContext,
    TransactionManager,
    TransformExecutionResult,
    TransformRecordDlqRetryResult,
    TransformRepository,
)
from foundry_lite.application.primitives import CommitResult
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.transform_protocols import (
    TransformDatasetRegistry,
    TransformDatasetTransactions,
    TransformDatasetVersions,
    TransformRuntimeBoundary,
    require_transform_write_open,
)
from foundry_lite.application.services.transform_record_dlq_replay import retry_transform_record_dlq
from foundry_lite.application.services.transform_runs import TransformRunPlan
from foundry_lite.domain.context import RequestContext

TransformCommitHook = Callable[[TransactionContext, CommitResult], None]


class _TransformReplayRunner(Protocol):
    def execute_transform_plan(self, plan: TransformRunPlan, staged: Path) -> TransformExecutionResult:
        """Execute a transform plan into a staged output path."""
        ...

    def finalize_executed_transform(
        self,
        ctx: RequestContext,
        plan: TransformRunPlan,
        staged: Path,
        execution: TransformExecutionResult,
        dead_letter_ids: Sequence[str],
        *,
        should_audit_retry: bool = False,
        after_transform_commit: TransformCommitHook | None = None,
    ) -> CommitResult:
        """Commit a clean transform replay output."""
        ...

    def abort_transform_run(self, ctx: RequestContext, plan: TransformRunPlan, exc: Exception) -> None:
        """Abort a failed transform replay plan."""
        ...


class TransformDlqReplayService(CoreService):
    """Replay transform-owned dead-letter records."""

    required_dependencies = ("engine", "dataset_transaction_repository", "transform_repository")
    required_collaborators = (
        "dataset_registry_service",
        "dataset_transaction_service",
        "dataset_version_service",
        "runtime_service",
        "transform_run_service",
    )
    dataset_registry_service: TransformDatasetRegistry
    dataset_transaction_repository: DatasetTransactionRepository
    dataset_transaction_service: TransformDatasetTransactions
    dataset_version_service: TransformDatasetVersions
    engine: TransactionManager
    runtime_service: TransformRuntimeBoundary
    transform_repository: TransformRepository
    transform_run_service: _TransformReplayRunner

    def retry_transform_dead_letter_record(
        self,
        record_id: str,
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> TransformRecordDlqRetryResult:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "operations:retry", "dead_letter_record", record_id)
        require_transform_write_open(
            self.runtime_service,
            ctx,
            "retry_transform_dead_letter_record",
            "dead_letter_record",
            record_id,
        )
        self._require_replay_runtime_boundary()
        return retry_transform_record_dlq(self, record_id, idempotency_key=idempotency_key, ctx=ctx)

    def _execute_transform_plan(self, plan: TransformRunPlan, staged: Path) -> TransformExecutionResult:
        return self.transform_run_service.execute_transform_plan(plan, staged)

    def _finalize_executed_transform(
        self,
        ctx: RequestContext,
        plan: TransformRunPlan,
        staged: Path,
        execution: TransformExecutionResult,
        dead_letter_ids: Sequence[str],
        *,
        should_audit_retry: bool = False,
        after_transform_commit: TransformCommitHook | None = None,
    ) -> CommitResult:
        return self.transform_run_service.finalize_executed_transform(
            ctx,
            plan,
            staged,
            execution,
            dead_letter_ids,
            should_audit_retry=should_audit_retry,
            after_transform_commit=after_transform_commit,
        )

    def _abort_transform_run(self, ctx: RequestContext, plan: TransformRunPlan, exc: Exception) -> None:
        self.transform_run_service.abort_transform_run(ctx, plan, exc)

    def _require_replay_runtime_boundary(self) -> None:
        _ = (
            self.engine,
            self.dataset_registry_service,
            self.dataset_transaction_repository,
            self.dataset_transaction_service,
            self.dataset_version_service,
            self.transform_repository,
        )
