from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, insert, select, update
from sqlalchemy.engine import Engine

from foundry_lite.application.ports.media_reference_binding_repository import MediaReferenceBindingRecord
from foundry_lite.infrastructure import schema as db


class SqlAlchemyMediaReferenceBindingRepository:
    """SQLAlchemy implementation of the media-reference binding boundary (durable rows only)."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def binding_by_holder(
        self, *, transaction: Any, tenant_id: str, holder_type: str, holder_id: str, property_name: str
    ) -> MediaReferenceBindingRecord | None:
        row = (
            transaction.execute(
                select(db.media_reference_bindings).where(
                    and_(
                        db.media_reference_bindings.c.tenant_id == tenant_id,
                        db.media_reference_bindings.c.holder_type == holder_type,
                        db.media_reference_bindings.c.holder_id == holder_id,
                        db.media_reference_bindings.c.property_name == property_name,
                    )
                )
            )
            .mappings()
            .first()
        )
        return _binding_from_row(row) if row else None

    def bindings_for_media_versions(
        self, *, transaction: Any, tenant_id: str, media_item_version_ids: list[str]
    ) -> list[MediaReferenceBindingRecord]:
        if not media_item_version_ids:
            return []
        rows = (
            transaction.execute(
                select(db.media_reference_bindings).where(
                    and_(
                        db.media_reference_bindings.c.tenant_id == tenant_id,
                        db.media_reference_bindings.c.media_item_version_id.in_(media_item_version_ids),
                    )
                )
            )
            .mappings()
            .all()
        )
        return [_binding_from_row(row) for row in rows]

    def create_binding(self, *, transaction: Any, record: MediaReferenceBindingRecord) -> None:
        transaction.execute(
            insert(db.media_reference_bindings).values(tenant_id=record.tenant_id, **_binding_values(record))
        )

    def update_binding(self, *, transaction: Any, record: MediaReferenceBindingRecord) -> None:
        transaction.execute(
            update(db.media_reference_bindings)
            .where(
                and_(
                    db.media_reference_bindings.c.tenant_id == record.tenant_id,
                    db.media_reference_bindings.c.holder_type == record.holder_type,
                    db.media_reference_bindings.c.holder_id == record.holder_id,
                    db.media_reference_bindings.c.property_name == record.property_name,
                )
            )
            .values(
                media_set_id=record.media_set_id,
                media_item_id=record.media_item_id,
                media_item_version_id=record.media_item_version_id,
                logical_path=record.logical_path,
                content_hash=record.content_hash,
                security_envelope=dict(record.security_envelope),
                idempotency_key=record.idempotency_key,
                updated_at=record.updated_at,
            )
        )


def _binding_values(record: MediaReferenceBindingRecord) -> dict[str, object]:
    return {
        "id": record.media_reference_binding_id,
        "holder_type": record.holder_type,
        "holder_id": record.holder_id,
        "property_name": record.property_name,
        "media_set_id": record.media_set_id,
        "media_item_id": record.media_item_id,
        "media_item_version_id": record.media_item_version_id,
        "logical_path": record.logical_path,
        "content_hash": record.content_hash,
        "security_envelope": dict(record.security_envelope),
        "idempotency_key": record.idempotency_key,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _binding_from_row(row: Any) -> MediaReferenceBindingRecord:
    return MediaReferenceBindingRecord(
        media_reference_binding_id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        holder_type=str(row["holder_type"]),
        holder_id=str(row["holder_id"]),
        property_name=str(row["property_name"]),
        media_set_id=str(row["media_set_id"]),
        media_item_id=str(row["media_item_id"]),
        media_item_version_id=str(row["media_item_version_id"]),
        logical_path=str(row["logical_path"]),
        content_hash=str(row["content_hash"]),
        security_envelope=cast("dict[str, object]", dict(row["security_envelope"])),
        idempotency_key=str(row["idempotency_key"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )
