"""Application port contract for source registry repository."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict

from foundry_lite.application.ports.transaction_context import StatusTransition, TransactionContext

SourceConnectionKind = Literal[
    "rest",
    "rest_api",
    "postgres_jdbc",
    "sap_odata",
    "sharepoint_graph",
    "csv_upload",
    "batch_file",
    "webhook_listener",
    "debezium_cdc",
    "kafka",
    "media_upload",
]
SourceConnectionStatus = Literal["active", "disabled"]


class SourceConnectionAlreadyExistsError(Exception):
    """Raised when a source connection already exists for this tenant."""


class SourceConnectionTestAlreadyExistsError(Exception):
    """Raised when a Source connection-test idempotency key already exists."""


class SourceConnectionRow(TypedDict):
    id: str
    tenant_id: str
    source_name: str
    display_name: str
    kind: str
    target_dataset_ref: str | None
    target_media_set_id: str | None
    status: str
    config_summary: Mapping[str, object]
    config_fingerprint: str
    last_run_id: str | None
    last_workflow_run_id: str | None
    last_commit_ref: Mapping[str, object] | None
    created_at: str
    updated_at: str


class SourceConnectionTestRow(TypedDict):
    id: str
    tenant_id: str
    source_name: str
    source_type: str
    status: str
    config_fingerprint: str
    idempotency_key: str
    checks: Mapping[str, object]
    error: Mapping[str, object] | None
    operations_path: str
    started_at: str
    completed_at: str | None
    created_at: str


@dataclass(frozen=True)
class SourceConnectionRecord:
    source_id: str
    tenant_id: str
    source_name: str
    display_name: str
    kind: SourceConnectionKind
    target_dataset_ref: str | None
    target_media_set_id: str | None
    status: SourceConnectionStatus
    config_summary: Mapping[str, object]
    config_fingerprint: str
    last_run_id: str | None
    last_workflow_run_id: str | None
    last_commit_ref: Mapping[str, object] | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SourceConnectionUpdate:
    display_name: str | None = None
    target_dataset_ref: str | None = None
    target_media_set_id: str | None = None
    status: SourceConnectionStatus | None = None
    config_summary: Mapping[str, object] | None = None
    config_fingerprint: str | None = None
    last_run_id: str | None = None
    last_workflow_run_id: str | None = None
    last_commit_ref: Mapping[str, object] | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class SourceConnectionTestRecord:
    test_id: str
    tenant_id: str
    source_name: str
    source_type: str
    status: str
    config_fingerprint: str
    idempotency_key: str
    checks: Mapping[str, object]
    error: Mapping[str, object] | None
    operations_path: str
    started_at: str
    completed_at: str | None
    created_at: str


class SourceRegistryRepository(Protocol):
    """DB boundary for tenant-scoped product Source onboarding read models."""

    def create_source(
        self,
        *,
        transaction: TransactionContext,
        record: SourceConnectionRecord,
    ) -> None:
        """Persist a new source connection."""
        ...

    def update_source(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        source_name: str,
        patch: SourceConnectionUpdate,
    ) -> SourceConnectionRow | None:
        """Patch one source connection and return the updated row."""
        ...

    def update_source_status(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        source_name: str,
        transition: StatusTransition,
        expected_config_fingerprint: str,
        updated_at: str,
    ) -> SourceConnectionRow | None:
        """CAS one Source lifecycle transition and return the updated row."""
        ...

    def source_by_name(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        source_name: str,
    ) -> SourceConnectionRow | None:
        """Return one source connection in a tenant."""
        ...

    def list_sources(self, *, tenant_id: str) -> list[SourceConnectionRow]:
        """Return all source connections in stable order."""
        ...

    def create_connection_test(
        self,
        *,
        transaction: TransactionContext,
        record: SourceConnectionTestRecord,
    ) -> None:
        """Persist a started Source connection test."""
        ...

    def connection_test_by_idempotency_key(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        source_name: str,
        idempotency_key: str,
    ) -> SourceConnectionTestRow | None:
        """Return one replayable Source connection test."""
        ...

    def complete_connection_test(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        test_id: str,
        status: str,
        checks: Mapping[str, object],
        error: Mapping[str, object] | None,
        completed_at: str,
    ) -> SourceConnectionTestRow | None:
        """Close one running Source connection test exactly once."""
        ...

    def list_connection_tests(
        self,
        *,
        tenant_id: str,
        source_name: str,
        limit: int,
    ) -> list[SourceConnectionTestRow]:
        """Return newest connection tests for one Source."""
        ...
