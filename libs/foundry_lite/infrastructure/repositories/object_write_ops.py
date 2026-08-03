"""SQL write operations for action-driven object and link edits.

The v1 setProperty path only ever updated an existing object's properties
(``update_object_target``). Action Types v2 additionally creates and soft-deletes
whole objects and creates/deletes many-to-many links, all within one action
commit transaction. These functions carry that SQL so the repository class stays
small; each mirrors the invariants the source-indexing path already upholds — a
monotonic object change sequence, an appended immutable version snapshot, and
visibility governed by ``is_active``/``deleted`` (not ``index_version``, which the
read path ignores).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import and_, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from foundry_lite.application.ports.action_repository import (
    ObjectCreateWrite,
    ObjectDeleteWrite,
    ObjectLinkDeleteWrite,
    ObjectLinkWrite,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.object_change_sequence import next_object_change_sequence

_OBJECT_CONFLICT = ["tenant_id", "object_type_id", "object_id", "index_version"]
_LINK_CONFLICT = ["tenant_id", "link_type_id", "from_object_id", "to_object_id", "index_version"]


def snapshot_object_record_version(transaction: Any, tenant_id: str, record_id: str) -> None:
    """Append the current object record state to the immutable version history."""
    row = (
        transaction.execute(
            select(db.object_records).where(
                and_(db.object_records.c.tenant_id == tenant_id, db.object_records.c.id == record_id)
            )
        )
        .mappings()
        .one()
    )
    transaction.execute(
        insert(db.object_record_versions).values(
            id=f"object_record_version_{uuid4().hex}",
            tenant_id=row["tenant_id"],
            object_record_id=row["id"],
            object_type_id=row["object_type_id"],
            object_type_api_name=row["object_type_api_name"],
            object_id=row["object_id"],
            index_version=row["index_version"],
            is_active=row["is_active"],
            properties=row["properties"],
            base_properties=row["base_properties"],
            edit_properties=row["edit_properties"],
            property_versions=row["property_versions"],
            source_dataset_version_id=row["source_dataset_version_id"],
            source_hash=row["source_hash"],
            object_version=row["object_version"],
            object_change_sequence=row["object_change_sequence"],
            deleted=row["deleted"],
            deletion_reason=row["deletion_reason"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
    )


def _object_create_values(record: ObjectCreateWrite, change_sequence: int) -> dict[str, object]:
    properties = dict(record.properties)
    return {
        "id": record.object_record_id,
        "tenant_id": record.tenant_id,
        "object_type_id": record.object_type_id,
        "object_type_api_name": record.object_type_api_name,
        "object_id": record.object_id,
        "index_version": "active",
        "is_active": True,
        "properties": properties,
        "base_properties": {},
        "edit_properties": dict(properties),
        "property_versions": {name: 1 for name in properties},
        "source_dataset_version_id": None,
        "source_hash": None,
        "object_version": 1,
        "object_change_sequence": change_sequence,
        "deleted": False,
        "deletion_reason": None,
        "created_at": record.created_at,
        "updated_at": record.created_at,
    }


def _object_create_statement(transaction: Any, values: dict[str, object]) -> Any:
    # RETURNING (not rowcount) tells insert from conflict: ON CONFLICT DO NOTHING
    # reports rowcount unreliably on psycopg, so mirror the action-run insert helper.
    returning = db.object_records.c.id
    dialect = transaction.dialect.name
    if dialect == "postgresql":
        pg_statement: Any = postgres_insert(db.object_records).values(**values)
        return pg_statement.on_conflict_do_nothing(index_elements=_OBJECT_CONFLICT).returning(returning)
    if dialect == "sqlite":
        sqlite_statement: Any = sqlite_insert(db.object_records).values(**values)
        return sqlite_statement.on_conflict_do_nothing(index_elements=_OBJECT_CONFLICT).returning(returning)
    return insert(db.object_records).values(**values).returning(returning)


def create_object_record_write(transaction: Any, record: ObjectCreateWrite) -> bool:
    """Insert a new action-created object; ``False`` if the identity already exists."""
    change_sequence = next_object_change_sequence(transaction, record.tenant_id)
    values = _object_create_values(record, change_sequence)
    inserted = transaction.execute(_object_create_statement(transaction, values)).first()
    if inserted is None:
        return False
    snapshot_object_record_version(transaction, record.tenant_id, record.object_record_id)
    return True


def soft_delete_object_write(transaction: Any, record: ObjectDeleteWrite) -> bool:
    """CAS an object into the soft-deleted state; ``False`` if the expected version did not match."""
    change_sequence = next_object_change_sequence(transaction, record.tenant_id)
    result = transaction.execute(
        update(db.object_records)
        .where(
            and_(
                db.object_records.c.tenant_id == record.tenant_id,
                db.object_records.c.id == record.object_record_id,
                db.object_records.c.object_version == record.expected_object_version,
            )
        )
        .values(
            deleted=True,
            is_active=False,
            deletion_reason=record.deletion_reason,
            object_version=record.expected_object_version + 1,
            object_change_sequence=change_sequence,
            updated_at=record.updated_at,
        )
    )
    if result.rowcount != 1:
        return False
    snapshot_object_record_version(transaction, record.tenant_id, record.object_record_id)
    return True


def _object_link_values(record: ObjectLinkWrite) -> dict[str, object]:
    return {
        "id": record.link_record_id,
        "tenant_id": record.tenant_id,
        "link_type_id": record.link_type_id,
        "link_type_api_name": record.link_type_api_name,
        "index_version": "active",
        "is_active": True,
        "from_object_type_id": record.from_object_type_id,
        "from_api_name": record.from_api_name,
        "from_object_id": record.from_object_id,
        "to_object_type_id": record.to_object_type_id,
        "to_api_name": record.to_api_name,
        "to_object_id": record.to_object_id,
        "properties": {},
        "source_dataset_version_id": None,
        "link_version": 1,
        "deleted": False,
        "deletion_reason": None,
        "updated_at": record.updated_at,
    }


def _object_link_upsert_statement(transaction: Any, record: ObjectLinkWrite) -> Any:
    values = _object_link_values(record)
    reactivation = {
        "is_active": True,
        "deleted": False,
        "deletion_reason": None,
        "link_version": db.object_links.c.link_version + 1,
        "updated_at": record.updated_at,
    }
    dialect = transaction.dialect.name
    if dialect == "postgresql":
        return (
            postgres_insert(db.object_links)
            .values(**values)
            .on_conflict_do_update(index_elements=_LINK_CONFLICT, set_=reactivation)
        )
    if dialect == "sqlite":
        return (
            sqlite_insert(db.object_links)
            .values(**values)
            .on_conflict_do_update(index_elements=_LINK_CONFLICT, set_=reactivation)
        )
    return insert(db.object_links).values(**values)


def create_object_link_write(transaction: Any, record: ObjectLinkWrite) -> None:
    """Create the link, or reactivate a soft-deleted one, idempotently on link identity."""
    transaction.execute(_object_link_upsert_statement(transaction, record))


def soft_delete_object_link_write(transaction: Any, record: ObjectLinkDeleteWrite) -> bool:
    """Soft-delete the active link between two objects; ``False`` if none was active."""
    result = transaction.execute(
        update(db.object_links)
        .where(
            and_(
                db.object_links.c.tenant_id == record.tenant_id,
                db.object_links.c.link_type_id == record.link_type_id,
                db.object_links.c.from_object_id == record.from_object_id,
                db.object_links.c.to_object_id == record.to_object_id,
                db.object_links.c.is_active == True,  # noqa: E712
                db.object_links.c.deleted == False,  # noqa: E712
            )
        )
        .values(
            is_active=False,
            deleted=True,
            deletion_reason=record.deletion_reason,
            link_version=db.object_links.c.link_version + 1,
            updated_at=record.updated_at,
        )
    )
    return result.rowcount >= 1
