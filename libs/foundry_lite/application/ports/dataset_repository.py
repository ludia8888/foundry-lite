from __future__ import annotations

from typing import Any, Protocol

from foundry_lite.application.ports.transaction_context import TransactionContext


class DatasetAlreadyExistsError(Exception):
    """Raised when the metadata store rejects a duplicate active dataset."""


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
        created_at: str,
        updated_at: str,
    ) -> None:
        """Persist a dataset registry row."""
        ...

    def find_active_dataset(self, *, tenant_id: str, namespace: str, name: str) -> dict[str, Any] | None:
        """Return the active dataset row for a tenant/ref pair."""
        ...
