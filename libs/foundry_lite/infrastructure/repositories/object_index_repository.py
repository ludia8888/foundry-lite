from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, insert, select, update
from sqlalchemy.engine import Engine

from foundry_lite.application.ports import (
    IndexRunCursor,
    IndexRunError,
    IndexRunRecord,
    LinkTypeRow,
    ObjectConflictRecord,
    ObjectIndexLinkRow,
    ObjectLinkInsert,
    ObjectRecordCdcUpdate,
    ObjectRecordInsert,
    ObjectRecordSourceUpdate,
)
from foundry_lite.application.ports.object_index_repository import IndexRunRow
from foundry_lite.infrastructure import schema as db


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
    ) -> None:
        transaction.execute(
            update(db.index_runs)
            .where(and_(db.index_runs.c.tenant_id == tenant_id, db.index_runs.c.id == run_id))
            .values(
                status="succeeded",
                rows_read=rows_read,
                objects_upserted=objects_upserted,
                objects_deleted=objects_deleted,
                links_upserted=links_upserted,
                cursor=cursor,
                completed_at=completed_at,
            )
        )

    def mark_index_run_failed(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        run_id: str,
        error: IndexRunError,
        completed_at: str,
    ) -> None:
        transaction.execute(
            update(db.index_runs)
            .where(and_(db.index_runs.c.tenant_id == tenant_id, db.index_runs.c.id == run_id))
            .values(status="failed", error=error, completed_at=completed_at)
        )

    def insert_object_record(self, *, transaction: Any, record: ObjectRecordInsert) -> None:
        transaction.execute(
            insert(db.object_records).values(
                id=record.record_id,
                tenant_id=record.tenant_id,
                object_type_id=record.object_type_id,
                object_type_api_name=record.object_type_api_name,
                object_id=record.object_id,
                properties=record.properties,
                base_properties=record.base_properties,
                edit_properties=record.edit_properties,
                property_versions=record.property_versions,
                source_dataset_version_id=record.source_dataset_version_id,
                source_hash=record.source_hash,
                object_version=record.object_version,
                deleted=record.deleted,
                deletion_reason=record.deletion_reason,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
        )

    def update_object_record_from_source(
        self,
        *,
        transaction: Any,
        record: ObjectRecordSourceUpdate,
    ) -> None:
        transaction.execute(
            update(db.object_records)
            .where(and_(db.object_records.c.tenant_id == record.tenant_id, db.object_records.c.id == record.record_id))
            .values(
                properties=record.properties,
                base_properties=record.base_properties,
                source_dataset_version_id=record.source_dataset_version_id,
                source_hash=record.source_hash,
                object_version=record.object_version,
                updated_at=record.updated_at,
            )
        )

    def update_object_record_from_cdc(
        self,
        *,
        transaction: Any,
        record: ObjectRecordCdcUpdate,
    ) -> None:
        transaction.execute(
            update(db.object_records)
            .where(and_(db.object_records.c.tenant_id == record.tenant_id, db.object_records.c.id == record.record_id))
            .values(
                properties=record.properties,
                base_properties=record.base_properties,
                property_versions=record.property_versions,
                source_dataset_version_id=record.source_dataset_version_id,
                source_hash=record.source_hash,
                object_version=record.object_version,
                deleted=record.deleted,
                deletion_reason=record.deletion_reason,
                updated_at=record.updated_at,
            )
        )

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
    ) -> ObjectIndexLinkRow | None:
        row = (
            transaction.execute(
                select(db.object_links).where(
                    and_(
                        db.object_links.c.tenant_id == tenant_id,
                        db.object_links.c.link_type_id == link_type_id,
                        db.object_links.c.from_object_id == from_object_id,
                        db.object_links.c.to_object_id == to_object_id,
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
