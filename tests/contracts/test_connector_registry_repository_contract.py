from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from foundry_lite.application.ports.connector_registry_repository import (
    ConnectorConnectionRecord,
    ConnectorConnectionRow,
    ConnectorConnectionUpdate,
    ConnectorRegistryRepository,
    ConnectorResourceRecord,
    ConnectorResourceRow,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyConnectorRegistryRepository
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine


class ConnectorRegistryHarness(Protocol):
    @property
    def repository(self) -> ConnectorRegistryRepository: ...

    def transaction(self) -> AbstractContextManager[Any]: ...

    def connection_rows(self) -> list[dict[str, Any]]: ...

    def resource_rows(self) -> list[dict[str, Any]]: ...


@dataclass
class FakeConnectorRegistryRepository:
    connections: list[ConnectorConnectionRow] = field(default_factory=list)
    resources: list[ConnectorResourceRow] = field(default_factory=list)

    def create_connection(self, *, transaction: Any, record: ConnectorConnectionRecord) -> None:
        del transaction
        self.connections.append(_connection_row(record))

    def update_connection(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        connector_name: str,
        patch: ConnectorConnectionUpdate,
    ) -> ConnectorConnectionRow | None:
        del transaction
        for row in self.connections:
            if row["tenant_id"] == tenant_id and row["connector_name"] == connector_name:
                cast(dict[str, Any], row).update(_patch_values(patch))
                return cast(ConnectorConnectionRow, dict(row))
        return None

    def connection_by_name(
        self, *, transaction: Any, tenant_id: str, connector_name: str
    ) -> ConnectorConnectionRow | None:
        del transaction
        for row in self.connections:
            if row["tenant_id"] == tenant_id and row["connector_name"] == connector_name:
                return cast(ConnectorConnectionRow, dict(row))
        return None

    def list_connections(self, *, tenant_id: str) -> list[ConnectorConnectionRow]:
        rows = [cast(ConnectorConnectionRow, dict(row)) for row in self.connections if row["tenant_id"] == tenant_id]
        return sorted(rows, key=lambda row: row["connector_name"])

    def upsert_resource(self, *, transaction: Any, record: ConnectorResourceRecord) -> ConnectorResourceRow:
        del transaction
        existing = self.resource_by_name(
            transaction=None,
            tenant_id=record.tenant_id,
            connector_name=record.connector_name,
            resource_name=record.resource_name,
        )
        if existing is None:
            self.resources.append(_resource_row(record))
        else:
            for row in self.resources:
                if _is_resource(row, record.tenant_id, record.connector_name, record.resource_name):
                    cast(dict[str, Any], row).update(_resource_patch(record))
                    break
        resolved = self.resource_by_name(
            transaction=None,
            tenant_id=record.tenant_id,
            connector_name=record.connector_name,
            resource_name=record.resource_name,
        )
        assert resolved is not None
        return resolved

    def resource_by_name(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        connector_name: str,
        resource_name: str,
    ) -> ConnectorResourceRow | None:
        del transaction
        for row in self.resources:
            if _is_resource(row, tenant_id, connector_name, resource_name):
                return cast(ConnectorResourceRow, dict(row))
        return None

    def resources_for_connection(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        connector_name: str,
    ) -> list[ConnectorResourceRow]:
        del transaction
        rows = [
            cast(ConnectorResourceRow, dict(row))
            for row in self.resources
            if row["tenant_id"] == tenant_id and row["connector_name"] == connector_name
        ]
        return sorted(rows, key=lambda row: row["resource_name"])


@dataclass
class FakeConnectorRegistryHarness:
    repository: FakeConnectorRegistryRepository = field(default_factory=FakeConnectorRegistryRepository)

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        yield self

    def connection_rows(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.repository.connections]

    def resource_rows(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.repository.resources]


@dataclass
class SqlAlchemyConnectorRegistryHarness:
    engine: Engine
    repository: SqlAlchemyConnectorRegistryRepository

    @classmethod
    def create(cls, tmp_path: Path) -> SqlAlchemyConnectorRegistryHarness:
        engine = create_engine(f"sqlite:///{tmp_path / 'connector_registry.db'}", future=True)
        db.create_database(engine)
        return cls(engine=engine, repository=SqlAlchemyConnectorRegistryRepository(engine))

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self.engine.begin() as conn:
            yield conn

    def connection_rows(self) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            return [dict(row) for row in conn.execute(select(db.connector_connections)).mappings().all()]

    def resource_rows(self) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            return [dict(row) for row in conn.execute(select(db.connector_resources)).mappings().all()]


@pytest.fixture(params=["fake", "sqlalchemy"])
def harness(request: pytest.FixtureRequest, tmp_path: Path) -> ConnectorRegistryHarness:
    if request.param == "fake":
        return FakeConnectorRegistryHarness()
    return SqlAlchemyConnectorRegistryHarness.create(tmp_path)


def test_connection_create_list_get_and_update_are_tenant_scoped(harness: ConnectorRegistryHarness) -> None:
    with harness.transaction() as txn:
        harness.repository.create_connection(transaction=txn, record=_connection_record("tenant-a", "erp"))
        harness.repository.create_connection(transaction=txn, record=_connection_record("tenant-b", "erp"))
        updated = harness.repository.update_connection(
            transaction=txn,
            tenant_id="tenant-a",
            connector_name="erp",
            patch=ConnectorConnectionUpdate(
                display_name="ERP Updated",
                base_url="https://api.example.test/v2",
                auth={"mode": "bearer", "tokenSecretRef": "erp-token-v2"},
                rate_limit_per_minute=30,
                allow_private_network=False,
                status="active",
                config_fingerprint="sha256:updated",
                updated_at="2026-06-28T00:00:01Z",
            ),
        )
        tenant_b_row = harness.repository.connection_by_name(
            transaction=txn,
            tenant_id="tenant-b",
            connector_name="erp",
        )
    tenant_a_rows = harness.repository.list_connections(tenant_id="tenant-a")

    assert updated is not None
    assert updated["display_name"] == "ERP Updated"
    assert updated["auth"] == {"mode": "bearer", "tokenSecretRef": "erp-token-v2"}
    assert [row["tenant_id"] for row in tenant_a_rows] == ["tenant-a"]
    assert tenant_b_row is not None
    assert tenant_b_row["display_name"] == "ERP"
    assert len(harness.connection_rows()) == 2


def test_resource_upsert_round_trips_and_rewrites_same_resource(harness: ConnectorRegistryHarness) -> None:
    with harness.transaction() as txn:
        harness.repository.create_connection(transaction=txn, record=_connection_record("tenant-a", "erp"))
        first = harness.repository.upsert_resource(transaction=txn, record=_resource_record("/orders"))
        second = harness.repository.upsert_resource(
            transaction=txn,
            record=_resource_record("/orders/v2", schema_columns=("id", "amount"), fingerprint="sha256:resource-v2"),
        )
        resources = harness.repository.resources_for_connection(
            transaction=txn,
            tenant_id="tenant-a",
            connector_name="erp",
        )

    assert first["resource_path"] == "/orders"
    assert second["resource_path"] == "/orders/v2"
    assert second["schema_columns"] == ["id", "amount"]
    assert [row["resource_name"] for row in resources] == ["orders"]
    assert len(harness.resource_rows()) == 1


def test_resource_lookup_is_tenant_scoped(harness: ConnectorRegistryHarness) -> None:
    with harness.transaction() as txn:
        harness.repository.upsert_resource(transaction=txn, record=_resource_record("/orders", tenant_id="tenant-a"))
        harness.repository.upsert_resource(transaction=txn, record=_resource_record("/orders", tenant_id="tenant-b"))
        tenant_a_resource = harness.repository.resource_by_name(
            transaction=txn,
            tenant_id="tenant-a",
            connector_name="erp",
            resource_name="orders",
        )
        tenant_b_resources = harness.repository.resources_for_connection(
            transaction=txn,
            tenant_id="tenant-b",
            connector_name="erp",
        )

    assert tenant_a_resource is not None
    assert tenant_a_resource["tenant_id"] == "tenant-a"
    assert [row["tenant_id"] for row in tenant_b_resources] == ["tenant-b"]


def _connection_record(tenant_id: str, connector_name: str) -> ConnectorConnectionRecord:
    return ConnectorConnectionRecord(
        connection_id=f"conn_{tenant_id}_{connector_name}",
        tenant_id=tenant_id,
        connector_name=connector_name,
        display_name="ERP",
        kind="rest",
        base_url="https://api.example.test",
        auth={"mode": "bearer", "tokenSecretRef": "erp-token"},
        rate_limit_per_minute=60,
        allow_private_network=False,
        status="active",
        config_fingerprint="sha256:connection",
        created_at="2026-06-28T00:00:00Z",
        updated_at="2026-06-28T00:00:00Z",
    )


def _resource_record(
    resource_path: str,
    *,
    tenant_id: str = "tenant-a",
    schema_columns: tuple[str, ...] = ("id",),
    fingerprint: str = "sha256:resource",
) -> ConnectorResourceRecord:
    return ConnectorResourceRecord(
        resource_id=f"resource_{tenant_id}_orders",
        tenant_id=tenant_id,
        connector_name="erp",
        resource_name="orders",
        dataset_ref="raw.orders",
        resource_path=resource_path,
        pagination={"strategy": "cursor", "itemsPath": "items"},
        schema_columns=schema_columns,
        primary_key=("id",),
        config_fingerprint=fingerprint,
        created_at="2026-06-28T00:00:00Z",
        updated_at="2026-06-28T00:00:00Z",
    )


def _connection_row(record: ConnectorConnectionRecord) -> ConnectorConnectionRow:
    return cast(
        ConnectorConnectionRow,
        {
            "id": record.connection_id,
            "tenant_id": record.tenant_id,
            "connector_name": record.connector_name,
            "display_name": record.display_name,
            "kind": record.kind,
            "base_url": record.base_url,
            "auth": dict(record.auth),
            "rate_limit_per_minute": record.rate_limit_per_minute,
            "allow_private_network": record.allow_private_network,
            "status": record.status,
            "config_fingerprint": record.config_fingerprint,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        },
    )


def _resource_row(record: ConnectorResourceRecord) -> ConnectorResourceRow:
    return cast(
        ConnectorResourceRow,
        {
            "id": record.resource_id,
            "tenant_id": record.tenant_id,
            "connector_name": record.connector_name,
            "resource_name": record.resource_name,
            "dataset_ref": record.dataset_ref,
            "resource_path": record.resource_path,
            "pagination": dict(record.pagination),
            "schema_columns": list(record.schema_columns),
            "primary_key": list(record.primary_key),
            "config_fingerprint": record.config_fingerprint,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        },
    )


def _patch_values(patch: ConnectorConnectionUpdate) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "display_name": patch.display_name,
            "base_url": patch.base_url,
            "auth": dict(patch.auth) if patch.auth is not None else None,
            "rate_limit_per_minute": patch.rate_limit_per_minute,
            "allow_private_network": patch.allow_private_network,
            "status": patch.status,
            "config_fingerprint": patch.config_fingerprint,
            "updated_at": patch.updated_at,
        }.items()
        if value is not None
    }


def _resource_patch(record: ConnectorResourceRecord) -> dict[str, Any]:
    return {
        "dataset_ref": record.dataset_ref,
        "resource_path": record.resource_path,
        "pagination": dict(record.pagination),
        "schema_columns": list(record.schema_columns),
        "primary_key": list(record.primary_key),
        "config_fingerprint": record.config_fingerprint,
        "updated_at": record.updated_at,
    }


def _is_resource(row: ConnectorResourceRow, tenant_id: str, connector_name: str, resource_name: str) -> bool:
    return (
        row["tenant_id"] == tenant_id
        and row["connector_name"] == connector_name
        and row["resource_name"] == resource_name
    )
