from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, insert, select
from sqlalchemy.engine import Engine

from foundry_lite.application.ports.media_derivative_repository import ContentUnitRecord, MediaDerivativeRecord
from foundry_lite.application.state_transitions import MEDIA_DERIVATIVE_COMMITTED, MEDIA_DERIVATIVE_FAILED
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.status_cas import cas_status_update


class SqlAlchemyMediaDerivativeRepository:
    """SQLAlchemy implementation of the media derivative + content-unit boundary.

    CAS / uniqueness / durable-row operations only; the processing service owns the
    transaction lifecycle and the domain decisions.
    """

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_derivative_or_get_existing(
        self, *, transaction: Any, record: MediaDerivativeRecord
    ) -> MediaDerivativeRecord | None:
        existing = self._derivative_by_spec(transaction, record)
        if existing is not None:
            return existing
        # uq(source_version, kind, spec_hash, model_version, params_hash) backstops a race.
        transaction.execute(
            insert(db.media_derivatives).values(tenant_id=record.tenant_id, **_derivative_values(record))
        )
        return None

    def derivative_by_id(
        self, *, transaction: Any, tenant_id: str, media_derivative_id: str
    ) -> MediaDerivativeRecord | None:
        row = (
            transaction.execute(
                select(db.media_derivatives).where(
                    and_(
                        db.media_derivatives.c.tenant_id == tenant_id, db.media_derivatives.c.id == media_derivative_id
                    )
                )
            )
            .mappings()
            .first()
        )
        return _derivative_from_row(row) if row else None

    def get_derivatives(self, *, transaction: Any, ids: list[str]) -> list[MediaDerivativeRecord]:
        if not ids:
            return []
        rows = (
            transaction.execute(select(db.media_derivatives).where(db.media_derivatives.c.id.in_(ids))).mappings().all()
        )
        return [_derivative_from_row(row) for row in rows]

    def commit_derivative(
        self, *, transaction: Any, tenant_id: str, media_derivative_id: str, committed_at: str
    ) -> MediaDerivativeRecord | None:
        cas_status_update(
            transaction,
            db.media_derivatives,
            tenant_id=tenant_id,
            row_id=media_derivative_id,
            transition=MEDIA_DERIVATIVE_COMMITTED,
            values={"committed_at": committed_at},
        )
        return self.derivative_by_id(
            transaction=transaction, tenant_id=tenant_id, media_derivative_id=media_derivative_id
        )

    def fail_derivative(
        self, *, transaction: Any, tenant_id: str, media_derivative_id: str, error: dict[str, object], failed_at: str
    ) -> MediaDerivativeRecord | None:
        cas_status_update(
            transaction,
            db.media_derivatives,
            tenant_id=tenant_id,
            row_id=media_derivative_id,
            transition=MEDIA_DERIVATIVE_FAILED,
            values={"error": error, "committed_at": failed_at},
        )
        return self.derivative_by_id(
            transaction=transaction, tenant_id=tenant_id, media_derivative_id=media_derivative_id
        )

    def insert_content_units(self, *, transaction: Any, records: list[ContentUnitRecord]) -> int:
        inserted = 0
        for record in records:
            if self._content_unit_exists(transaction, record):
                continue
            transaction.execute(
                insert(db.content_units).values(tenant_id=record.tenant_id, **_content_unit_values(record))
            )
            inserted += 1
        return inserted

    def get_content_units(self, *, transaction: Any, tenant_id: str, derivative_id: str) -> list[ContentUnitRecord]:
        rows = (
            transaction.execute(
                select(db.content_units)
                .where(
                    and_(db.content_units.c.tenant_id == tenant_id, db.content_units.c.derivative_id == derivative_id)
                )
                .order_by(db.content_units.c.ordinal)
            )
            .mappings()
            .all()
        )
        return [_content_unit_from_row(row) for row in rows]

    def get_content_units_by_ids(self, *, transaction: Any, ids: list[str]) -> list[ContentUnitRecord]:
        if not ids:
            return []
        rows = transaction.execute(select(db.content_units).where(db.content_units.c.id.in_(ids))).mappings().all()
        return [_content_unit_from_row(row) for row in rows]

    def get_committed_content_units_for_versions(
        self, *, transaction: Any, tenant_id: str, source_media_item_version_ids: list[str]
    ) -> list[ContentUnitRecord]:
        if not source_media_item_version_ids:
            return []
        rows = (
            transaction.execute(
                select(db.content_units)
                .where(
                    and_(
                        db.content_units.c.tenant_id == tenant_id,
                        db.content_units.c.source_media_item_version_id.in_(source_media_item_version_ids),
                    )
                )
                .order_by(db.content_units.c.source_media_item_version_id, db.content_units.c.ordinal)
            )
            .mappings()
            .all()
        )
        return [_content_unit_from_row(row) for row in rows]

    def fetch_unreachable_staged_derivatives(
        self, *, transaction: Any, tenant_id: str, older_than: str
    ) -> list[MediaDerivativeRecord]:
        rows = (
            transaction.execute(
                select(db.media_derivatives).where(
                    and_(
                        db.media_derivatives.c.tenant_id == tenant_id,
                        db.media_derivatives.c.status == "STAGED",
                        db.media_derivatives.c.created_at < older_than,
                    )
                )
            )
            .mappings()
            .all()
        )
        return [_derivative_from_row(row) for row in rows]

    def _derivative_by_spec(self, transaction: Any, record: MediaDerivativeRecord) -> MediaDerivativeRecord | None:
        row = (
            transaction.execute(
                select(db.media_derivatives).where(
                    and_(
                        db.media_derivatives.c.source_media_item_version_id == record.source_media_item_version_id,
                        db.media_derivatives.c.derivative_kind == record.derivative_kind,
                        db.media_derivatives.c.processor_spec_hash == record.processor_spec_hash,
                        db.media_derivatives.c.model_version == record.model_version,
                        db.media_derivatives.c.params_hash == record.params_hash,
                    )
                )
            )
            .mappings()
            .first()
        )
        return _derivative_from_row(row) if row else None

    def _content_unit_exists(self, transaction: Any, record: ContentUnitRecord) -> bool:
        row = (
            transaction.execute(
                select(db.content_units.c.id).where(
                    and_(
                        db.content_units.c.source_media_item_version_id == record.source_media_item_version_id,
                        db.content_units.c.unit_kind == record.unit_kind,
                        db.content_units.c.ordinal == record.ordinal,
                        db.content_units.c.chunk_spec_hash == record.chunk_spec_hash,
                    )
                )
            )
            .mappings()
            .first()
        )
        return row is not None


def _derivative_values(record: MediaDerivativeRecord) -> dict[str, object | None]:
    return {
        "id": record.media_derivative_id,
        "source_media_item_version_id": record.source_media_item_version_id,
        "derivative_kind": record.derivative_kind,
        "processor_spec_hash": record.processor_spec_hash,
        "processor_name": record.processor_name,
        "processor_version": record.processor_version,
        "model_name": record.model_name,
        "model_version": record.model_version,
        "params_hash": record.params_hash,
        "blob_key": record.blob_key,
        "content_hash": record.content_hash,
        "byte_size": record.byte_size,
        "mime_type": record.mime_type,
        "security_envelope": dict(record.security_envelope),
        "status": record.status,
        "error": record.error,
        "created_at": record.created_at,
        "committed_at": record.committed_at,
    }


def _derivative_from_row(row: Any) -> MediaDerivativeRecord:
    return MediaDerivativeRecord(
        media_derivative_id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        source_media_item_version_id=str(row["source_media_item_version_id"]),
        derivative_kind=str(row["derivative_kind"]),
        processor_spec_hash=str(row["processor_spec_hash"]),
        processor_name=str(row["processor_name"]),
        processor_version=str(row["processor_version"]),
        model_name=str(row["model_name"]) if row["model_name"] is not None else None,
        model_version=str(row["model_version"]),
        params_hash=str(row["params_hash"]),
        blob_key=str(row["blob_key"]) if row["blob_key"] is not None else None,
        content_hash=str(row["content_hash"]) if row["content_hash"] is not None else None,
        byte_size=int(row["byte_size"]) if row["byte_size"] is not None else None,
        mime_type=str(row["mime_type"]) if row["mime_type"] is not None else None,
        security_envelope=cast("dict[str, object]", dict(row["security_envelope"])),
        status=str(row["status"]),
        error=cast("dict[str, object]", dict(row["error"])) if row["error"] is not None else None,
        created_at=str(row["created_at"]),
        committed_at=str(row["committed_at"]) if row["committed_at"] is not None else None,
    )


def _content_unit_values(record: ContentUnitRecord) -> dict[str, object | None]:
    return {
        "id": record.content_unit_id,
        "source_media_item_version_id": record.source_media_item_version_id,
        "derivative_id": record.derivative_id,
        "unit_kind": record.unit_kind,
        "ordinal": record.ordinal,
        "page_number": record.page_number,
        "start_ms": record.start_ms,
        "end_ms": record.end_ms,
        "bbox": record.bbox,
        "speaker": record.speaker,
        "language": record.language,
        "text": record.text,
        "text_hash": record.text_hash,
        "chunk_spec_hash": record.chunk_spec_hash,
        "security_envelope": dict(record.security_envelope),
        "created_at": record.created_at,
    }


def _content_unit_from_row(row: Any) -> ContentUnitRecord:
    return ContentUnitRecord(
        content_unit_id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        source_media_item_version_id=str(row["source_media_item_version_id"]),
        derivative_id=str(row["derivative_id"]),
        unit_kind=str(row["unit_kind"]),
        ordinal=int(row["ordinal"]),
        page_number=int(row["page_number"]) if row["page_number"] is not None else None,
        start_ms=int(row["start_ms"]) if row["start_ms"] is not None else None,
        end_ms=int(row["end_ms"]) if row["end_ms"] is not None else None,
        bbox=cast("dict[str, object]", dict(row["bbox"])) if row["bbox"] is not None else None,
        speaker=str(row["speaker"]) if row["speaker"] is not None else None,
        language=str(row["language"]) if row["language"] is not None else None,
        text=str(row["text"]),
        text_hash=str(row["text_hash"]),
        chunk_spec_hash=str(row["chunk_spec_hash"]),
        security_envelope=cast("dict[str, object]", dict(row["security_envelope"])),
        created_at=str(row["created_at"]),
    )
