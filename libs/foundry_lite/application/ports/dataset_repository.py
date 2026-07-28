"""Application port contract for dataset repository."""

from __future__ import annotations

from typing import Protocol, TypedDict

from foundry_lite.application.ports.transaction_context import TransactionContext


class DatasetAlreadyExistsError(Exception):
    """Raised when the metadata store rejects a duplicate active dataset."""


DatasetPartitionSpec = list[str]
DatasetSortOrder = list[str]


class DatasetRow(TypedDict):
    id: str
    tenant_id: str
    namespace: str
    name: str
    description: str | None
    storage_kind: str
    storage_uri: str | None
    owner_team: str | None
    classification: str | None
    status: str
    primary_key: list[str]
    partition_spec: DatasetPartitionSpec
    sort_order: DatasetSortOrder
    target_file_size_bytes: int | None
    created_at: str
    updated_at: str


class DatasetRepository(Protocol):
    """DB boundary for dataset registry metadata."""

    def create_dataset(
        self,
        *,
        transaction: TransactionContext,
        dataset_id: str,
        tenant_id: str,
        namespace: str,
        name: str,
        description: str | None,
        storage_kind: str,
        storage_uri: str,
        owner_team: str | None,
        classification: str | None,
        primary_key: list[str],
        partition_spec: DatasetPartitionSpec,
        sort_order: DatasetSortOrder,
        target_file_size_bytes: int | None,
        created_at: str,
        updated_at: str,
    ) -> None:
        """Persist a dataset registry row."""
        ...

    def find_active_dataset(self, *, tenant_id: str, namespace: str, name: str) -> DatasetRow | None:
        """Return the active dataset row for a tenant/ref pair."""
        ...

    def active_dataset_by_ref(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        namespace: str,
        name: str,
    ) -> DatasetRow | None:
        """Return one active tenant-scoped dataset inside the caller transaction."""
        ...

    def dataset_by_id(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        dataset_id: str,
    ) -> DatasetRow | None:
        """Return one tenant-scoped dataset row inside the caller transaction."""
        ...

    def list_active_datasets(self, *, tenant_id: str) -> list[DatasetRow]:
        """Return all active dataset rows for one tenant in stable order."""
        ...
