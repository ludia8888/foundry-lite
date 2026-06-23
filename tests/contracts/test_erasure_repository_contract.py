from __future__ import annotations

from pathlib import Path

import pytest
from foundry_lite.application.ports.erasure_repository import (
    ErasureCertificateRecord,
    ErasureRedactionExecutionRecord,
    ErasureRequestRecord,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyErasureRepository
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


@pytest.fixture(params=["sqlalchemy", "postgres"])
def erasure_repo(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[SqlAlchemyErasureRepository, Engine]:
    if request.param == "sqlalchemy":
        engine = create_engine(f"sqlite:///{tmp_path / 'erasure.db'}", future=True)
        db.create_database(engine)
        return SqlAlchemyErasureRepository(engine), engine
    postgres_fixture = request.getfixturevalue("postgres_fixture")
    return SqlAlchemyErasureRepository(postgres_fixture.engine), postgres_fixture.engine


def _request(
    *, request_id: str = "req-1", idempotency_key: str = "idem-1", tenant_id: str = "tenant-demo"
) -> ErasureRequestRecord:
    return ErasureRequestRecord(
        request_id=request_id,
        tenant_id=tenant_id,
        subject_kind="user_email",
        subject_hash="hash-abc",
        subject_token_key_id="local-erasure-key",
        requested_by="dpo-1",
        idempotency_key=idempotency_key,
        reason="gdpr_article_17",
        status="requested",
        evidence={"subjectValue": "***REDACTED***", "requestId": request_id},
        requested_at="2026-06-22T00:00:00Z",
    )


def test_erasure_repository_request_insert_is_idempotent_and_tenant_scoped(
    erasure_repo: tuple[SqlAlchemyErasureRepository, Engine],
) -> None:
    repo, engine = erasure_repo
    with engine.begin() as conn:
        fresh = repo.insert_request_or_get_existing(transaction=conn, record=_request())
        # A different request id with the same idempotency key resolves to the first winner.
        replay = repo.insert_request_or_get_existing(transaction=conn, record=_request(request_id="req-2"))

    assert fresh is None
    assert replay is not None and replay.request_id == "req-1"

    with engine.begin() as conn:
        stored = repo.request_by_id(transaction=conn, tenant_id="tenant-demo", request_id="req-1")
        assert stored is not None and stored.subject_hash == "hash-abc" and stored.status == "requested"
        # Raw subject value is never persisted; only the redacted evidence is.
        assert stored.evidence["subjectValue"] == "***REDACTED***"
        assert repo.request_by_id(transaction=conn, tenant_id="tenant-other", request_id="req-1") is None


def test_erasure_repository_certificate_persist_is_idempotent_and_marks_executed(
    erasure_repo: tuple[SqlAlchemyErasureRepository, Engine],
) -> None:
    repo, engine = erasure_repo

    def _cert(certificate_id: str) -> ErasureCertificateRecord:
        return ErasureCertificateRecord(
            certificate_id=certificate_id,
            tenant_id="tenant-demo",
            request_id="req-1",
            manifest_id="man-1",
            certificate={"status": "CERTIFIED", "certificateId": certificate_id},
            certified_at="2026-06-22T01:00:00Z",
        )

    with engine.begin() as conn:
        repo.insert_request_or_get_existing(transaction=conn, record=_request())
        fresh = repo.insert_certificate_or_get_existing(transaction=conn, record=_cert("cert-1"))
        # A second certificate for the same request resolves to the first persisted one.
        replay = repo.insert_certificate_or_get_existing(transaction=conn, record=_cert("cert-2"))
        repo.mark_request_executed(
            transaction=conn,
            tenant_id="tenant-demo",
            request_id="req-1",
            manifest_id="man-1",
            executed_at="2026-06-22T01:00:00Z",
        )

    assert fresh is None
    assert replay is not None and replay.certificate_id == "cert-1"

    with engine.begin() as conn:
        certificate = repo.certificate_by_request(transaction=conn, tenant_id="tenant-demo", request_id="req-1")
        assert certificate is not None and certificate.manifest_id == "man-1"
        executed = repo.request_by_id(transaction=conn, tenant_id="tenant-demo", request_id="req-1")
        assert executed is not None and executed.status == "executed" and executed.manifest_id == "man-1"
        assert repo.certificate_by_request(transaction=conn, tenant_id="tenant-other", request_id="req-1") is None


def test_erasure_repository_redaction_execution_insert_is_idempotent_per_resource(
    erasure_repo: tuple[SqlAlchemyErasureRepository, Engine],
) -> None:
    repo, engine = erasure_repo

    def _execution(execution_id: str, *, resource_id: str = "clean.orders:row-7") -> ErasureRedactionExecutionRecord:
        return ErasureRedactionExecutionRecord(
            execution_id=execution_id,
            tenant_id="tenant-demo",
            request_id="req-1",
            resource_type="dataset_row",
            resource_id=resource_id,
            surface="clean.orders",
            status="completed",
            new_version_id="dsv-new",
            executed_at="2026-06-23T01:00:00Z",
        )

    with engine.begin() as conn:
        fresh = repo.insert_redaction_execution_or_get_existing(transaction=conn, record=_execution("exec-1"))
        # A replay for the same resource resolves to the first persisted record (idempotent).
        replay = repo.insert_redaction_execution_or_get_existing(transaction=conn, record=_execution("exec-2"))
        repo.insert_redaction_execution_or_get_existing(
            transaction=conn, record=_execution("exec-3", resource_id="clean.orders:row-9")
        )

    assert fresh is None
    assert replay is not None and replay.execution_id == "exec-1"

    with engine.begin() as conn:
        executions = repo.redaction_executions_for_request(
            transaction=conn, tenant_id="tenant-demo", request_id="req-1"
        )
        assert {execution.resource_id for execution in executions} == {"clean.orders:row-7", "clean.orders:row-9"}
        assert (
            repo.redaction_executions_for_request(transaction=conn, tenant_id="tenant-other", request_id="req-1") == []
        )
