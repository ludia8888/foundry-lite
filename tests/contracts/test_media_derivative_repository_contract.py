from __future__ import annotations

from pathlib import Path

import pytest
from foundry_lite.application.ports.media_derivative_repository import (
    ContentUnitRecord,
    MediaDerivativeRecord,
    MediaProcessingRunRecord,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyMediaDerivativeRepository
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


@pytest.fixture(params=["sqlalchemy", "postgres"])
def derivative_repo(
    request: pytest.FixtureRequest, tmp_path: Path
) -> tuple[SqlAlchemyMediaDerivativeRepository, Engine]:
    if request.param == "sqlalchemy":
        engine = create_engine(f"sqlite:///{tmp_path / 'deriv.db'}", future=True)
        db.create_database(engine)
        return SqlAlchemyMediaDerivativeRepository(engine), engine
    postgres_fixture = request.getfixturevalue("postgres_fixture")
    return SqlAlchemyMediaDerivativeRepository(postgres_fixture.engine), postgres_fixture.engine


def _derivative(
    derivative_id: str = "mder-1", *, status: str = "STAGED", spec: str = "spec-1"
) -> MediaDerivativeRecord:
    return MediaDerivativeRecord(
        media_derivative_id=derivative_id,
        tenant_id="tenant-demo",
        source_media_item_version_id="miv-1",
        derivative_kind="pdf_text",
        processor_spec_hash=spec,
        processor_name="pdf_text_v1",
        processor_version="1.0.0",
        model_name=None,
        model_version="",
        params_hash=spec,
        security_envelope={"tenantId": "tenant-demo", "classification": "confidential"},
        status=status,
        content_hash="content-1",
        created_at="2026-06-23T00:00:00Z",
    )


def _unit(content_unit_id: str, *, ordinal: int, spec: str = "spec-1") -> ContentUnitRecord:
    return ContentUnitRecord(
        content_unit_id=content_unit_id,
        tenant_id="tenant-demo",
        source_media_item_version_id="miv-1",
        derivative_id="mder-1",
        unit_kind="page",
        ordinal=ordinal,
        text=f"page {ordinal}",
        text_hash=f"h{ordinal}",
        chunk_spec_hash=spec,
        security_envelope={"classification": "confidential"},
        page_number=ordinal + 1,
        bbox={"x": 10, "y": 20, "width": 300, "height": 40, "unit": "pt"},
        parent_content_unit_id="cu-parent",
        source_locator={"pageNumber": ordinal + 1, "blockId": f"block-{ordinal}"},
        structure={"kind": "paragraph", "level": 1},
        confidence=0.97,
        created_at="2026-06-23T00:00:00Z",
    )


def test_create_derivative_is_idempotent_on_spec_key(
    derivative_repo: tuple[SqlAlchemyMediaDerivativeRepository, Engine],
) -> None:
    repo, engine = derivative_repo
    with engine.begin() as conn:
        fresh = repo.create_derivative_or_get_existing(transaction=conn, record=_derivative())
        replay = repo.create_derivative_or_get_existing(transaction=conn, record=_derivative(derivative_id="mder-9"))

    assert fresh is None
    assert replay is not None and replay.media_derivative_id == "mder-1"
    with engine.begin() as conn:
        assert (
            repo.derivative_by_id(transaction=conn, tenant_id="tenant-demo", media_derivative_id="mder-1") is not None
        )
        assert {d.media_derivative_id for d in repo.get_derivatives(transaction=conn, ids=["mder-1"])} == {"mder-1"}


def test_commit_and_fail_are_cas_transitions(
    derivative_repo: tuple[SqlAlchemyMediaDerivativeRepository, Engine],
) -> None:
    repo, engine = derivative_repo
    with engine.begin() as conn:
        repo.create_derivative_or_get_existing(transaction=conn, record=_derivative())
        repo.create_derivative_or_get_existing(
            transaction=conn, record=_derivative(derivative_id="mder-2", spec="spec-2")
        )
        committed = repo.commit_derivative(
            transaction=conn, tenant_id="tenant-demo", media_derivative_id="mder-1", committed_at="2026-06-23T01:00:00Z"
        )
        failed = repo.fail_derivative(
            transaction=conn,
            tenant_id="tenant-demo",
            media_derivative_id="mder-2",
            error={"reason": "encrypted_pdf"},
            failed_at="2026-06-23T01:00:00Z",
        )

    assert committed is not None and committed.status == "COMMITTED"
    assert failed is not None and failed.status == "FAILED" and failed.error == {"reason": "encrypted_pdf"}


def test_content_units_insert_dedups_and_reads_in_order(
    derivative_repo: tuple[SqlAlchemyMediaDerivativeRepository, Engine],
) -> None:
    repo, engine = derivative_repo
    with engine.begin() as conn:
        repo.create_derivative_or_get_existing(transaction=conn, record=_derivative())
        inserted = repo.insert_content_units(
            transaction=conn, records=[_unit("cu-1", ordinal=0), _unit("cu-2", ordinal=1)]
        )
        # Replaying the same ordinal/chunk-spec is idempotent (no duplicate units).
        replay = repo.insert_content_units(transaction=conn, records=[_unit("cu-9", ordinal=0)])
        units = repo.get_content_units(transaction=conn, tenant_id="tenant-demo", derivative_id="mder-1")
        resolved = repo.content_unit_by_id(
            transaction=conn,
            tenant_id="tenant-demo",
            content_unit_id="cu-1",
        )
        cross_tenant = repo.content_unit_by_id(
            transaction=conn,
            tenant_id="tenant-other",
            content_unit_id="cu-1",
        )
        second_page = repo.get_content_units(
            transaction=conn,
            tenant_id="tenant-demo",
            derivative_id="mder-1",
            after_ordinal=0,
            page_number=2,
            limit=1,
        )

    assert inserted == 2
    assert replay == 0
    assert [unit.ordinal for unit in units] == [0, 1]
    assert units[0].bbox == {"x": 10, "y": 20, "width": 300, "height": 40, "unit": "pt"}
    assert units[0].parent_content_unit_id == "cu-parent"
    assert units[0].source_locator == {"pageNumber": 1, "blockId": "block-0"}
    assert units[0].structure == {"kind": "paragraph", "level": 1}
    assert units[0].confidence == pytest.approx(0.97)
    assert resolved is not None and resolved.content_unit_id == "cu-1"
    assert cross_tenant is None
    assert [unit.content_unit_id for unit in second_page] == ["cu-2"]


def _run(
    run_id: str, *, version_id: str = "miv-1", created_at: str = "2026-06-24T00:00:00Z"
) -> MediaProcessingRunRecord:
    return MediaProcessingRunRecord(
        media_processing_run_id=run_id,
        tenant_id="tenant-demo",
        source_media_item_version_id=version_id,
        processor_name="ocr_v1",
        derivative_kind="ocr_v1",
        processing_spec_hash="spec-1",
        status="RUNNING",
        started_at=created_at,
        created_at=created_at,
    )


def test_media_run_lifecycle_create_complete_and_list(
    derivative_repo: tuple[SqlAlchemyMediaDerivativeRepository, Engine],
) -> None:
    repo, engine = derivative_repo
    with engine.begin() as conn:
        repo.create_media_run(transaction=conn, record=_run("mrun-1", created_at="2026-06-24T00:00:00Z"))
        repo.create_media_run(transaction=conn, record=_run("mrun-2", created_at="2026-06-24T01:00:00Z"))
        done = repo.complete_media_run(
            transaction=conn,
            tenant_id="tenant-demo",
            media_processing_run_id="mrun-1",
            status="SUCCEEDED",
            finished_at="2026-06-24T00:05:00Z",
            media_derivative_id="mder-1",
        )
        failed = repo.complete_media_run(
            transaction=conn,
            tenant_id="tenant-demo",
            media_processing_run_id="mrun-2",
            status="FAILED",
            finished_at="2026-06-24T01:05:00Z",
            failure_kind="validation",
            failure_reason="encrypted_pdf",
        )
        listed = repo.list_media_runs(transaction=conn, tenant_id="tenant-demo")
        fetched = repo.get_media_runs(transaction=conn, ids=["mrun-1"])

    assert done is not None and done.status == "SUCCEEDED" and done.media_derivative_id == "mder-1"
    assert failed is not None and failed.failure_kind == "validation" and failed.failure_reason == "encrypted_pdf"
    # newest-first by created_at
    assert [run.media_processing_run_id for run in listed] == ["mrun-2", "mrun-1"]
    assert [run.media_processing_run_id for run in fetched] == ["mrun-1"]


def test_media_run_completion_rejects_unknown_terminal_status_without_mutating_run(
    derivative_repo: tuple[SqlAlchemyMediaDerivativeRepository, Engine],
) -> None:
    repo, engine = derivative_repo
    with engine.begin() as conn:
        repo.create_media_run(transaction=conn, record=_run("mrun-invalid-status"))
        with pytest.raises(ValueError, match="must be SUCCEEDED or FAILED"):
            repo.complete_media_run(
                transaction=conn,
                tenant_id="tenant-demo",
                media_processing_run_id="mrun-invalid-status",
                status="SUCESS",
                finished_at="2026-06-24T00:05:00Z",
            )
        current = repo.get_media_runs(transaction=conn, ids=["mrun-invalid-status"])

    assert len(current) == 1
    assert current[0].status == "RUNNING"
    assert current[0].finished_at is None


def test_list_media_runs_filters_by_version_and_tenant(
    derivative_repo: tuple[SqlAlchemyMediaDerivativeRepository, Engine],
) -> None:
    repo, engine = derivative_repo
    with engine.begin() as conn:
        repo.create_media_run(transaction=conn, record=_run("mrun-a", version_id="miv-1"))
        repo.create_media_run(transaction=conn, record=_run("mrun-b", version_id="miv-2"))
        scoped = repo.list_media_runs(transaction=conn, tenant_id="tenant-demo", source_media_item_version_id="miv-2")

    assert [run.media_processing_run_id for run in scoped] == ["mrun-b"]


def test_fetch_unreachable_staged_derivatives_finds_old_staged(
    derivative_repo: tuple[SqlAlchemyMediaDerivativeRepository, Engine],
) -> None:
    repo, engine = derivative_repo
    with engine.begin() as conn:
        repo.create_derivative_or_get_existing(transaction=conn, record=_derivative())
        repo.commit_derivative(
            transaction=conn, tenant_id="tenant-demo", media_derivative_id="mder-1", committed_at="2026-06-20T01:00:00Z"
        )
        repo.create_derivative_or_get_existing(
            transaction=conn, record=_derivative(derivative_id="mder-staged", spec="spec-staged")
        )
        orphans = repo.fetch_unreachable_staged_derivatives(
            transaction=conn, tenant_id="tenant-demo", older_than="2099-01-01T00:00:00Z"
        )

    assert [d.media_derivative_id for d in orphans] == ["mder-staged"]
