from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from foundry_lite.application.ports.connector_registry_repository import (
    ConnectorConnectionAlreadyExistsError,
    ConnectorConnectionRecord,
    ConnectorConnectionRow,
    ConnectorConnectionUpdate,
    ConnectorResourceRecord,
    ConnectorResourceRow,
)
from foundry_lite.infrastructure import schema as db


class SqlAlchemyConnectorRegistryRepository:
    """SQLAlchemy implementation of connector onboarding configuration storage."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_connection(self, *, transaction: Any, record: ConnectorConnectionRecord) -> None:
        try:
            transaction.execute(
                insert(db.connector_connections).values(
                    id=record.connection_id,
                    tenant_id=record.tenant_id,
                    connector_name=record.connector_name,
                    display_name=record.display_name,
                    kind=record.kind,
                    base_url=record.base_url,
                    auth=dict(record.auth),
                    rate_limit_per_minute=record.rate_limit_per_minute,
                    allow_private_network=record.allow_private_network,
                    status=record.status,
                    config_fingerprint=record.config_fingerprint,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                )
            )
        except IntegrityError as exc:
            raise ConnectorConnectionAlreadyExistsError from exc

    def update_connection(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        connector_name: str,
        patch: ConnectorConnectionUpdate,
    ) -> ConnectorConnectionRow | None:
        values = _connection_patch_values(patch)
        if values:
            transaction.execute(
                update(db.connector_connections)
                .where(
                    and_(
                        db.connector_connections.c.tenant_id == tenant_id,
                        db.connector_connections.c.connector_name == connector_name,
                    )
                )
                .values(**values)
            )
        return self.connection_by_name(transaction=transaction, tenant_id=tenant_id, connector_name=connector_name)

    def connection_by_name(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        connector_name: str,
    ) -> ConnectorConnectionRow | None:
        row = (
            transaction.execute(
                select(db.connector_connections).where(
                    and_(
                        db.connector_connections.c.tenant_id == tenant_id,
                        db.connector_connections.c.connector_name == connector_name,
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(ConnectorConnectionRow, dict(row)) if row else None

    def list_connections(self, *, tenant_id: str) -> list[ConnectorConnectionRow]:
        with self.engine.begin() as conn:
            rows = (
                conn.execute(
                    select(db.connector_connections)
                    .where(db.connector_connections.c.tenant_id == tenant_id)
                    .order_by(db.connector_connections.c.connector_name)
                )
                .mappings()
                .all()
            )
            return [cast(ConnectorConnectionRow, dict(row)) for row in rows]

    def upsert_resource(self, *, transaction: Any, record: ConnectorResourceRecord) -> ConnectorResourceRow:
        existing = self.resource_by_name(
            transaction=transaction,
            tenant_id=record.tenant_id,
            connector_name=record.connector_name,
            resource_name=record.resource_name,
        )
        if existing is None:
            transaction.execute(
                insert(db.connector_resources).values(
                    id=record.resource_id,
                    tenant_id=record.tenant_id,
                    connector_name=record.connector_name,
                    resource_name=record.resource_name,
                    dataset_ref=record.dataset_ref,
                    resource_path=record.resource_path,
                    pagination=dict(record.pagination),
                    schema_columns=list(record.schema_columns),
                    primary_key=list(record.primary_key),
                    config_fingerprint=record.config_fingerprint,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                )
            )
        else:
            transaction.execute(
                update(db.connector_resources)
                .where(
                    and_(
                        db.connector_resources.c.tenant_id == record.tenant_id,
                        db.connector_resources.c.connector_name == record.connector_name,
                        db.connector_resources.c.resource_name == record.resource_name,
                    )
                )
                .values(
                    dataset_ref=record.dataset_ref,
                    resource_path=record.resource_path,
                    pagination=dict(record.pagination),
                    schema_columns=list(record.schema_columns),
                    primary_key=list(record.primary_key),
                    config_fingerprint=record.config_fingerprint,
                    updated_at=record.updated_at,
                )
            )
        resolved = self.resource_by_name(
            transaction=transaction,
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
        row = (
            transaction.execute(
                select(db.connector_resources).where(
                    and_(
                        db.connector_resources.c.tenant_id == tenant_id,
                        db.connector_resources.c.connector_name == connector_name,
                        db.connector_resources.c.resource_name == resource_name,
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(ConnectorResourceRow, dict(row)) if row else None

    def resources_for_connection(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        connector_name: str,
    ) -> list[ConnectorResourceRow]:
        rows = (
            transaction.execute(
                select(db.connector_resources)
                .where(
                    and_(
                        db.connector_resources.c.tenant_id == tenant_id,
                        db.connector_resources.c.connector_name == connector_name,
                    )
                )
                .order_by(db.connector_resources.c.resource_name)
            )
            .mappings()
            .all()
        )
        return [cast(ConnectorResourceRow, dict(row)) for row in rows]


def _connection_patch_values(patch: ConnectorConnectionUpdate) -> dict[str, object]:
    values: dict[str, object] = {}
    if patch.display_name is not None:
        values["display_name"] = patch.display_name
    if patch.base_url is not None:
        values["base_url"] = patch.base_url
    if patch.auth is not None:
        values["auth"] = dict(patch.auth)
    if patch.rate_limit_per_minute is not None:
        values["rate_limit_per_minute"] = patch.rate_limit_per_minute
    if patch.allow_private_network is not None:
        values["allow_private_network"] = patch.allow_private_network
    if patch.status is not None:
        values["status"] = patch.status
    if patch.config_fingerprint is not None:
        values["config_fingerprint"] = patch.config_fingerprint
    if patch.updated_at is not None:
        values["updated_at"] = patch.updated_at
    return values
