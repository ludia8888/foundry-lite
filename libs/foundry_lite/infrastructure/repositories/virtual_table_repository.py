"""SQLAlchemy registry of virtual-table pointers (metadata only, never external rows)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import Engine, delete, insert, select
from sqlalchemy.exc import IntegrityError

from foundry_lite.application.ports.virtual_table import (
    VirtualTableAlreadyExistsError,
    VirtualTableColumn,
    VirtualTableRecord,
    VirtualTableSchema,
)
from foundry_lite.infrastructure.schema import virtual_tables


class SqlAlchemyVirtualTableRepository:
    """Durable ``VirtualTableRepository``.

    Registration is unique per (tenant, parent, name): two pointers with the same name in the
    same folder would make a downstream reference ambiguous, so the second one is a conflict
    rather than an overwrite.
    """

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def register(self, record: VirtualTableRecord) -> VirtualTableRecord:
        statement = insert(virtual_tables).values(
            id=record.rid,
            tenant_id=record.tenant_id,
            name=record.name,
            parent_rid=record.parent_rid,
            connection_rid=record.connection_rid,
            config=dict(record.config),
            pinned_schema=_schema_payload(record.schema),
            markings=list(record.markings),
            created_at=record.created_at,
        )
        try:
            with self.engine.begin() as connection:
                connection.execute(statement)
        except IntegrityError as error:
            raise VirtualTableAlreadyExistsError(
                f"virtual table already registered: parent={record.parent_rid} name={record.name}"
            ) from error
        return record

    def get(self, *, tenant_id: str, rid: str) -> VirtualTableRecord | None:
        statement = select(virtual_tables).where(virtual_tables.c.tenant_id == tenant_id, virtual_tables.c.id == rid)
        with self.engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        return _record(row) if row is not None else None

    def list_for_connection(self, *, tenant_id: str, connection_rid: str) -> tuple[VirtualTableRecord, ...]:
        statement = (
            select(virtual_tables)
            .where(
                virtual_tables.c.tenant_id == tenant_id,
                virtual_tables.c.connection_rid == connection_rid,
            )
            .order_by(virtual_tables.c.name)
        )
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return tuple(_record(row) for row in rows)

    def delete(self, *, tenant_id: str, rid: str) -> None:
        statement = delete(virtual_tables).where(virtual_tables.c.tenant_id == tenant_id, virtual_tables.c.id == rid)
        with self.engine.begin() as connection:
            connection.execute(statement)


def _schema_payload(schema: VirtualTableSchema) -> dict[str, object]:
    return {
        "columns": [
            {"name": column.name, "dataType": column.data_type, "isNullable": column.is_nullable}
            for column in schema.columns
        ]
    }


def _pinned_schema(raw: object) -> VirtualTableSchema:
    payload = raw if isinstance(raw, Mapping) else {}
    columns = payload.get("columns")
    if not isinstance(columns, list):
        return VirtualTableSchema()
    return VirtualTableSchema(
        columns=tuple(
            VirtualTableColumn(
                name=str(item.get("name", "")),
                data_type=str(item.get("dataType", "")),
                is_nullable=bool(item.get("isNullable", True)),
            )
            for item in columns
            if isinstance(item, Mapping)
        )
    )


def _record(row: Any) -> VirtualTableRecord:
    markings = row["markings"]
    return VirtualTableRecord(
        rid=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        name=str(row["name"]),
        parent_rid=str(row["parent_rid"]),
        connection_rid=str(row["connection_rid"]),
        config=row["config"] if isinstance(row["config"], Mapping) else {},
        schema=_pinned_schema(row["pinned_schema"]),
        markings=tuple(str(value) for value in markings) if isinstance(markings, list) else (),
        created_at=str(row["created_at"]),
    )
