from __future__ import annotations

from typing import Any, Protocol

from foundry_lite.application.ports.transaction_context import TransactionContext


class DatasetVersionRepository(Protocol):
    """DB read boundary for committed dataset versions and schemas."""

    def next_version_number(self, *, transaction: TransactionContext, dataset_id: str) -> int:
        """Return the next committed version number inside the caller transaction."""
        ...

    def schema_for_version(self, *, dataset_id: str, schema_version: int) -> dict[str, Any] | None:
        """Return a dataset schema row by dataset id and schema version."""
        ...

    def latest_version_by_dataset_id(
        self, *, transaction: TransactionContext, dataset_id: str
    ) -> dict[str, Any] | None:
        """Return the latest committed dataset version row inside the caller transaction."""
        ...

    def version_by_id(self, *, transaction: TransactionContext, version_id: str) -> dict[str, Any] | None:
        """Return a committed dataset version row by id inside the caller transaction."""
        ...

    def list_versions(self, *, dataset_id: str) -> list[dict[str, Any]]:
        """Return committed dataset versions in version order."""
        ...
