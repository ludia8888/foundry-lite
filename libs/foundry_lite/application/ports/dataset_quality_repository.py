from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class DatasetSchemaRecord:
    schema_id: str
    dataset_id: str
    version: int
    schema_json: dict[str, Any]
    schema_hash: str
    created_at: str


@dataclass(frozen=True)
class DatasetCheckRecord:
    check_id: str
    tenant_id: str
    dataset_id: str
    name: str
    check_type: str
    config: dict[str, Any]
    severity: str
    enabled: bool


@dataclass(frozen=True)
class DatasetCheckResultRecord:
    check_result_id: str
    tenant_id: str
    check_id: str
    run_id: str
    transaction_id: str
    status: str
    details: dict[str, Any]
    created_at: str


class DatasetQualityRepository(Protocol):
    """DB boundary for dataset schema registry and quality check results."""

    def schema_by_hash(
        self,
        *,
        transaction: Any,
        dataset_id: str,
        schema_hash: str,
    ) -> dict[str, Any] | None:
        """Return an existing schema row matching a dataset_id + schema_hash, or None."""
        ...

    def latest_schema_version(self, *, transaction: Any, dataset_id: str) -> int | None:
        """Return the highest schema version number for a dataset, or None when no schemas exist."""
        ...

    def insert_schema(self, *, transaction: Any, record: DatasetSchemaRecord) -> None:
        """Persist one dataset schema row inside the caller transaction."""
        ...

    def check_by_name(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        dataset_id: str,
        name: str,
    ) -> dict[str, Any] | None:
        """Return an existing dataset check row by tenant + dataset + canonical name, or None."""
        ...

    def insert_check(self, *, transaction: Any, record: DatasetCheckRecord) -> None:
        """Persist one dataset check definition inside the caller transaction."""
        ...

    def insert_check_result(self, *, transaction: Any, record: DatasetCheckResultRecord) -> None:
        """Persist one dataset check result inside the caller transaction."""
        ...
