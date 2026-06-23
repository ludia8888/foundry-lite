from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, insert, select
from sqlalchemy.engine import Engine

from foundry_lite.application.ports.erasure_repository import (
    ErasureCertificateRecord,
    ErasureRedactionExecutionRecord,
    ErasureRequestRecord,
)
from foundry_lite.application.ports.transaction_context import ERASURE_REQUEST_EXECUTED
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.status_cas import cas_status_update


class SqlAlchemyErasureRepository:
    """SQLAlchemy implementation of durable erasure request and certificate storage."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def insert_request_or_get_existing(
        self, *, transaction: Any, record: ErasureRequestRecord
    ) -> ErasureRequestRecord | None:
        existing = self._request_by_idempotency(transaction, record.tenant_id, record.idempotency_key)
        if existing is not None:
            return existing
        # The (tenant_id, idempotency_key) unique constraint is the backstop for a
        # concurrent race; operator-initiated erasure requests fail closed and retry.
        transaction.execute(insert(db.erasure_requests).values(tenant_id=record.tenant_id, **_request_values(record)))
        return None

    def request_by_id(self, *, transaction: Any, tenant_id: str, request_id: str) -> ErasureRequestRecord | None:
        row = (
            transaction.execute(
                select(db.erasure_requests).where(
                    and_(db.erasure_requests.c.tenant_id == tenant_id, db.erasure_requests.c.id == request_id)
                )
            )
            .mappings()
            .first()
        )
        return _request_from_row(row) if row else None

    def mark_request_executed(
        self, *, transaction: Any, tenant_id: str, request_id: str, manifest_id: str, executed_at: str
    ) -> None:
        # CAS requested -> executed so a request is certified at most once; a replay
        # finds the row already executed and the transition is a safe no-op.
        cas_status_update(
            transaction,
            db.erasure_requests,
            tenant_id=tenant_id,
            row_id=request_id,
            transition=ERASURE_REQUEST_EXECUTED,
            values={"manifest_id": manifest_id, "executed_at": executed_at},
        )

    def insert_certificate_or_get_existing(
        self, *, transaction: Any, record: ErasureCertificateRecord
    ) -> ErasureCertificateRecord | None:
        existing = self.certificate_by_request(
            transaction=transaction, tenant_id=record.tenant_id, request_id=record.request_id
        )
        if existing is not None:
            return existing
        # The (tenant_id, request_id) unique constraint backstops a concurrent race.
        transaction.execute(
            insert(db.erasure_certificates).values(tenant_id=record.tenant_id, **_certificate_values(record))
        )
        return None

    def certificate_by_request(
        self, *, transaction: Any, tenant_id: str, request_id: str
    ) -> ErasureCertificateRecord | None:
        row = (
            transaction.execute(
                select(db.erasure_certificates).where(
                    and_(
                        db.erasure_certificates.c.tenant_id == tenant_id,
                        db.erasure_certificates.c.request_id == request_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        return _certificate_from_row(row) if row else None

    def insert_redaction_execution_or_get_existing(
        self, *, transaction: Any, record: ErasureRedactionExecutionRecord
    ) -> ErasureRedactionExecutionRecord | None:
        existing = self._redaction_execution_by_resource(
            transaction, record.tenant_id, record.request_id, record.resource_type, record.resource_id
        )
        if existing is not None:
            return existing
        # The (tenant_id, request_id, resource_type, resource_id) unique constraint
        # backstops a concurrent race so a resource is redacted at most once.
        transaction.execute(
            insert(db.erasure_redaction_executions).values(
                tenant_id=record.tenant_id, **_redaction_execution_values(record)
            )
        )
        return None

    def redaction_executions_for_request(
        self, *, transaction: Any, tenant_id: str, request_id: str
    ) -> list[ErasureRedactionExecutionRecord]:
        rows = (
            transaction.execute(
                select(db.erasure_redaction_executions).where(
                    and_(
                        db.erasure_redaction_executions.c.tenant_id == tenant_id,
                        db.erasure_redaction_executions.c.request_id == request_id,
                    )
                )
            )
            .mappings()
            .all()
        )
        return [_redaction_execution_from_row(row) for row in rows]

    def _redaction_execution_by_resource(
        self, transaction: Any, tenant_id: str, request_id: str, resource_type: str, resource_id: str
    ) -> ErasureRedactionExecutionRecord | None:
        row = (
            transaction.execute(
                select(db.erasure_redaction_executions).where(
                    and_(
                        db.erasure_redaction_executions.c.tenant_id == tenant_id,
                        db.erasure_redaction_executions.c.request_id == request_id,
                        db.erasure_redaction_executions.c.resource_type == resource_type,
                        db.erasure_redaction_executions.c.resource_id == resource_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        return _redaction_execution_from_row(row) if row else None

    def _request_by_idempotency(
        self, transaction: Any, tenant_id: str, idempotency_key: str
    ) -> ErasureRequestRecord | None:
        row = (
            transaction.execute(
                select(db.erasure_requests).where(
                    and_(
                        db.erasure_requests.c.tenant_id == tenant_id,
                        db.erasure_requests.c.idempotency_key == idempotency_key,
                    )
                )
            )
            .mappings()
            .first()
        )
        return _request_from_row(row) if row else None


def _request_values(record: ErasureRequestRecord) -> dict[str, object]:
    return {
        "id": record.request_id,
        "subject_kind": record.subject_kind,
        "subject_hash": record.subject_hash,
        "subject_token_key_id": record.subject_token_key_id,
        "requested_by": record.requested_by,
        "idempotency_key": record.idempotency_key,
        "reason": record.reason,
        "status": record.status,
        "manifest_id": record.manifest_id,
        "evidence": record.evidence,
        "requested_at": record.requested_at,
    }


def _request_from_row(row: Any) -> ErasureRequestRecord:
    return ErasureRequestRecord(
        request_id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        subject_kind=str(row["subject_kind"]),
        subject_hash=str(row["subject_hash"]),
        subject_token_key_id=str(row["subject_token_key_id"]),
        requested_by=str(row["requested_by"]),
        idempotency_key=str(row["idempotency_key"]),
        reason=str(row["reason"]),
        status=str(row["status"]),
        evidence=cast(dict[str, object], dict(row["evidence"])),
        requested_at=str(row["requested_at"]),
        manifest_id=str(row["manifest_id"]) if row["manifest_id"] is not None else None,
    )


def _certificate_values(record: ErasureCertificateRecord) -> dict[str, object]:
    return {
        "id": record.certificate_id,
        "request_id": record.request_id,
        "manifest_id": record.manifest_id,
        "certificate": record.certificate,
        "certified_at": record.certified_at,
    }


def _certificate_from_row(row: Any) -> ErasureCertificateRecord:
    return ErasureCertificateRecord(
        certificate_id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        request_id=str(row["request_id"]),
        manifest_id=str(row["manifest_id"]),
        certificate=cast(dict[str, object], dict(row["certificate"])),
        certified_at=str(row["certified_at"]),
    )


def _redaction_execution_values(record: ErasureRedactionExecutionRecord) -> dict[str, object | None]:
    return {
        "id": record.execution_id,
        "request_id": record.request_id,
        "resource_type": record.resource_type,
        "resource_id": record.resource_id,
        "surface": record.surface,
        "status": record.status,
        "new_version_id": record.new_version_id,
        "executed_at": record.executed_at,
    }


def _redaction_execution_from_row(row: Any) -> ErasureRedactionExecutionRecord:
    return ErasureRedactionExecutionRecord(
        execution_id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        request_id=str(row["request_id"]),
        resource_type=str(row["resource_type"]),
        resource_id=str(row["resource_id"]),
        surface=str(row["surface"]),
        status=str(row["status"]),
        new_version_id=str(row["new_version_id"]) if row["new_version_id"] is not None else None,
        executed_at=str(row["executed_at"]),
    )
