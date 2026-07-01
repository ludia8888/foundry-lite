"""Application port contract for source registry repository."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict

from foundry_lite.application.ports.transaction_context import TransactionContext

SourceConnectionKind = Literal["rest", "csv_upload", "batch_file", "webhook_listener", "debezium_cdc", "media_upload"]
SourceConnectionStatus = Literal["active", "disabled"]


class SourceConnectionAlreadyExistsError(Exception):
    """Raised when a source connection already exists for this tenant."""


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
