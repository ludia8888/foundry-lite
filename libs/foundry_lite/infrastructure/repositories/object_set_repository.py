"""SQLAlchemy repository adapter for object set repository persistence."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, delete, insert, select
from sqlalchemy.engine import Engine

from foundry_lite.application.ports import ObjectRecordRow, ObjectSetObjectTypeRow, ObjectSetRecord, ObjectSetRow
from foundry_lite.infrastructure import schema as db


class SqlAlchemyObjectSetRepository:
    """SQLAlchemy implementation of object set rows and supporting membership reads."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_object_set(self, *, transaction: Any, record: ObjectSetRecord) -> None:
        transaction.execute(
            insert(db.object_sets).values(
                id=record.set_id,
                tenant_id=record.tenant_id,
                name=record.name,
                object_type_id=record.object_type_id,
                set_type=record.set_type,
                definition=record.definition,
                visibility=record.visibility,
                owner_user_id=record.owner_user_id,
                expires_at=record.expires_at,
                created_at=record.created_at,
            )
        )

    def object_set_by_id(self, *, transaction: Any, tenant_id: str, set_id: str) -> ObjectSetRow | None:
        row = (
            transaction.execute(
                select(db.object_sets).where(
                    and_(db.object_sets.c.tenant_id == tenant_id, db.object_sets.c.id == set_id)
                )
            )
            .mappings()
            .first()
        )
        return cast(ObjectSetRow, dict(row)) if row else None

    def object_sets(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_id: str | None = None,
    ) -> list[ObjectSetRow]:
        query = select(db.object_sets).where(db.object_sets.c.tenant_id == tenant_id)
        if object_type_id is not None:
            query = query.where(db.object_sets.c.object_type_id == object_type_id)
        rows = transaction.execute(query.order_by(db.object_sets.c.created_at)).mappings().all()
        return [cast(ObjectSetRow, dict(row)) for row in rows]

    def delete_object_sets(self, *, transaction: Any, tenant_id: str, set_ids: list[str]) -> None:
        if not set_ids:
            return
        transaction.execute(
            delete(db.object_sets).where(
                and_(db.object_sets.c.tenant_id == tenant_id, db.object_sets.c.id.in_(set_ids))
            )
        )

    def active_object_ids(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
        object_ids: list[str],
    ) -> set[str]:
        if not object_ids:
            return set()
        rows = (
            transaction.execute(
                select(db.object_records.c.object_id).where(
                    and_(
                        db.object_records.c.tenant_id == tenant_id,
                        db.object_records.c.object_type_api_name == object_type_api_name,
                        db.object_records.c.object_id.in_(object_ids),
                        db.object_records.c.is_active == True,  # noqa: E712
                        db.object_records.c.deleted == False,  # noqa: E712
                    )
                )
            )
            .mappings()
            .all()
        )
        return {row["object_id"] for row in rows}

    def active_object_records_by_ids(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
        object_ids: list[str],
    ) -> list[ObjectRecordRow]:
        if not object_ids:
            return []
        rows = (
            transaction.execute(
                select(db.object_records).where(
                    and_(
                        db.object_records.c.tenant_id == tenant_id,
                        db.object_records.c.object_type_api_name == object_type_api_name,
                        db.object_records.c.object_id.in_(object_ids),
                        db.object_records.c.is_active == True,  # noqa: E712
                        db.object_records.c.deleted == False,  # noqa: E712
                    )
                )
            )
            .mappings()
            .all()
        )
        return [cast(ObjectRecordRow, dict(row)) for row in rows]

    def property_names_for_object_type(self, *, transaction: Any, object_type_id: str) -> set[str]:
        rows = (
            transaction.execute(
                select(db.property_types.c.api_name).where(db.property_types.c.object_type_id == object_type_id)
            )
            .mappings()
            .all()
        )
        return {str(row["api_name"]) for row in rows}

    def object_type_by_id(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_id: str,
    ) -> ObjectSetObjectTypeRow | None:
        row = (
            transaction.execute(
                select(db.object_types).where(
                    and_(
                        db.object_types.c.tenant_id == tenant_id,
                        db.object_types.c.id == object_type_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(ObjectSetObjectTypeRow, dict(row)) if row else None
