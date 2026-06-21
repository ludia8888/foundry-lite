from __future__ import annotations

from foundry_lite.application.ports import DatasetTransactionRow
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.dataset.protocols import DatasetRuntimeBoundary
from foundry_lite.application.services.dataset.storage_consistency import cleanup_staging_transaction
from foundry_lite.domain.context import RequestContext


class DatasetRecoveryService(CoreService):
    """Watchdog that recovers OPEN dataset transactions abandoned by OOM/crash.

    A process killed between opening a dataset transaction and committing it
    leaves an OPEN row that never reaches a terminal state. This service finds
    OPEN transactions older than an operator-provided cutoff, cleans up their
    staging artifacts, and marks them ABORTED with watchdog evidence so the
    dataset is recoverable instead of being blocked by a phantom OPEN write.
    """

    required_dependencies = ("engine", "dataset_storage", "dataset_transaction_repository")
    required_collaborators = ("runtime_service",)
    runtime_service: DatasetRuntimeBoundary

    def abort_stale_open_transactions(
        self,
        created_before: str,
        *,
        ctx: RequestContext | None = None,
    ) -> list[str]:
        ctx = ctx or RequestContext()
        with self.engine.begin() as conn:
            stale = self.dataset_transaction_repository.list_open_transactions(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                created_before=created_before,
            )
        aborted_ids: list[str] = []
        for tx in stale:
            aborted_id = self._abort_stale_open_transaction(ctx, tx)
            if aborted_id is not None:
                aborted_ids.append(aborted_id)
        return aborted_ids

    def _abort_stale_open_transaction(self, ctx: RequestContext, tx: DatasetTransactionRow) -> str | None:
        transaction_id = str(tx["id"])
        staging_cleanup = cleanup_staging_transaction(self.dataset_storage, ctx, tx, transaction_id)
        metadata = {
            **dict(tx["metadata"]),
            "abortedBy": "watchdog",
            "abortReason": "stale_open_transaction",
        }
        with self.engine.begin() as conn:
            aborted = self.dataset_transaction_repository.abort_transaction(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                transaction_id=transaction_id,
                metadata=metadata,
            )
            if not aborted:
                return None
            self.runtime_service._audit(
                conn,
                ctx,
                event_type="dataset.transaction.aborted_by_watchdog",
                resource_type="dataset_transaction",
                resource_id=transaction_id,
                action="abort_stale_open_transaction",
                after_ref={"stagingCleanup": staging_cleanup, "abortReason": "stale_open_transaction"},
            )
        return transaction_id
