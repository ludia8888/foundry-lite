"""Atomic finalization boundary for Dataset row-batch ingestion."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from foundry_lite.application.ports import (
    DatasetRow,
    DatasetTransactionRepository,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.primitives import CommitResult
from foundry_lite.application.services.dataset.protocols import (
    DatasetTransactionManager,
    mark_sync_run_committed,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import DatasetCommitBlocked, InvariantViolation

DatasetCommitGuard = Callable[[TransactionContext], None]


def finalize_rows_batch(
    transaction_manager: TransactionManager,
    dataset_transaction_service: DatasetTransactionManager,
    repository: DatasetTransactionRepository,
    ctx: RequestContext,
    dataset: DatasetRow,
    plan_transaction_id: str,
    run_id: str,
    staged: Path,
    transaction_metadata: Mapping[str, object],
    before_commit: DatasetCommitGuard | None,
) -> CommitResult:
    blocked: DatasetCommitBlocked | None = None
    with transaction_manager.begin() as transaction:
        try:
            return dataset_transaction_service._finalize_open_transaction(
                transaction,
                ctx,
                dataset=dataset,
                transaction_id=plan_transaction_id,
                staged_parquet=staged,
                run_id=run_id,
                audit_action="source_rows_batch_commit",
                outbox_event_type="dataset.version.committed",
                transaction_metadata=transaction_metadata,
                after_persist=lambda commit_transaction, result: _complete_rows_batch(
                    repository,
                    commit_transaction,
                    ctx,
                    run_id,
                    result,
                    before_commit,
                ),
            )
        except DatasetCommitBlocked as exc:
            blocked = exc
    if blocked is not None:
        raise blocked
    raise InvariantViolation("source rows batch finalization did not return a commit result")


def _complete_rows_batch(
    repository: DatasetTransactionRepository,
    transaction: TransactionContext,
    ctx: RequestContext,
    run_id: str,
    result: CommitResult,
    before_commit: DatasetCommitGuard | None,
) -> None:
    if before_commit is not None:
        before_commit(transaction)
    mark_sync_run_committed(repository, transaction, ctx, run_id, result)
