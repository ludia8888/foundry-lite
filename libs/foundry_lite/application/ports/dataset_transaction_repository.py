from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict

from foundry_lite.application.ports.transaction_context import TransactionContext

DatasetRunKind = Literal["sync", "transform", "materialization"]
DeadLetterRecordStatus = Literal["QUARANTINED", "REPLAY_REQUESTED", "REPLAYING", "RESOLVED", "DISCARDED"]
DeadLetterRecordReplayStatus = Literal["NOT_REQUESTED", "REQUESTED", "REPLAYING", "SUCCEEDED", "FAILED", "DISCARDED"]
DatasetTransactionMetadata = Mapping[str, object]
DatasetFilePartitionValues = Mapping[str, object]
DatasetRunError = Mapping[str, object]
DeadLetterRecordPayload = Mapping[str, object]


@dataclass(frozen=True)
class DatasetTransactionRecord:
    transaction_id: str
    tenant_id: str
    dataset_id: str
    branch: str
    tx_type: str
    status: str
    base_version_id: str | None
    committed_version_id: str | None
    schema_version: int | None
    created_by: str
    created_at: str
    committed_at: str | None
    metadata: DatasetTransactionMetadata


@dataclass(frozen=True)
class DatasetVersionRecord:
    version_id: str
    tenant_id: str
    dataset_id: str
    branch: str
    version_number: int
    transaction_id: str
    schema_version: int
    manifest_uri: str
    row_count: int
    byte_size: int
    status: str
    superseded_by_version_id: str | None
    created_at: str


@dataclass(frozen=True)
class DatasetFileRecord:
    file_id: str
    tenant_id: str
    dataset_version_id: str
    uri: str
    file_format: str
    row_count: int
    byte_size: int
    content_hash: str
    partition_values: DatasetFilePartitionValues


@dataclass(frozen=True)
class SyncRunRecord:
    sync_run_id: str
    tenant_id: str
    sync_name: str
    source_type: str
    output_dataset_id: str
    transaction_id: str | None
    committed_version_id: str | None
    status: str
    error: DatasetRunError | None
    created_at: str
    completed_at: str | None


class SyncRunRow(TypedDict):
    id: str
    tenant_id: str
    sync_name: str
    source_type: str
    output_dataset_id: str
    transaction_id: str | None
    committed_version_id: str | None
    status: str
    error: DatasetRunError | None
    created_at: str
    completed_at: str | None


@dataclass(frozen=True)
class DeadLetterRecord:
    dead_letter_record_id: str
    tenant_id: str
    source_event_id: str
    source_dataset_version_id: str | None
    source_run_id: str | None
    payload: DeadLetterRecordPayload
    payload_hash: str
    schema_version: int | None
    transform_version: str | None
    error_kind: str
    error_message: str
    event_time: str | None
    ingested_at: str
    first_failed_at: str
    attempts: int
    status: DeadLetterRecordStatus
    replay_status: DeadLetterRecordReplayStatus
    replay_run_id: str | None
    is_closed_partition_affected: bool
    metadata: DatasetTransactionMetadata
    replay_idempotency_key: str | None = None
    replay_requested_at: str | None = None
    discarded_at: str | None = None
    backfill_plan: DatasetTransactionMetadata | None = None


class DatasetVersionConflictError(Exception):
    """Raised when a dataset version number is already committed."""


class DatasetTransactionRow(TypedDict):
    id: str
    tenant_id: str
    dataset_id: str
    branch: str
    tx_type: str
    status: str
    base_version_id: str | None
    committed_version_id: str | None
    schema_version: int | None
    created_by: str
    created_at: str
    committed_at: str | None
    metadata: DatasetTransactionMetadata


class DeadLetterRecordRow(TypedDict):
    id: str
    tenant_id: str
    source_event_id: str
    source_dataset_version_id: str | None
    source_run_id: str | None
    payload: DeadLetterRecordPayload
    payload_hash: str
    schema_version: int | None
    transform_version: str | None
    error_kind: str
    error_message: str
    event_time: str | None
    ingested_at: str
    first_failed_at: str
    attempts: int
    status: DeadLetterRecordStatus
    replay_status: DeadLetterRecordReplayStatus
    replay_run_id: str | None
    is_closed_partition_affected: bool
    metadata: DatasetTransactionMetadata
    replay_idempotency_key: str | None
    replay_requested_at: str | None
    discarded_at: str | None
    backfill_plan: DatasetTransactionMetadata | None


class DeadLetterRecordBackfillPlan(TypedDict):
    affectedDatasetVersionId: str | None
    is_closed_partition_affected: bool
    nextStep: str


class DeadLetterRecordRetryResult(TypedDict):
    deadLetterRecordId: str
    status: DeadLetterRecordStatus
    replayStatus: DeadLetterRecordReplayStatus
    replayRunId: str
    originDeadLetterRecordId: str
    is_idempotent_replay: bool
    replayDatasetVersionId: str | None
    rowCount: int | None
    error: DatasetRunError | None
    downstreamBackfillPlan: DeadLetterRecordBackfillPlan


class DeadLetterRecordBulkRetryFailure(TypedDict):
    deadLetterRecordId: str
    code: str
    message: str
    details: DatasetTransactionMetadata


class DeadLetterRecordBulkRetryResult(TypedDict):
    status: str
    items: list[DeadLetterRecordRetryResult]
    failedItems: list[DeadLetterRecordBulkRetryFailure]
    succeededCount: int
    failedCount: int


class DeadLetterRecordDiscardResult(TypedDict):
    deadLetterRecordId: str
    status: DeadLetterRecordStatus
    replayStatus: DeadLetterRecordReplayStatus
    discardedAt: str


class DatasetTransactionRepository(Protocol):
    """DB boundary for dataset transaction state, committed versions, and files."""

    def create_open_transaction(self, *, transaction: TransactionContext, record: DatasetTransactionRecord) -> None:
        """Persist a new OPEN dataset transaction inside the caller transaction."""
        ...

    def transaction_by_id(
        self, *, transaction: TransactionContext, transaction_id: str
    ) -> DatasetTransactionRow | None:
        """Return a dataset transaction row by id inside the caller transaction."""
        ...

    def list_open_transactions(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        created_before: str,
    ) -> list[DatasetTransactionRow]:
        """Return OPEN dataset transactions created before a cutoff for stale-tx recovery."""
        ...

    def abort_transaction(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        transaction_id: str,
        metadata: DatasetTransactionMetadata,
    ) -> None:
        """Mark a dataset transaction aborted inside the caller transaction."""
        ...

    def lock_dataset_for_version_allocation(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        dataset_id: str,
    ) -> None:
        """Lock one dataset row before assigning the next branch version number."""
        ...

    def insert_version(self, *, transaction: TransactionContext, record: DatasetVersionRecord) -> None:
        """Persist a committed dataset version row inside the caller transaction."""
        ...

    def insert_file(self, *, transaction: TransactionContext, record: DatasetFileRecord) -> None:
        """Persist a dataset file row inside the caller transaction."""
        ...

    def commit_transaction(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        transaction_id: str,
        committed_version_id: str,
        schema_version: int,
        committed_at: str,
        metadata: DatasetTransactionMetadata | None = None,
    ) -> bool:
        """Mark an OPEN dataset transaction committed inside the caller transaction."""
        ...

    def latest_committed_transaction(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        dataset_id: str,
    ) -> DatasetTransactionRow | None:
        """Return the latest committed dataset transaction for resume/checkpoint lookups."""
        ...

    def committed_transaction_by_version(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        committed_version_id: str,
    ) -> DatasetTransactionRow | None:
        """Return the committed dataset transaction that produced one dataset version."""
        ...

    def committed_webhook_transaction_by_event(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        dataset_id: str,
        connector_name: str,
        resource_name: str,
        event_id: str,
    ) -> DatasetTransactionRow | None:
        """Return the committed webhook transaction for an already-seen event id."""
        ...

    def abort_open_transaction_and_fail_run(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        transaction_id: str,
        run_id: str,
        run_kind: DatasetRunKind,
        error: DatasetRunError,
        completed_at: str,
    ) -> bool:
        """Abort an OPEN transaction and fail the associated run inside the caller transaction."""
        ...

    def insert_sync_run(self, *, transaction: TransactionContext, record: SyncRunRecord) -> None:
        """Persist a newly received sync run inside the caller transaction."""
        ...

    def sync_run_by_id(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        sync_run_id: str,
    ) -> SyncRunRow | None:
        """Return one tenant-scoped sync run for replay/idempotency recovery."""
        ...

    def insert_dead_letter_record(self, *, transaction: TransactionContext, record: DeadLetterRecord) -> bool:
        """Persist one tenant-scoped bad input record, returning False for an idempotent duplicate."""
        ...

    def list_dead_letter_records(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        status: DeadLetterRecordStatus | None = None,
    ) -> list[DeadLetterRecordRow]:
        """Return tenant-scoped bad input records for operations evidence."""
        ...

    def dead_letter_record_by_id(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        record_id: str,
    ) -> DeadLetterRecordRow | None:
        """Return one tenant-scoped bad input record for operations detail."""
        ...

    def update_dead_letter_record_replay_requested(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        record_id: str,
        replay_run_id: str,
        replay_idempotency_key: str,
        replay_requested_at: str,
        metadata: DatasetTransactionMetadata,
        backfill_plan: DatasetTransactionMetadata,
    ) -> DeadLetterRecordRow | None:
        """Mark one record replay-requested and return the updated row."""
        ...

    def discard_dead_letter_record(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        record_id: str,
        discarded_at: str,
        metadata: DatasetTransactionMetadata,
    ) -> DeadLetterRecordRow | None:
        """Mark one record discarded and return the updated row."""
        ...

    def update_dead_letter_record_replay_succeeded(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        record_id: str,
        replay_run_id: str,
        metadata: DatasetTransactionMetadata,
    ) -> DeadLetterRecordRow | None:
        """Mark one record resolved after a replay commit and return the row."""
        ...

    def update_dead_letter_record_replay_failed(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        record_id: str,
        replay_run_id: str,
        metadata: DatasetTransactionMetadata,
    ) -> DeadLetterRecordRow | None:
        """Mark one record replay-failed and return the row."""
        ...

    def update_sync_run_terminal(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        sync_run_id: str,
        status: str,
        committed_version_id: str | None,
        completed_at: str,
    ) -> None:
        """Mark a sync run terminal inside the caller transaction."""
        ...
