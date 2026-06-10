from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

DatasetRunKind = Literal["sync", "transform", "materialization"]


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
    metadata: dict[str, Any]


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
    partition_values: dict[str, Any]


@dataclass(frozen=True)
class SyncRunRecord:
    sync_run_id: str
    tenant_id: str
    sync_name: str
    source_type: str
    output_dataset_id: str
    transaction_id: str
    committed_version_id: str | None
    status: str
    error: dict[str, Any] | None
    created_at: str
    completed_at: str | None


class DatasetTransactionRepository(Protocol):
    """DB boundary for dataset transaction state, committed versions, and files."""

    def create_open_transaction(self, *, transaction: Any, record: DatasetTransactionRecord) -> None:
        """Persist a new OPEN dataset transaction inside the caller transaction."""
        ...

    def transaction_by_id(self, *, transaction: Any, transaction_id: str) -> dict[str, Any] | None:
        """Return a dataset transaction row by id inside the caller transaction."""
        ...

    def abort_transaction(self, *, transaction: Any, transaction_id: str, metadata: dict[str, Any]) -> None:
        """Mark a dataset transaction aborted inside the caller transaction."""
        ...

    def insert_version(self, *, transaction: Any, record: DatasetVersionRecord) -> None:
        """Persist a committed dataset version row inside the caller transaction."""
        ...

    def insert_file(self, *, transaction: Any, record: DatasetFileRecord) -> None:
        """Persist a dataset file row inside the caller transaction."""
        ...

    def commit_transaction(
        self,
        *,
        transaction: Any,
        transaction_id: str,
        committed_version_id: str,
        schema_version: int,
        committed_at: str,
    ) -> None:
        """Mark a dataset transaction committed inside the caller transaction."""
        ...

    def abort_open_transaction_and_fail_run(
        self,
        *,
        transaction_id: str,
        run_id: str,
        run_kind: DatasetRunKind,
        error: dict[str, Any],
        completed_at: str,
    ) -> None:
        """Best-effort abort for an OPEN transaction and the associated run row."""
        ...

    def insert_sync_run(self, *, transaction: Any, record: SyncRunRecord) -> None:
        """Persist a newly received sync run inside the caller transaction."""
        ...

    def update_sync_run_terminal(
        self,
        *,
        transaction: Any,
        sync_run_id: str,
        status: str,
        committed_version_id: str | None,
        completed_at: str,
    ) -> None:
        """Mark a sync run terminal inside the caller transaction."""
        ...
