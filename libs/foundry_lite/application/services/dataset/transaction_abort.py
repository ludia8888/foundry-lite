"""Failure cleanup for open dataset transactions."""

from __future__ import annotations

from typing import Protocol

from foundry_lite.application.ports import (
    DatasetRunKind,
    DatasetStorageAdapter,
    DatasetTransactionRepository,
    TransactionManager,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.dataset.protocols import DatasetRuntimeBoundary
from foundry_lite.application.services.dataset.storage_consistency import cleanup_staging_transaction
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected


class DatasetTransactionAbortBoundary(Protocol):
    engine: TransactionManager
    dataset_storage: DatasetStorageAdapter
    dataset_transaction_repository: DatasetTransactionRepository
    runtime_service: DatasetRuntimeBoundary


def abort_transaction_after_error(
    service: DatasetTransactionAbortBoundary,
    ctx: RequestContext,
    transaction_id: str,
    run_id: str,
    exc: Exception,
    run_kind: DatasetRunKind,
    adapter: str | None,
) -> None:
    error_payload = service.runtime_service._error_payload(
        exc, ctx, run_id=run_id, correlation_id=run_id, adapter=adapter
    )
    with service.engine.begin() as conn:
        tx = service.dataset_transaction_repository.transaction_by_id(transaction=conn, transaction_id=transaction_id)
        cleanup = cleanup_staging_transaction(service.dataset_storage, ctx, tx, transaction_id)
        is_aborted = service.dataset_transaction_repository.abort_open_transaction_and_fail_run(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            transaction_id=transaction_id,
            run_id=run_id,
            run_kind=run_kind,
            error=error_payload,
            completed_at=_now(),
        )
        _require_abort_won(is_aborted, transaction_id, run_id)
        service.runtime_service._audit(
            conn,
            ctx,
            event_type="dataset.transaction.aborted",
            resource_type="dataset_transaction",
            resource_id=transaction_id,
            action="abort",
            after_ref={"error": error_payload, "staging_cleanup": cleanup},
            correlation_id=run_id,
        )


def _require_abort_won(is_aborted: bool, transaction_id: str, run_id: str) -> None:
    if not is_aborted:
        raise ConflictDetected(
            "dataset transaction abort lost its current state",
            details={"transaction_id": transaction_id, "run_id": run_id},
        )
