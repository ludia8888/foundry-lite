"""SQLAlchemy repository adapter for the object-index row-hash changelog."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, delete, insert, select
from sqlalchemy.engine import Engine

from foundry_lite.infrastructure import schema as db

# SQLite caps bound parameters per statement, so key deletes are chunked well
# below the historical 999-variable limit instead of assuming a modern build.
_DELETE_CHUNK_SIZE = 500


class SqlAlchemyObjectIndexRowHashRepository:
    """SQLAlchemy implementation of per-primary-key row-hash persistence."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def load_row_hashes(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_id: str,
    ) -> dict[str, str]:
        rows = transaction.execute(
            select(db.object_index_row_hashes.c.object_id, db.object_index_row_hashes.c.row_hash).where(
                and_(
                    db.object_index_row_hashes.c.tenant_id == tenant_id,
                    db.object_index_row_hashes.c.object_type_id == object_type_id,
                )
            )
        ).all()
        return {str(object_id): str(row_hash) for object_id, row_hash in rows}

    def replace_row_hashes(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_id: str,
        dataset_version_id: str,
        row_hashes: Mapping[str, str],
        updated_at: str,
    ) -> None:
        transaction.execute(
            delete(db.object_index_row_hashes).where(
                and_(
                    db.object_index_row_hashes.c.tenant_id == tenant_id,
                    db.object_index_row_hashes.c.object_type_id == object_type_id,
                )
            )
        )
        self._insert_row_hashes(transaction, tenant_id, object_type_id, dataset_version_id, row_hashes, updated_at)

    def apply_row_hash_changes(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_id: str,
        dataset_version_id: str,
        upserted: Mapping[str, str],
        removed_object_ids: Collection[str],
        updated_at: str,
    ) -> None:
        # Delete-then-insert keeps the upsert dialect-portable (SQLite and
        # PostgreSQL) while staying atomic inside the caller's transaction.
        stale_object_ids = sorted({*upserted, *removed_object_ids})
        for start in range(0, len(stale_object_ids), _DELETE_CHUNK_SIZE):
            chunk = stale_object_ids[start : start + _DELETE_CHUNK_SIZE]
            transaction.execute(
                delete(db.object_index_row_hashes).where(
                    and_(
                        db.object_index_row_hashes.c.tenant_id == tenant_id,
                        db.object_index_row_hashes.c.object_type_id == object_type_id,
                        db.object_index_row_hashes.c.object_id.in_(chunk),
                    )
                )
            )
        self._insert_row_hashes(transaction, tenant_id, object_type_id, dataset_version_id, upserted, updated_at)

    def _insert_row_hashes(
        self,
        transaction: Any,
        tenant_id: str,
        object_type_id: str,
        dataset_version_id: str,
        row_hashes: Mapping[str, str],
        updated_at: str,
    ) -> None:
        if not row_hashes:
            return
        transaction.execute(
            insert(db.object_index_row_hashes).values(
                [
                    {
                        "id": f"object_index_row_hash_{uuid4().hex}",
                        "tenant_id": tenant_id,
                        "object_type_id": object_type_id,
                        "object_id": object_id,
                        "row_hash": row_hash,
                        "dataset_version_id": dataset_version_id,
                        "updated_at": updated_at,
                    }
                    for object_id, row_hash in sorted(row_hashes.items())
                ]
            )
        )
