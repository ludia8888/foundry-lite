from __future__ import annotations

from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.engine import Engine

from foundry_lite.infrastructure import schema as db


class SqlAlchemyObjectReadRepository:
    """SQLAlchemy implementation of object record and link reads."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def object_record(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
        object_id: str,
    ) -> dict[str, Any] | None:
        row = (
            transaction.execute(
                select(db.object_records).where(
                    and_(
                        db.object_records.c.tenant_id == tenant_id,
                        db.object_records.c.object_type_api_name == object_type_api_name,
                        db.object_records.c.object_id == object_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    def active_object_rows(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
    ) -> list[dict[str, Any]]:
        rows = (
            transaction.execute(
                select(db.object_records)
                .where(
                    and_(
                        db.object_records.c.tenant_id == tenant_id,
                        db.object_records.c.object_type_api_name == object_type_api_name,
                        db.object_records.c.deleted == False,  # noqa: E712
                    )
                )
                .order_by(db.object_records.c.object_id)
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    def active_links_from(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        link_type_api_name: str,
        from_api_name: str,
        from_object_id: str,
    ) -> list[dict[str, Any]]:
        rows = (
            transaction.execute(
                select(db.object_links).where(
                    and_(
                        db.object_links.c.tenant_id == tenant_id,
                        db.object_links.c.link_type_api_name == link_type_api_name,
                        db.object_links.c.from_api_name == from_api_name,
                        db.object_links.c.from_object_id == from_object_id,
                        db.object_links.c.deleted == False,  # noqa: E712
                    )
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]
