from __future__ import annotations

from typing import Any

from sqlalchemy import and_, delete, insert, select
from sqlalchemy.engine import Engine

from foundry_lite.application.ports.media_access_cache_repository import MediaAccessCacheRecord
from foundry_lite.infrastructure import schema as db


class SqlAlchemyMediaAccessCacheRepository:
    """SQLAlchemy implementation of the access-pattern cache boundary.

    Durable-row operations only; the access-pattern service owns the recompute decision. The
    cache is never serving truth — these rows are evictable and rebuildable from the source.
    """

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def access_cache_by_key(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        source_media_item_version_id: str,
        access_pattern: str,
        access_pattern_spec_hash: str,
    ) -> MediaAccessCacheRecord | None:
        row = (
            transaction.execute(
                select(db.media_access_caches).where(
                    and_(
                        db.media_access_caches.c.tenant_id == tenant_id,
                        db.media_access_caches.c.source_media_item_version_id == source_media_item_version_id,
                        db.media_access_caches.c.access_pattern == access_pattern,
                        db.media_access_caches.c.access_pattern_spec_hash == access_pattern_spec_hash,
                    )
                )
            )
            .mappings()
            .first()
        )
        return _cache_from_row(row) if row else None

    def upsert_access_cache(self, *, transaction: Any, record: MediaAccessCacheRecord) -> MediaAccessCacheRecord:
        # Replace any prior row for the unique key so a recompute always reflects the latest source.
        transaction.execute(
            delete(db.media_access_caches).where(
                and_(
                    db.media_access_caches.c.tenant_id == record.tenant_id,
                    db.media_access_caches.c.source_media_item_version_id == record.source_media_item_version_id,
                    db.media_access_caches.c.access_pattern == record.access_pattern,
                    db.media_access_caches.c.access_pattern_spec_hash == record.access_pattern_spec_hash,
                )
            )
        )
        transaction.execute(insert(db.media_access_caches).values(tenant_id=record.tenant_id, **_cache_values(record)))
        return record

    def delete_access_caches_for_version(
        self, *, transaction: Any, tenant_id: str, source_media_item_version_id: str
    ) -> list[str]:
        rows = (
            transaction.execute(
                select(db.media_access_caches.c.id).where(
                    and_(
                        db.media_access_caches.c.tenant_id == tenant_id,
                        db.media_access_caches.c.source_media_item_version_id == source_media_item_version_id,
                    )
                )
            )
            .mappings()
            .all()
        )
        ids = [str(row["id"]) for row in rows]
        if ids:
            transaction.execute(
                delete(db.media_access_caches).where(
                    and_(
                        db.media_access_caches.c.tenant_id == tenant_id,
                        db.media_access_caches.c.id.in_(ids),
                    )
                )
            )
        return ids


def _cache_values(record: MediaAccessCacheRecord) -> dict[str, object | None]:
    return {
        "id": record.access_cache_id,
        "source_media_item_version_id": record.source_media_item_version_id,
        "access_pattern": record.access_pattern,
        "access_pattern_spec_hash": record.access_pattern_spec_hash,
        "persistence_policy": record.persistence_policy,
        "source_content_hash": record.source_content_hash,
        "blob_key": record.blob_key,
        "content_hash": record.content_hash,
        "byte_size": record.byte_size,
        "mime_type": record.mime_type,
        "created_at": record.created_at,
        "expires_at": record.expires_at,
    }


def _cache_from_row(row: Any) -> MediaAccessCacheRecord:
    return MediaAccessCacheRecord(
        access_cache_id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        source_media_item_version_id=str(row["source_media_item_version_id"]),
        access_pattern=str(row["access_pattern"]),
        access_pattern_spec_hash=str(row["access_pattern_spec_hash"]),
        persistence_policy=str(row["persistence_policy"]),
        source_content_hash=str(row["source_content_hash"]),
        blob_key=str(row["blob_key"]),
        content_hash=str(row["content_hash"]),
        byte_size=int(row["byte_size"]),
        mime_type=str(row["mime_type"]),
        created_at=str(row["created_at"]),
        expires_at=str(row["expires_at"]) if row["expires_at"] is not None else None,
    )
