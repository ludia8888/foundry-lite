from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, TypedDict

from foundry_lite.application.ports.transaction_context import TransactionContext

DatasetSchemaJson = Mapping[str, object]
DatasetCheckConfig = Mapping[str, object]
DatasetCheckResult = dict[str, object]


class DatasetSchemaRow(TypedDict):
    id: str
    dataset_id: str
    version: int
    schema_json: DatasetSchemaJson
    schema_hash: str
    created_at: str


@dataclass(frozen=True)
class DatasetSchemaReference:
    schema_id: str
    version: int


class DatasetCheckRow(TypedDict):
    id: str
    tenant_id: str
    dataset_id: str
    name: str
    check_type: str
    config: DatasetCheckConfig
    severity: str
    enabled: bool


class DatasetCheckResultRow(TypedDict):
    id: str
    tenant_id: str
    check_id: str
    run_id: str
    transaction_id: str
    checked_manifest_hash: str
    validated_against_schema_version_id: str
    validated_against_schema_version: int
    status: str
    details: DatasetCheckResult
    created_at: str


@dataclass(frozen=True)
class DatasetSchemaRecord:
    schema_id: str
    dataset_id: str
    version: int
    schema_json: DatasetSchemaJson
    schema_hash: str
    created_at: str


@dataclass(frozen=True)
class DatasetCheckRecord:
    check_id: str
    tenant_id: str
    dataset_id: str
    name: str
    check_type: str
    config: DatasetCheckConfig
    severity: str
    enabled: bool


@dataclass(frozen=True)
class DatasetCheckResultRecord:
    check_result_id: str
    tenant_id: str
    check_id: str
    run_id: str
    transaction_id: str
    checked_manifest_hash: str
    validated_against_schema_version_id: str
    validated_against_schema_version: int
    status: str
    details: DatasetCheckResult
    created_at: str


class DatasetQualityRepository(Protocol):
    """DB boundary for dataset schema registry and quality check results."""

    def schema_by_hash(
        self,
        *,
        transaction: TransactionContext,
        dataset_id: str,
        schema_hash: str,
    ) -> DatasetSchemaRow | None:
        """Return an existing schema row matching a dataset_id + schema_hash, or None."""
        ...

    def latest_schema_version(self, *, transaction: TransactionContext, dataset_id: str) -> int | None:
        """Return the highest schema version number for a dataset, or None when no schemas exist."""
        ...

    def insert_schema(self, *, transaction: TransactionContext, record: DatasetSchemaRecord) -> None:
        """Persist one dataset schema row inside the caller transaction."""
        ...

    def check_by_name(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        dataset_id: str,
        name: str,
    ) -> DatasetCheckRow | None:
        """Return an existing dataset check row by tenant + dataset + canonical name, or None."""
        ...

    def insert_check(self, *, transaction: TransactionContext, record: DatasetCheckRecord) -> None:
        """Persist one dataset check definition inside the caller transaction."""
        ...

    def insert_check_result(self, *, transaction: TransactionContext, record: DatasetCheckResultRecord) -> None:
        """Persist one dataset check result inside the caller transaction."""
        ...

    def check_results_for_transaction(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        transaction_id: str,
    ) -> list[DatasetCheckResultRow]:
        """Return tenant-scoped quality results for one dataset transaction."""
        ...
