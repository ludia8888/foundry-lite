"""Application port contract for dataset quality repository."""

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
    data_contract_version_id: str | None
    run_id: str
    transaction_id: str
    checked_manifest_hash: str
    validated_against_schema_version_id: str
    validated_against_schema_version: int
    status: str
    details: DatasetCheckResult
    created_at: str


class DatasetCheckResultHistoryRow(TypedDict):
    id: str
    tenant_id: str
    dataset_id: str
    check_id: str
    data_contract_version_id: str | None
    check_name: str
    check_type: str
    severity: str
    run_id: str
    transaction_id: str
    checked_manifest_hash: str
    validated_against_schema_version_id: str
    validated_against_schema_version: int
    status: str
    details: DatasetCheckResult
    created_at: str


class DatasetQualityContractCheckSnapshot(TypedDict):
    checkId: str
    name: str
    checkType: str
    config: DatasetCheckConfig
    severity: str
    enabled: bool


class DatasetQualityContractVersionRow(TypedDict):
    id: str
    tenant_id: str
    dataset_id: str
    contract_key: str
    version: int
    status: str
    owner_user_id: str | None
    description: str | None
    checks_snapshot: list[DatasetQualityContractCheckSnapshot]
    schema_version_id: str | None
    schema_version: int | None
    created_at: str
    activated_at: str | None


class DatasetCheckResultStatusCountRow(TypedDict):
    status: str
    count: int


class DatasetCheckResultTypeStatusCountRow(TypedDict):
    check_type: str
    status: str
    count: int


class DatasetQualityContractCheck(TypedDict):
    id: str
    datasetId: str
    name: str
    checkType: str
    config: DatasetCheckConfig
    severity: str
    enabled: bool


class DatasetQualityContractCheckCreateResult(TypedDict):
    check: DatasetQualityContractCheck
    isIdempotentReplay: bool


class DatasetQualityContractCheckList(TypedDict):
    datasetRef: str
    checks: list[DatasetQualityContractCheck]


class DatasetQualityResultHistoryItem(TypedDict):
    id: str
    checkId: str
    dataContractVersionId: str | None
    checkName: str
    checkType: str
    severity: str
    runId: str
    transactionId: str
    checkedManifestHash: str
    validatedAgainstSchemaVersionId: str
    validatedAgainstSchemaVersion: int
    status: str
    details: DatasetCheckResult
    createdAt: str


class DatasetQualityResultHistory(TypedDict):
    datasetRef: str
    results: list[DatasetQualityResultHistoryItem]


class DatasetQualityResultStatusCount(TypedDict):
    status: str
    count: int


class DatasetQualityResultTypeStatusCount(TypedDict):
    checkType: str
    status: str
    count: int


class DatasetQualityResultSummary(TypedDict):
    datasetRef: str
    totalResults: int
    statusCounts: list[DatasetQualityResultStatusCount]
    checkTypeStatusCounts: list[DatasetQualityResultTypeStatusCount]
    latestResults: list[DatasetQualityResultHistoryItem]


class DatasetQualityContractVersion(TypedDict):
    id: str
    datasetId: str
    datasetRef: str
    contractKey: str
    version: int
    status: str
    ownerUserId: str | None
    description: str | None
    checks: list[DatasetQualityContractCheck]
    schemaVersionId: str | None
    schemaVersion: int | None
    createdAt: str
    activatedAt: str | None


class DatasetQualityContractVersionCreateResult(TypedDict):
    contractVersion: DatasetQualityContractVersion
    isIdempotentReplay: bool


class DatasetQualityContractVersionList(TypedDict):
    datasetRef: str
    contractVersions: list[DatasetQualityContractVersion]


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
    data_contract_version_id: str | None = None


@dataclass(frozen=True)
class DatasetQualityContractVersionRecord:
    contract_version_id: str
    tenant_id: str
    dataset_id: str
    contract_key: str
    version: int
    status: str
    owner_user_id: str | None
    description: str | None
    checks_snapshot: list[DatasetQualityContractCheckSnapshot]
    schema_version_id: str | None
    schema_version: int | None
    created_at: str
    activated_at: str | None = None


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

    def latest_schema(self, *, transaction: TransactionContext, dataset_id: str) -> DatasetSchemaRow | None:
        """Return the newest schema row for a dataset, or None when no schemas exist."""
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

    def update_check(self, *, transaction: TransactionContext, record: DatasetCheckRecord) -> bool:
        """Update one tenant-scoped dataset check definition inside the caller transaction."""
        ...

    def check_by_id(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        dataset_id: str,
        check_id: str,
    ) -> DatasetCheckRow | None:
        """Return one tenant-scoped dataset quality contract check definition."""
        ...

    def checks_for_dataset(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        dataset_id: str,
    ) -> list[DatasetCheckRow]:
        """Return tenant-scoped quality contract check definitions for one dataset."""
        ...

    def insert_check_result(self, *, transaction: TransactionContext, record: DatasetCheckResultRecord) -> None:
        """Persist one dataset check result inside the caller transaction."""
        ...

    def latest_contract_version_number(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        dataset_id: str,
        contract_key: str,
    ) -> int | None:
        """Return the highest contract version number for one tenant dataset contract."""
        ...

    def insert_contract_version(
        self,
        *,
        transaction: TransactionContext,
        record: DatasetQualityContractVersionRecord,
    ) -> None:
        """Persist one immutable dataset quality contract version."""
        ...

    def contract_version_by_id(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        dataset_id: str,
        contract_version_id: str,
    ) -> DatasetQualityContractVersionRow | None:
        """Return one tenant-scoped dataset quality contract version."""
        ...

    def contract_versions_for_dataset(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        dataset_id: str,
        limit: int,
    ) -> list[DatasetQualityContractVersionRow]:
        """Return newest tenant-scoped quality contract versions for one dataset."""
        ...

    def active_contract_version(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        dataset_id: str,
        contract_key: str,
    ) -> DatasetQualityContractVersionRow | None:
        """Return the active version for one tenant dataset contract key, if any."""
        ...

    def activate_contract_version(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        dataset_id: str,
        contract_key: str,
        contract_version_id: str,
        activated_at: str,
    ) -> bool:
        """Mark one contract version active and supersede older active versions."""
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

    def check_results_for_dataset(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        dataset_id: str,
        limit: int,
    ) -> list[DatasetCheckResultHistoryRow]:
        """Return newest tenant-scoped quality result history for one dataset."""
        ...

    def check_result_status_counts_for_dataset(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        dataset_id: str,
    ) -> list[DatasetCheckResultStatusCountRow]:
        """Return tenant-scoped quality result counts by status for one dataset."""
        ...

    def check_result_type_status_counts_for_dataset(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        dataset_id: str,
    ) -> list[DatasetCheckResultTypeStatusCountRow]:
        """Return tenant-scoped quality result counts by check type and status."""
        ...
