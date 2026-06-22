from __future__ import annotations

from typing import Protocol, TypedDict

from foundry_lite.application.ports.dataset_quality_repository import DatasetSchemaRow
from foundry_lite.application.ports.transaction_context import TransactionContext


class DatasetVersionRow(TypedDict):
    id: str
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


class DatasetVersionRepository(Protocol):
    """DB read boundary for committed dataset versions and schemas."""

    def next_version_number(self, *, transaction: TransactionContext, dataset_id: str) -> int:
        """Return the next committed version number inside the caller transaction."""
        ...

    def schema_for_version(self, *, dataset_id: str, schema_version: int) -> DatasetSchemaRow | None:
        """Return a dataset schema row by dataset id and schema version."""
        ...

    def latest_version_by_dataset_id(
        self, *, transaction: TransactionContext, dataset_id: str
    ) -> DatasetVersionRow | None:
        """Return the latest committed dataset version row inside the caller transaction."""
        ...

    def version_by_id(self, *, transaction: TransactionContext, version_id: str) -> DatasetVersionRow | None:
        """Return a committed dataset version row by id inside the caller transaction."""
        ...

    def version_by_dataset_id_and_id(
        self,
        *,
        transaction: TransactionContext,
        dataset_id: str,
        version_id: str,
    ) -> DatasetVersionRow | None:
        """Return a committed dataset version row by dataset id and version id."""
        ...

    def list_versions(self, *, dataset_id: str) -> list[DatasetVersionRow]:
        """Return committed dataset versions in version order."""
        ...
