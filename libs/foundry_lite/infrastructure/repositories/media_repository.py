from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, delete, func, insert, select, update
from sqlalchemy.engine import Engine

from foundry_lite.application.ports.media_repository import (
    MediaItemRecord,
    MediaItemVersionRecord,
    MediaSetRecord,
    MediaTransactionRecord,
)
from foundry_lite.application.state_transitions import (
    MEDIA_TRANSACTION_ABORT,
    MEDIA_TRANSACTION_COMMIT,
    MEDIA_VERSION_COMMITTED,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.status_cas import cas_status_update, cas_status_update_many


class SqlAlchemyMediaRepository:
    """SQLAlchemy implementation of the media catalog, transactions, items, and versions.

    CAS / uniqueness / durable-row operations only; the application services own the
    transaction lifecycle and the domain decisions.
    """

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # -- catalog ---------------------------------------------------------------

    def create_media_set_or_get_existing(self, *, transaction: Any, record: MediaSetRecord) -> MediaSetRecord | None:
        existing = self.media_set_by_ref(
            transaction=transaction, tenant_id=record.tenant_id, namespace=record.namespace, name=record.name
        )
        if existing is not None:
            return existing
        # The (tenant_id, namespace, name) unique constraint backstops a concurrent race.
        transaction.execute(insert(db.media_sets).values(tenant_id=record.tenant_id, **_media_set_values(record)))
        return None

    def get_media_sets(self, *, transaction: Any, ids: list[str]) -> list[MediaSetRecord]:
        if not ids:
            return []
        rows = transaction.execute(select(db.media_sets).where(db.media_sets.c.id.in_(ids))).mappings().all()
        return [_media_set_from_row(row) for row in rows]

    def media_set_by_ref(self, *, transaction: Any, tenant_id: str, namespace: str, name: str) -> MediaSetRecord | None:
        row = (
            transaction.execute(
                select(db.media_sets).where(
                    and_(
                        db.media_sets.c.tenant_id == tenant_id,
                        db.media_sets.c.namespace == namespace,
                        db.media_sets.c.name == name,
                    )
                )
            )
            .mappings()
            .first()
        )
        return _media_set_from_row(row) if row else None

    # -- transactions ----------------------------------------------------------

    def create_open_transaction(
        self, *, transaction: Any, record: MediaTransactionRecord
    ) -> MediaTransactionRecord | None:
        existing = self._transaction_by_idempotency(
            transaction, record.tenant_id, record.media_set_id, record.idempotency_key
        )
        if existing is not None:
            return existing
        transaction.execute(
            insert(db.media_transactions).values(tenant_id=record.tenant_id, **_media_transaction_values(record))
        )
        return None

    def transaction_by_id(
        self, *, transaction: Any, tenant_id: str, media_transaction_id: str
    ) -> MediaTransactionRecord | None:
        row = (
            transaction.execute(
                select(db.media_transactions).where(
                    and_(
                        db.media_transactions.c.tenant_id == tenant_id,
                        db.media_transactions.c.id == media_transaction_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        return _media_transaction_from_row(row) if row else None

    def commit_transaction(
        self, *, transaction: Any, tenant_id: str, media_transaction_id: str, committed_at: str
    ) -> MediaTransactionRecord | None:
        cas_status_update(
            transaction,
            db.media_transactions,
            tenant_id=tenant_id,
            row_id=media_transaction_id,
            transition=MEDIA_TRANSACTION_COMMIT,
            values={"committed_at": committed_at},
        )
        return self.transaction_by_id(
            transaction=transaction, tenant_id=tenant_id, media_transaction_id=media_transaction_id
        )

    def abort_transaction(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        media_transaction_id: str,
        aborted_at: str,
        error: dict[str, object] | None,
    ) -> MediaTransactionRecord | None:
        cas_status_update(
            transaction,
            db.media_transactions,
            tenant_id=tenant_id,
            row_id=media_transaction_id,
            transition=MEDIA_TRANSACTION_ABORT,
            values={"aborted_at": aborted_at, "error": error},
        )
        return self.transaction_by_id(
            transaction=transaction, tenant_id=tenant_id, media_transaction_id=media_transaction_id
        )

    # -- items + head CAS ------------------------------------------------------

    def upsert_media_item(self, *, transaction: Any, record: MediaItemRecord) -> MediaItemRecord:
        existing = self._media_item_by_path(transaction, record.tenant_id, record.media_set_id, record.logical_path)
        if existing is not None:
            return existing
        transaction.execute(insert(db.media_items).values(tenant_id=record.tenant_id, **_media_item_values(record)))
        return record

    def media_item_by_id(self, *, transaction: Any, tenant_id: str, media_item_id: str) -> MediaItemRecord | None:
        return self._media_item_by_id(transaction, tenant_id, media_item_id)

    def cas_item_head_version(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        media_item_id: str,
        expected_head_version_id: str | None,
        new_head_version_id: str,
        updated_at: str,
    ) -> MediaItemRecord | None:
        # head_version_id is a moving pointer, not a status; CAS it directly so a
        # concurrent same-path commit cannot lose a version (one wins the head).
        result = transaction.execute(
            update(db.media_items)
            .where(
                and_(
                    db.media_items.c.tenant_id == tenant_id,
                    db.media_items.c.id == media_item_id,
                    db.media_items.c.head_version_id.is_(None)
                    if expected_head_version_id is None
                    else db.media_items.c.head_version_id == expected_head_version_id,
                )
            )
            .values(head_version_id=new_head_version_id, updated_at=updated_at)
        )
        if result.rowcount != 1:
            return None
        return self._media_item_by_id(transaction, tenant_id, media_item_id)

    # -- immutable versions ----------------------------------------------------

    def insert_version(self, *, transaction: Any, record: MediaItemVersionRecord) -> MediaItemVersionRecord | None:
        existing = self._version_by_blob(
            transaction, record.tenant_id, record.content_hash, record.byte_size, record.blob_key
        )
        if existing is not None:
            return existing
        # uq(media_item_id, version_number) and uq(tenant, content_hash, byte_size, blob_key)
        # backstop concurrent inserts; the caller retries on a conflicting version number.
        transaction.execute(
            insert(db.media_item_versions).values(tenant_id=record.tenant_id, **_media_version_values(record))
        )
        return None

    def media_item_version_by_id(
        self, *, transaction: Any, tenant_id: str, media_item_version_id: str
    ) -> MediaItemVersionRecord | None:
        row = (
            transaction.execute(
                select(db.media_item_versions).where(
                    and_(
                        db.media_item_versions.c.tenant_id == tenant_id,
                        db.media_item_versions.c.id == media_item_version_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        return _media_version_from_row(row) if row else None

    def get_media_item_versions(self, *, transaction: Any, ids: list[str]) -> list[MediaItemVersionRecord]:
        if not ids:
            return []
        rows = (
            transaction.execute(select(db.media_item_versions).where(db.media_item_versions.c.id.in_(ids)))
            .mappings()
            .all()
        )
        return [_media_version_from_row(row) for row in rows]

    def next_version_number(self, *, transaction: Any, media_item_id: str) -> int:
        current = transaction.execute(
            select(func.max(db.media_item_versions.c.version_number)).where(
                db.media_item_versions.c.media_item_id == media_item_id
            )
        ).scalar()
        return int(current) + 1 if current is not None else 1

    def commit_staged_versions(
        self, *, transaction: Any, tenant_id: str, media_transaction_id: str, committed_at: str
    ) -> list[MediaItemVersionRecord]:
        cas_status_update_many(
            transaction,
            db.media_item_versions,
            tenant_id=tenant_id,
            transition=MEDIA_VERSION_COMMITTED,
            values={"committed_at": committed_at},
            conditions=[db.media_item_versions.c.media_transaction_id == media_transaction_id],
        )
        rows = (
            transaction.execute(
                select(db.media_item_versions).where(
                    and_(
                        db.media_item_versions.c.tenant_id == tenant_id,
                        db.media_item_versions.c.media_transaction_id == media_transaction_id,
                    )
                )
            )
            .mappings()
            .all()
        )
        return [_media_version_from_row(row) for row in rows]

    def fetch_unreachable_staged_versions(
        self, *, transaction: Any, tenant_id: str, older_than: str
    ) -> list[MediaItemVersionRecord]:
        # STAGED versions whose transaction is no longer OPEN (aborted/never committed)
        # and which were created before the cutoff: candidates for staged-blob cleanup.
        open_transactions = select(db.media_transactions.c.id).where(db.media_transactions.c.status == "OPEN")
        rows = (
            transaction.execute(
                select(db.media_item_versions).where(
                    and_(
                        db.media_item_versions.c.tenant_id == tenant_id,
                        db.media_item_versions.c.status == "STAGED",
                        db.media_item_versions.c.created_at < older_than,
                        db.media_item_versions.c.media_transaction_id.not_in(open_transactions),
                    )
                )
            )
            .mappings()
            .all()
        )
        return [_media_version_from_row(row) for row in rows]

    # -- L7 retention / legal hold / purge -------------------------------------

    def mark_versions_for_retention(
        self, *, transaction: Any, tenant_id: str, media_item_version_ids: list[str], marked_at: str
    ) -> int:
        if not media_item_version_ids:
            return 0
        result = transaction.execute(
            update(db.media_item_versions)
            .where(
                and_(
                    db.media_item_versions.c.tenant_id == tenant_id,
                    db.media_item_versions.c.id.in_(media_item_version_ids),
                )
            )
            .values(retention_marked_at=marked_at)
        )
        return int(result.rowcount)

    def set_version_legal_hold(
        self, *, transaction: Any, tenant_id: str, media_item_version_id: str, has_legal_hold: bool
    ) -> MediaItemVersionRecord | None:
        transaction.execute(
            update(db.media_item_versions)
            .where(
                and_(
                    db.media_item_versions.c.tenant_id == tenant_id,
                    db.media_item_versions.c.id == media_item_version_id,
                )
            )
            .values(legal_hold=has_legal_hold)
        )
        return self.media_item_version_by_id(
            transaction=transaction, tenant_id=tenant_id, media_item_version_id=media_item_version_id
        )

    def fetch_retention_marked_versions(
        self, *, transaction: Any, tenant_id: str, marked_before: str
    ) -> list[MediaItemVersionRecord]:
        rows = (
            transaction.execute(
                select(db.media_item_versions).where(
                    and_(
                        db.media_item_versions.c.tenant_id == tenant_id,
                        db.media_item_versions.c.retention_marked_at.is_not(None),
                        db.media_item_versions.c.retention_marked_at <= marked_before,
                    )
                )
            )
            .mappings()
            .all()
        )
        return [_media_version_from_row(row) for row in rows]

    def has_reference_binding(self, *, transaction: Any, tenant_id: str, media_item_version_id: str) -> bool:
        row = (
            transaction.execute(
                select(db.media_reference_bindings.c.id).where(
                    and_(
                        db.media_reference_bindings.c.tenant_id == tenant_id,
                        db.media_reference_bindings.c.media_item_version_id == media_item_version_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        return row is not None

    def purge_version(self, *, transaction: Any, tenant_id: str, media_item_version_id: str) -> None:
        # Physical delete of one version's whole derived-data footprint, in the caller's
        # transaction. Bindings are NOT deleted here — the service refuses to purge a
        # reachable version, so a bound version never reaches this method.
        for table in (db.content_units, db.media_derivatives, db.media_access_caches):
            transaction.execute(
                delete(table).where(
                    and_(
                        table.c.tenant_id == tenant_id,
                        table.c.source_media_item_version_id == media_item_version_id,
                    )
                )
            )
        transaction.execute(
            delete(db.media_item_versions).where(
                and_(
                    db.media_item_versions.c.tenant_id == tenant_id,
                    db.media_item_versions.c.id == media_item_version_id,
                )
            )
        )

    # -- private lookups -------------------------------------------------------

    def _transaction_by_idempotency(
        self, transaction: Any, tenant_id: str, media_set_id: str, idempotency_key: str
    ) -> MediaTransactionRecord | None:
        row = (
            transaction.execute(
                select(db.media_transactions).where(
                    and_(
                        db.media_transactions.c.tenant_id == tenant_id,
                        db.media_transactions.c.media_set_id == media_set_id,
                        db.media_transactions.c.idempotency_key == idempotency_key,
                    )
                )
            )
            .mappings()
            .first()
        )
        return _media_transaction_from_row(row) if row else None

    def _media_item_by_path(
        self, transaction: Any, tenant_id: str, media_set_id: str, logical_path: str
    ) -> MediaItemRecord | None:
        row = (
            transaction.execute(
                select(db.media_items).where(
                    and_(
                        db.media_items.c.tenant_id == tenant_id,
                        db.media_items.c.media_set_id == media_set_id,
                        db.media_items.c.logical_path == logical_path,
                    )
                )
            )
            .mappings()
            .first()
        )
        return _media_item_from_row(row) if row else None

    def _media_item_by_id(self, transaction: Any, tenant_id: str, media_item_id: str) -> MediaItemRecord | None:
        row = (
            transaction.execute(
                select(db.media_items).where(
                    and_(db.media_items.c.tenant_id == tenant_id, db.media_items.c.id == media_item_id)
                )
            )
            .mappings()
            .first()
        )
        return _media_item_from_row(row) if row else None

    def _version_by_blob(
        self, transaction: Any, tenant_id: str, content_hash: str, byte_size: int, blob_key: str
    ) -> MediaItemVersionRecord | None:
        row = (
            transaction.execute(
                select(db.media_item_versions).where(
                    and_(
                        db.media_item_versions.c.tenant_id == tenant_id,
                        db.media_item_versions.c.content_hash == content_hash,
                        db.media_item_versions.c.byte_size == byte_size,
                        db.media_item_versions.c.blob_key == blob_key,
                    )
                )
            )
            .mappings()
            .first()
        )
        return _media_version_from_row(row) if row else None


def _media_set_values(record: MediaSetRecord) -> dict[str, object]:
    return {
        "id": record.media_set_id,
        "namespace": record.namespace,
        "name": record.name,
        "schema_type": record.schema_type,
        "primary_format": record.primary_format,
        "allowed_input_formats": list(record.allowed_input_formats),
        "transaction_policy": record.transaction_policy,
        "storage_profile": record.storage_profile,
        "processing_profile": record.processing_profile,
        "classification": record.classification,
        "retention_policy_id": record.retention_policy_id,
        "is_virtual": record.is_virtual,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _media_set_from_row(row: Any) -> MediaSetRecord:
    return MediaSetRecord(
        media_set_id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        namespace=str(row["namespace"]),
        name=str(row["name"]),
        schema_type=str(row["schema_type"]),
        primary_format=str(row["primary_format"]),
        allowed_input_formats=tuple(str(value) for value in row["allowed_input_formats"]),
        transaction_policy=str(row["transaction_policy"]),
        storage_profile=str(row["storage_profile"]),
        processing_profile=str(row["processing_profile"]),
        classification=str(row["classification"]),
        retention_policy_id=str(row["retention_policy_id"]) if row["retention_policy_id"] is not None else None,
        is_virtual=bool(row["is_virtual"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _media_transaction_values(record: MediaTransactionRecord) -> dict[str, object | None]:
    return {
        "id": record.media_transaction_id,
        "media_set_id": record.media_set_id,
        "status": record.status,
        "mode": record.mode,
        "idempotency_key": record.idempotency_key,
        "request_fingerprint": record.request_fingerprint,
        "opened_at": record.opened_at,
        "committed_at": record.committed_at,
        "aborted_at": record.aborted_at,
        "error": record.error,
        "trace": record.trace,
    }


def _media_transaction_from_row(row: Any) -> MediaTransactionRecord:
    return MediaTransactionRecord(
        media_transaction_id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        media_set_id=str(row["media_set_id"]),
        status=str(row["status"]),
        mode=str(row["mode"]),
        idempotency_key=str(row["idempotency_key"]),
        request_fingerprint=str(row["request_fingerprint"]),
        opened_at=str(row["opened_at"]),
        committed_at=str(row["committed_at"]) if row["committed_at"] is not None else None,
        aborted_at=str(row["aborted_at"]) if row["aborted_at"] is not None else None,
        error=cast("dict[str, object]", dict(row["error"])) if row["error"] is not None else None,
        trace=cast("dict[str, object]", dict(row["trace"])) if row["trace"] is not None else None,
    )


def _media_item_values(record: MediaItemRecord) -> dict[str, object | None]:
    return {
        "id": record.media_item_id,
        "media_set_id": record.media_set_id,
        "logical_path": record.logical_path,
        "head_version_id": record.head_version_id,
        "is_deleted": record.is_deleted,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _media_item_from_row(row: Any) -> MediaItemRecord:
    return MediaItemRecord(
        media_item_id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        media_set_id=str(row["media_set_id"]),
        logical_path=str(row["logical_path"]),
        head_version_id=str(row["head_version_id"]) if row["head_version_id"] is not None else None,
        is_deleted=bool(row["is_deleted"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _media_version_values(record: MediaItemVersionRecord) -> dict[str, object | None]:
    return {
        "id": record.media_item_version_id,
        "media_item_id": record.media_item_id,
        "media_transaction_id": record.media_transaction_id,
        "version_number": record.version_number,
        "blob_key": record.blob_key,
        "content_hash": record.content_hash,
        "byte_size": record.byte_size,
        "supplied_mime_type": record.supplied_mime_type,
        "sniffed_mime_type": record.sniffed_mime_type,
        "schema_type": record.schema_type,
        "format": record.format,
        "probe_metadata": dict(record.probe_metadata),
        "security_envelope": dict(record.security_envelope),
        "source_ref": dict(record.source_ref) if record.source_ref is not None else None,
        "status": record.status,
        "retention_marked_at": record.retention_marked_at,
        "legal_hold": record.has_legal_hold,
        "created_at": record.created_at,
        "committed_at": record.committed_at,
    }


def _media_version_from_row(row: Any) -> MediaItemVersionRecord:
    return MediaItemVersionRecord(
        media_item_version_id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        media_item_id=str(row["media_item_id"]),
        media_transaction_id=str(row["media_transaction_id"]),
        version_number=int(row["version_number"]),
        blob_key=str(row["blob_key"]),
        content_hash=str(row["content_hash"]),
        byte_size=int(row["byte_size"]),
        supplied_mime_type=str(row["supplied_mime_type"]),
        sniffed_mime_type=str(row["sniffed_mime_type"]),
        schema_type=str(row["schema_type"]),
        format=str(row["format"]),
        probe_metadata=cast("dict[str, object]", dict(row["probe_metadata"])),
        security_envelope=cast("dict[str, object]", dict(row["security_envelope"])),
        source_ref=cast("dict[str, object]", dict(row["source_ref"])) if row["source_ref"] is not None else None,
        status=str(row["status"]),
        retention_marked_at=str(row["retention_marked_at"]) if row["retention_marked_at"] is not None else None,
        has_legal_hold=bool(row["legal_hold"]),
        created_at=str(row["created_at"]),
        committed_at=str(row["committed_at"]) if row["committed_at"] is not None else None,
    )
