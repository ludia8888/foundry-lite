"""SQLAlchemy repository adapter for object index repository persistence."""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

from sqlalchemy import and_, delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from foundry_lite.application.ports import (
    IndexRunCursor,
    IndexRunError,
    IndexRunRecord,
    IndexRunSourceRef,
    LinkTypeRow,
    ObjectConflictRecord,
    ObjectIndexLinkRow,
    ObjectLinkInsert,
    ObjectLinkSourceDeletion,
    ObjectRecordCdcUpdate,
    ObjectRecordInsert,
    ObjectRecordRow,
    ObjectRecordSourceDeletion,
    ObjectRecordSourceUpdate,
)
from foundry_lite.application.ports.object_index_repository import IndexRunRow, IndexRunUsageRow
from foundry_lite.application.ports.transaction_context import INDEX_RUN_FAILED, INDEX_RUN_SUCCEEDED
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.object_change_sequence import next_object_change_sequence
from foundry_lite.infrastructure.repositories.status_cas import cas_status_update


class SqlAlchemyObjectIndexRepository:
    """SQLAlchemy implementation of object indexing writes and lookup reads."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def index_run_by_id(self, *, transaction: Any, tenant_id: str, run_id: str) -> IndexRunRow | None:
        row = (
            transaction.execute(
                select(db.index_runs).where(and_(db.index_runs.c.tenant_id == tenant_id, db.index_runs.c.id == run_id))
            )
            .mappings()
            .first()
        )
        return cast(IndexRunRow, dict(row)) if row else None

    def create_index_run(self, *, transaction: Any, record: IndexRunRecord) -> None:
        transaction.execute(
            insert(db.index_runs).values(
                id=record.run_id,
                tenant_id=record.tenant_id,
                object_type_id=record.object_type_id,
                object_type_api_name=record.object_type_api_name,
                trigger_type=record.trigger_type,
                source_ref=record.source_ref,
                status=record.status,
                cursor=record.cursor,
                rows_read=record.rows_read,
                objects_upserted=record.objects_upserted,
                objects_deleted=record.objects_deleted,
                links_upserted=record.links_upserted,
                error=record.error,
                started_at=record.started_at,
                completed_at=record.completed_at,
                created_at=record.created_at,
            )
        )

    def index_run_usage(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
        since: str,
    ) -> IndexRunUsageRow:
        conditions = and_(
            db.index_runs.c.tenant_id == tenant_id,
            db.index_runs.c.object_type_api_name == object_type_api_name,
            db.index_runs.c.created_at >= since,
        )
        status_rows = transaction.execute(
            select(db.index_runs.c.status, func.count()).where(conditions).group_by(db.index_runs.c.status)
        ).all()
        last_run_at = transaction.execute(select(func.max(db.index_runs.c.created_at)).where(conditions)).scalar_one()
        last_succeeded_at = transaction.execute(
            select(func.max(db.index_runs.c.completed_at)).where(
                and_(conditions, db.index_runs.c.status == "succeeded")
            )
        ).scalar_one()
        status_counts = {str(status): int(count) for status, count in status_rows}
        return {
            "status_counts": status_counts,
            "total_runs": sum(status_counts.values()),
            "last_run_at": cast(str | None, last_run_at),
            "last_succeeded_at": cast(str | None, last_succeeded_at),
        }

    def active_index_version(self, *, transaction: Any, tenant_id: str, object_type_id: str) -> str:
        pointer = self._active_index_pointer(transaction, tenant_id, object_type_id)
        if pointer is not None:
            return pointer
        return self._legacy_active_index_version(transaction, tenant_id, object_type_id)

    def _active_index_pointer(self, transaction: Any, tenant_id: str, object_type_id: str) -> str | None:
        row = (
            transaction.execute(
                select(db.object_index_versions.c.active_index_version).where(
                    and_(
                        db.object_index_versions.c.tenant_id == tenant_id,
                        db.object_index_versions.c.object_type_id == object_type_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        return str(row["active_index_version"]) if row else None

    def _legacy_active_index_version(self, transaction: Any, tenant_id: str, object_type_id: str) -> str:
        row = (
            transaction.execute(
                select(db.object_records.c.index_version)
                .where(
                    and_(
                        db.object_records.c.tenant_id == tenant_id,
                        db.object_records.c.object_type_id == object_type_id,
                        db.object_records.c.is_active == True,  # noqa: E712
                    )
                )
                .order_by(db.object_records.c.updated_at.desc(), db.object_records.c.index_version)
                .limit(1)
            )
            .mappings()
            .first()
        )
        return str(row["index_version"]) if row else "active"

    def mark_index_run_succeeded(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        run_id: str,
        rows_read: int,
        objects_upserted: int,
        objects_deleted: int,
        links_upserted: int,
        cursor: IndexRunCursor,
        completed_at: str,
        source_ref_updates: IndexRunSourceRef | None = None,
    ) -> bool:
        values: dict[str, Any] = {
            "rows_read": rows_read,
            "objects_upserted": objects_upserted,
            "objects_deleted": objects_deleted,
            "links_upserted": links_upserted,
            "cursor": cursor,
            "completed_at": completed_at,
        }
        if source_ref_updates:
            values["source_ref"] = self._merged_source_ref(transaction, tenant_id, run_id, source_ref_updates)
        return cas_status_update(
            transaction,
            db.index_runs,
            tenant_id=tenant_id,
            row_id=run_id,
            transition=INDEX_RUN_SUCCEEDED,
            values=values,
        )

    def _merged_source_ref(
        self,
        transaction: Any,
        tenant_id: str,
        run_id: str,
        source_ref_updates: IndexRunSourceRef,
    ) -> dict[str, Any]:
        stored = transaction.execute(
            select(db.index_runs.c.source_ref).where(
                and_(db.index_runs.c.tenant_id == tenant_id, db.index_runs.c.id == run_id)
            )
        ).scalar()
        merged: dict[str, Any] = dict(stored) if isinstance(stored, dict) else {}
        merged.update(source_ref_updates)
        return merged

    def mark_index_run_failed(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        run_id: str,
        error: IndexRunError,
        completed_at: str,
    ) -> bool:
        return cas_status_update(
            transaction,
            db.index_runs,
            tenant_id=tenant_id,
            row_id=run_id,
            transition=INDEX_RUN_FAILED,
            values={"error": error, "completed_at": completed_at},
        )

    def insert_object_record(self, *, transaction: Any, record: ObjectRecordInsert) -> None:
        object_change_sequence = next_object_change_sequence(transaction, record.tenant_id)
        transaction.execute(
            insert(db.object_records).values(
                id=record.record_id,
                tenant_id=record.tenant_id,
                object_type_id=record.object_type_id,
                object_type_api_name=record.object_type_api_name,
                object_id=record.object_id,
                index_version=record.index_version,
                is_active=record.is_active,
                properties=record.properties,
                base_properties=record.base_properties,
                edit_properties=record.edit_properties,
                property_versions=record.property_versions,
                source_dataset_version_id=record.source_dataset_version_id,
                source_hash=record.source_hash,
                object_version=record.object_version,
                object_change_sequence=object_change_sequence,
                deleted=record.deleted,
                deletion_reason=record.deletion_reason,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
        )
        _insert_object_record_version(
            transaction,
            values=_object_record_insert_version_values(record, object_change_sequence),
        )

    def object_record_in_index(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_id: str,
        object_type_api_name: str,
        object_id: str,
        index_version: str,
    ) -> ObjectRecordRow | None:
        row = (
            transaction.execute(
                select(db.object_records).where(
                    and_(
                        db.object_records.c.tenant_id == tenant_id,
                        db.object_records.c.object_type_id == object_type_id,
                        db.object_records.c.object_type_api_name == object_type_api_name,
                        db.object_records.c.object_id == object_id,
                        db.object_records.c.index_version == index_version,
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(ObjectRecordRow, dict(row)) if row else None

    def object_records_for_index_version(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_id: str,
        index_version: str,
    ) -> list[ObjectRecordRow]:
        rows = (
            transaction.execute(
                select(db.object_records)
                .where(
                    and_(
                        db.object_records.c.tenant_id == tenant_id,
                        db.object_records.c.object_type_id == object_type_id,
                        db.object_records.c.index_version == index_version,
                    )
                )
                .order_by(db.object_records.c.object_id)
            )
            .mappings()
            .all()
        )
        return [cast(ObjectRecordRow, dict(row)) for row in rows]

    def update_object_record_from_source(
        self,
        *,
        transaction: Any,
        record: ObjectRecordSourceUpdate,
    ) -> None:
        object_change_sequence = next_object_change_sequence(transaction, record.tenant_id)
        transaction.execute(
            update(db.object_records)
            .where(and_(db.object_records.c.tenant_id == record.tenant_id, db.object_records.c.id == record.record_id))
            .values(
                properties=record.properties,
                base_properties=record.base_properties,
                source_dataset_version_id=record.source_dataset_version_id,
                source_hash=record.source_hash,
                object_version=record.object_version,
                object_change_sequence=object_change_sequence,
                updated_at=record.updated_at,
            )
        )
        _insert_object_record_version_from_current(transaction, record.tenant_id, record.record_id)

    def mark_object_record_deleted_from_source(
        self,
        *,
        transaction: Any,
        record: ObjectRecordSourceDeletion,
    ) -> None:
        object_change_sequence = next_object_change_sequence(transaction, record.tenant_id)
        transaction.execute(
            update(db.object_records)
            .where(and_(db.object_records.c.tenant_id == record.tenant_id, db.object_records.c.id == record.record_id))
            .values(
                source_dataset_version_id=record.source_dataset_version_id,
                object_version=record.object_version,
                object_change_sequence=object_change_sequence,
                deleted=True,
                deletion_reason=record.deletion_reason,
                updated_at=record.updated_at,
            )
        )
        _insert_object_record_version_from_current(transaction, record.tenant_id, record.record_id)

    def update_object_record_from_cdc(
        self,
        *,
        transaction: Any,
        record: ObjectRecordCdcUpdate,
    ) -> bool:
        object_change_sequence = next_object_change_sequence(transaction, record.tenant_id)
        result = transaction.execute(
            update(db.object_records)
            .where(
                and_(
                    db.object_records.c.tenant_id == record.tenant_id,
                    db.object_records.c.id == record.record_id,
                    db.object_records.c.object_version == record.expected_object_version,
                )
            )
            .values(
                properties=record.properties,
                base_properties=record.base_properties,
                property_versions=record.property_versions,
                source_dataset_version_id=record.source_dataset_version_id,
                source_hash=record.source_hash,
                object_version=record.object_version,
                object_change_sequence=object_change_sequence,
                deleted=record.deleted,
                deletion_reason=record.deletion_reason,
                updated_at=record.updated_at,
            )
        )
        if result.rowcount != 1:
            return False
        _insert_object_record_version_from_current(transaction, record.tenant_id, record.record_id)
        return True

    def insert_object_conflict(self, *, transaction: Any, record: ObjectConflictRecord) -> None:
        transaction.execute(
            insert(db.object_conflicts).values(
                id=record.conflict_id,
                tenant_id=record.tenant_id,
                object_type_id=record.object_type_id,
                object_id=record.object_id,
                property_api_name=record.property_api_name,
                source_value=record.source_value,
                edit_value=record.edit_value,
                source_dataset_version_id=record.source_dataset_version_id,
                edit_id=record.edit_id,
                status=record.status,
                created_at=record.created_at,
            )
        )

    def link_types_for_object_type(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        ontology_version_id: str,
        from_object_type_id: str,
    ) -> list[LinkTypeRow]:
        rows = (
            transaction.execute(
                select(db.link_types).where(
                    and_(
                        db.link_types.c.tenant_id == tenant_id,
                        db.link_types.c.ontology_version_id == ontology_version_id,
                        db.link_types.c.from_object_type_id == from_object_type_id,
                    )
                )
            )
            .mappings()
            .all()
        )
        return [cast(LinkTypeRow, dict(row)) for row in rows]

    def object_link(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        link_type_id: str,
        from_object_id: str,
        to_object_id: str,
        index_version: str,
    ) -> ObjectIndexLinkRow | None:
        row = (
            transaction.execute(
                select(db.object_links).where(
                    and_(
                        db.object_links.c.tenant_id == tenant_id,
                        db.object_links.c.link_type_id == link_type_id,
                        db.object_links.c.from_object_id == from_object_id,
                        db.object_links.c.to_object_id == to_object_id,
                        db.object_links.c.index_version == index_version,
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(ObjectIndexLinkRow, dict(row)) if row else None

    def refresh_object_link(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        link_id: str,
        link_version: int,
        source_dataset_version_id: str,
        updated_at: str,
    ) -> None:
        transaction.execute(
            update(db.object_links)
            .where(and_(db.object_links.c.tenant_id == tenant_id, db.object_links.c.id == link_id))
            .values(
                link_version=link_version,
                source_dataset_version_id=source_dataset_version_id,
                deleted=False,
                deletion_reason=None,
                updated_at=updated_at,
            )
        )

    def insert_object_link(self, *, transaction: Any, record: ObjectLinkInsert) -> None:
        transaction.execute(
            insert(db.object_links).values(
                id=record.link_id,
                tenant_id=record.tenant_id,
                link_type_id=record.link_type_id,
                link_type_api_name=record.link_type_api_name,
                index_version=record.index_version,
                is_active=record.is_active,
                from_object_type_id=record.from_object_type_id,
                from_api_name=record.from_api_name,
                from_object_id=record.from_object_id,
                to_object_type_id=record.to_object_type_id,
                to_api_name=record.to_api_name,
                to_object_id=record.to_object_id,
                properties=record.properties,
                source_dataset_version_id=record.source_dataset_version_id,
                link_version=record.link_version,
                deleted=record.deleted,
                deletion_reason=record.deletion_reason,
                updated_at=record.updated_at,
            )
        )

    def object_links_for_index_version(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        from_object_type_id: str,
        index_version: str,
    ) -> list[ObjectIndexLinkRow]:
        rows = (
            transaction.execute(
                select(db.object_links)
                .where(
                    and_(
                        db.object_links.c.tenant_id == tenant_id,
                        db.object_links.c.from_object_type_id == from_object_type_id,
                        db.object_links.c.index_version == index_version,
                    )
                )
                .order_by(
                    db.object_links.c.link_type_id,
                    db.object_links.c.from_object_id,
                    db.object_links.c.to_object_id,
                )
            )
            .mappings()
            .all()
        )
        return [cast(ObjectIndexLinkRow, dict(row)) for row in rows]

    def mark_object_link_deleted_from_source(self, *, transaction: Any, record: ObjectLinkSourceDeletion) -> None:
        transaction.execute(
            update(db.object_links)
            .where(and_(db.object_links.c.tenant_id == record.tenant_id, db.object_links.c.id == record.link_id))
            .values(
                source_dataset_version_id=record.source_dataset_version_id,
                link_version=record.link_version,
                deleted=True,
                deletion_reason=record.deletion_reason,
                updated_at=record.updated_at,
            )
        )

    def switch_active_index_version(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_id: str,
        index_version: str,
        updated_at: str,
        expected_previous_index_version: str | None = None,
    ) -> bool:
        switched = self._upsert_active_index_pointer(
            transaction,
            tenant_id,
            object_type_id,
            index_version,
            updated_at,
            expected_previous_index_version,
        )
        if not switched:
            return False
        transaction.execute(
            update(db.object_records)
            .where(
                and_(
                    db.object_records.c.tenant_id == tenant_id,
                    db.object_records.c.object_type_id == object_type_id,
                    db.object_records.c.is_active == True,  # noqa: E712
                )
            )
            .values(is_active=False, updated_at=updated_at)
        )
        transaction.execute(
            update(db.object_links)
            .where(
                and_(
                    db.object_links.c.tenant_id == tenant_id,
                    db.object_links.c.from_object_type_id == object_type_id,
                    db.object_links.c.is_active == True,  # noqa: E712
                )
            )
            .values(is_active=False, updated_at=updated_at)
        )
        self._activate_index_version(transaction, tenant_id, object_type_id, index_version, updated_at)
        return True

    def _upsert_active_index_pointer(
        self,
        transaction: Any,
        tenant_id: str,
        object_type_id: str,
        index_version: str,
        updated_at: str,
        expected_previous_index_version: str | None,
    ) -> bool:
        values = _active_index_pointer_values(tenant_id, object_type_id, index_version, updated_at)
        dialect_name = transaction.dialect.name
        if dialect_name == "postgresql":
            return self._native_active_index_pointer_upsert(
                transaction,
                postgresql_insert(db.object_index_versions).values(**values),
                expected_previous_index_version,
            )
        if dialect_name == "sqlite":
            return self._native_active_index_pointer_upsert(
                transaction, sqlite_insert(db.object_index_versions).values(**values), expected_previous_index_version
            )
        raise RuntimeError(f"unsupported active index pointer upsert dialect: {dialect_name}")

    def _native_active_index_pointer_upsert(
        self,
        transaction: Any,
        statement: Any,
        expected_previous_index_version: str | None,
    ) -> bool:
        conflict_update: dict[str, Any] = {
            "active_index_version": statement.excluded.active_index_version,
            "updated_at": statement.excluded.updated_at,
        }
        conflict_args: dict[str, Any] = {
            "index_elements": [db.object_index_versions.c.tenant_id, db.object_index_versions.c.object_type_id],
            "set_": conflict_update,
        }
        if expected_previous_index_version is not None:
            conflict_args["where"] = db.object_index_versions.c.active_index_version == expected_previous_index_version
        result = transaction.execute(
            statement.on_conflict_do_update(**conflict_args).returning(db.object_index_versions.c.active_index_version)
        )
        return result.first() is not None

    def _activate_index_version(
        self,
        transaction: Any,
        tenant_id: str,
        object_type_id: str,
        index_version: str,
        updated_at: str,
    ) -> None:
        rows = (
            transaction.execute(
                select(db.object_records.c.id)
                .where(
                    and_(
                        db.object_records.c.tenant_id == tenant_id,
                        db.object_records.c.object_type_id == object_type_id,
                        db.object_records.c.index_version == index_version,
                    )
                )
                .order_by(db.object_records.c.object_id)
            )
            .mappings()
            .all()
        )
        for row in rows:
            object_change_sequence = next_object_change_sequence(transaction, tenant_id)
            transaction.execute(
                update(db.object_records)
                .where(and_(db.object_records.c.tenant_id == tenant_id, db.object_records.c.id == row["id"]))
                .values(
                    is_active=True,
                    object_change_sequence=object_change_sequence,
                    updated_at=updated_at,
                )
            )
            _insert_object_record_version_from_current(transaction, tenant_id, str(row["id"]))
        transaction.execute(
            update(db.object_links)
            .where(
                and_(
                    db.object_links.c.tenant_id == tenant_id,
                    db.object_links.c.from_object_type_id == object_type_id,
                    db.object_links.c.index_version == index_version,
                )
            )
            .values(is_active=True, updated_at=updated_at)
        )

    def delete_inactive_index_version(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_id: str,
        index_version: str,
    ) -> None:
        transaction.execute(
            delete(db.object_records).where(
                and_(
                    db.object_records.c.tenant_id == tenant_id,
                    db.object_records.c.object_type_id == object_type_id,
                    db.object_records.c.index_version == index_version,
                    db.object_records.c.is_active == False,  # noqa: E712
                )
            )
        )
        transaction.execute(
            delete(db.object_links).where(
                and_(
                    db.object_links.c.tenant_id == tenant_id,
                    db.object_links.c.from_object_type_id == object_type_id,
                    db.object_links.c.index_version == index_version,
                    db.object_links.c.is_active == False,  # noqa: E712
                )
            )
        )


def _active_index_pointer_values(
    tenant_id: str,
    object_type_id: str,
    index_version: str,
    updated_at: str,
) -> dict[str, str]:
    return {
        "id": f"object_index_version_{uuid4().hex}",
        "tenant_id": tenant_id,
        "object_type_id": object_type_id,
        "active_index_version": index_version,
        "updated_at": updated_at,
    }


def _object_record_insert_version_values(record: ObjectRecordInsert, object_change_sequence: int) -> dict[str, object]:
    return {
        "id": f"object_record_version_{uuid4().hex}",
        "tenant_id": record.tenant_id,
        "object_record_id": record.record_id,
        "object_type_id": record.object_type_id,
        "object_type_api_name": record.object_type_api_name,
        "object_id": record.object_id,
        "index_version": record.index_version,
        "is_active": record.is_active,
        "properties": dict(record.properties),
        "base_properties": dict(record.base_properties),
        "edit_properties": dict(record.edit_properties),
        "property_versions": dict(record.property_versions),
        "source_dataset_version_id": record.source_dataset_version_id,
        "source_hash": record.source_hash,
        "object_version": record.object_version,
        "object_change_sequence": object_change_sequence,
        "deleted": record.deleted,
        "deletion_reason": record.deletion_reason,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _insert_object_record_version_from_current(transaction: Any, tenant_id: str, record_id: str) -> None:
    row = (
        transaction.execute(
            select(db.object_records).where(
                and_(db.object_records.c.tenant_id == tenant_id, db.object_records.c.id == record_id)
            )
        )
        .mappings()
        .one()
    )
    _insert_object_record_version(transaction, values=_object_record_row_version_values(row))


def _object_record_row_version_values(row: Any) -> dict[str, object]:
    return {
        "id": f"object_record_version_{uuid4().hex}",
        "tenant_id": row["tenant_id"],
        "object_record_id": row["id"],
        "object_type_id": row["object_type_id"],
        "object_type_api_name": row["object_type_api_name"],
        "object_id": row["object_id"],
        "index_version": row["index_version"],
        "is_active": row["is_active"],
        "properties": row["properties"],
        "base_properties": row["base_properties"],
        "edit_properties": row["edit_properties"],
        "property_versions": row["property_versions"],
        "source_dataset_version_id": row["source_dataset_version_id"],
        "source_hash": row["source_hash"],
        "object_version": row["object_version"],
        "object_change_sequence": row["object_change_sequence"],
        "deleted": row["deleted"],
        "deletion_reason": row["deletion_reason"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _insert_object_record_version(transaction: Any, *, values: dict[str, object]) -> None:
    transaction.execute(
        insert(db.object_record_versions).values(
            id=values["id"],
            tenant_id=values["tenant_id"],
            object_record_id=values["object_record_id"],
            object_type_id=values["object_type_id"],
            object_type_api_name=values["object_type_api_name"],
            object_id=values["object_id"],
            index_version=values["index_version"],
            is_active=values["is_active"],
            properties=values["properties"],
            base_properties=values["base_properties"],
            edit_properties=values["edit_properties"],
            property_versions=values["property_versions"],
            source_dataset_version_id=values["source_dataset_version_id"],
            source_hash=values["source_hash"],
            object_version=values["object_version"],
            object_change_sequence=values["object_change_sequence"],
            deleted=values["deleted"],
            deletion_reason=values["deletion_reason"],
            created_at=values["created_at"],
            updated_at=values["updated_at"],
        )
    )
