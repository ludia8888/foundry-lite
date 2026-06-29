from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict

from foundry_lite.application.ports.transaction_context import TransactionContext

ConnectorConnectionKind = Literal["rest"]
ConnectorConnectionStatus = Literal["active", "disabled"]


class ConnectorConnectionAlreadyExistsError(Exception):
    """Raised when a connector connection already exists for this tenant."""


class ConnectorConnectionRow(TypedDict):
    id: str
    tenant_id: str
    connector_name: str
    display_name: str
    kind: str
    base_url: str
    auth: Mapping[str, object]
    rate_limit_per_minute: int | None
    allow_private_network: bool
    status: str
    config_fingerprint: str
    created_at: str
    updated_at: str


class ConnectorResourceRow(TypedDict):
    id: str
    tenant_id: str
    connector_name: str
    resource_name: str
    dataset_ref: str
    resource_path: str
    pagination: Mapping[str, object]
    schema_columns: Sequence[str]
    primary_key: Sequence[str]
    config_fingerprint: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ConnectorConnectionRecord:
    connection_id: str
    tenant_id: str
    connector_name: str
    display_name: str
    kind: ConnectorConnectionKind
    base_url: str
    auth: Mapping[str, object]
    rate_limit_per_minute: int | None
    allow_private_network: bool
    status: ConnectorConnectionStatus
    config_fingerprint: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ConnectorConnectionUpdate:
    display_name: str | None = None
    base_url: str | None = None
    auth: Mapping[str, object] | None = None
    rate_limit_per_minute: int | None = None
    allow_private_network: bool | None = None
    status: ConnectorConnectionStatus | None = None
    config_fingerprint: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class ConnectorResourceRecord:
    resource_id: str
    tenant_id: str
    connector_name: str
    resource_name: str
    dataset_ref: str
    resource_path: str
    pagination: Mapping[str, object]
    schema_columns: Sequence[str]
    primary_key: Sequence[str]
    config_fingerprint: str
    created_at: str
    updated_at: str


class ConnectorRegistryRepository(Protocol):
    """DB boundary for tenant-scoped connector onboarding configuration."""

    def create_connection(
        self,
        *,
        transaction: TransactionContext,
        record: ConnectorConnectionRecord,
    ) -> None:
        """Persist a new connector connection."""
        ...

    def update_connection(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        connector_name: str,
        patch: ConnectorConnectionUpdate,
    ) -> ConnectorConnectionRow | None:
        """Patch one connector connection and return the updated row."""
        ...

    def connection_by_name(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        connector_name: str,
    ) -> ConnectorConnectionRow | None:
        """Return one connector connection in a tenant."""
        ...

    def list_connections(self, *, tenant_id: str) -> list[ConnectorConnectionRow]:
        """Return all connector connections in stable order."""
        ...

    def upsert_resource(
        self,
        *,
        transaction: TransactionContext,
        record: ConnectorResourceRecord,
    ) -> ConnectorResourceRow:
        """Create or replace a connector resource config."""
        ...

    def resource_by_name(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        connector_name: str,
        resource_name: str,
    ) -> ConnectorResourceRow | None:
        """Return one connector resource config."""
        ...

    def resources_for_connection(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        connector_name: str,
    ) -> list[ConnectorResourceRow]:
        """Return connector resources in stable order."""
        ...
