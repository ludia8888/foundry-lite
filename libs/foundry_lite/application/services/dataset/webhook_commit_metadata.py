"""Application service helpers for webhook commit metadata workflows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from foundry_lite.application.dataset_webhook_events import WebhookEventKeyRecord
from foundry_lite.application.ports import (
    DatasetRow,
    DatasetTransactionRepository,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.primitives import CommitResult, _new_id, _now
from foundry_lite.application.services.dataset.ingest_models import UploadSyncPlan
from foundry_lite.application.services.dataset.protocols import DatasetTransactionManager
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import DatasetCommitBlocked, InvariantViolation


class WebhookCommitMetadataRuntime(Protocol):
    dataset_transaction_repository: DatasetTransactionRepository
    dataset_transaction_service: DatasetTransactionManager
    engine: TransactionManager

    def _mark_sync_run_committed(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run_id: str,
        result: CommitResult,
    ) -> None: ...


@dataclass(frozen=True)
class WebhookCommitMetadataRecorder:
    runtime: WebhookCommitMetadataRuntime
    ctx: RequestContext
    run_id: str
    dataset: DatasetRow
    connector_name: str
    resource_name: str
    event_id: str
    payload_hash: str

    def __call__(self, conn: TransactionContext, result: CommitResult) -> None:
        record_webhook_commit_metadata(
            self.runtime,
            conn,
            self.ctx,
            self.run_id,
            result,
            self.dataset,
            self.connector_name,
            self.resource_name,
            self.event_id,
            self.payload_hash,
        )


def finalize_webhook_commit(
    runtime: WebhookCommitMetadataRuntime,
    ctx: RequestContext,
    dataset: DatasetRow,
    plan: UploadSyncPlan,
    staged: Path,
    metadata: Mapping[str, object],
    recorder: WebhookCommitMetadataRecorder,
) -> CommitResult:
    blocked: DatasetCommitBlocked | None = None
    with runtime.engine.begin() as conn:
        try:
            return runtime.dataset_transaction_service._finalize_open_transaction(
                conn,
                ctx,
                dataset=dataset,
                transaction_id=plan.transaction_id,
                staged_parquet=staged,
                run_id=plan.run_id,
                audit_action="webhook_append_commit",
                outbox_event_type="dataset.version.committed",
                transaction_metadata=metadata,
                after_persist=recorder,
            )
        except DatasetCommitBlocked as exc:
            blocked = exc
    if blocked is not None:
        raise blocked
    raise InvariantViolation("webhook finalization did not return a commit result")


def record_webhook_commit_metadata(
    runtime: WebhookCommitMetadataRuntime,
    conn: TransactionContext,
    ctx: RequestContext,
    run_id: str,
    result: CommitResult,
    dataset: DatasetRow,
    connector_name: str,
    resource_name: str,
    event_id: str,
    payload_hash: str,
) -> None:
    runtime._mark_sync_run_committed(conn, ctx, run_id, result)
    runtime.dataset_transaction_repository.insert_webhook_event_key(
        transaction=conn,
        record=WebhookEventKeyRecord(
            webhook_event_key_id=_new_id("wehkey"),
            tenant_id=ctx.tenant_id,
            dataset_id=dataset["id"],
            connector_name=connector_name,
            resource_name=resource_name,
            event_id=event_id,
            transaction_id=result.transaction_id,
            committed_version_id=result.version_id,
            payload_hash=payload_hash,
            created_at=_now(),
        ),
    )
