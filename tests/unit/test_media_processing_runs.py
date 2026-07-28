"""L0 Media Operations / run surface through MediaProcessingService.

Every processing attempt records a run row (RUNNING -> SUCCEEDED/FAILED) so an operator
can see a failed attempt (the operator-evidence proof class). The run is operations
evidence, NEVER serving truth — a FAILED run never makes an uncommitted derivative
resolvable, and the run status never substitutes for the COMMITTED derivative
(invariant workflow_status_does_not_replace_domain_commit). Proven over a real SQLite
engine + local storage with injected fake processors.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pytest
from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.adapter_failure import AdapterError, AdapterFailure
from foundry_lite.application.ports.media_processor import (
    MediaProcessingRequest,
    MediaProcessingResult,
    ProcessedContentUnit,
    ProcessorSpec,
)
from foundry_lite.application.services.media.catalog import MediaCatalogService, MediaSetSpec
from foundry_lite.application.services.media.processing import MediaProcessingService
from foundry_lite.application.services.media.transactions import MediaTransactionService
from foundry_lite.application.services.media.uploads import MediaUploadInput, MediaUploadService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.local_media_storage import LocalMediaStorageAdapter
from foundry_lite.infrastructure.repositories import SqlAlchemyMediaDerivativeRepository, SqlAlchemyMediaRepository
from foundry_lite.security.policy import PolicyService
from sqlalchemy import select

_SPEC = ProcessorSpec(processor="ocr_v1", processor_version="1.0", model="tesseract", model_version="5.3.0")


class _FakeRuntime:
    def _audit(self, conn: TransactionContext, ctx: RequestContext, *, event_type: str, **_: object) -> None: ...

    def _outbox(
        self, conn: TransactionContext, ctx: RequestContext, event_type: str, *_: object, **__: object
    ) -> str | None:
        return event_type


class _OkProcessor:
    profile_name = "fake-ok"

    def failure_contract(self) -> object: ...

    def supports(self, request: MediaProcessingRequest) -> bool:
        return True

    def process(self, request: MediaProcessingRequest) -> MediaProcessingResult:
        unit = ProcessedContentUnit(unit_kind="page", ordinal=0, page_number=1, text="ocr text", text_hash="h-1")
        return MediaProcessingResult(
            media_item_version_id=request.media_item_version_id,
            processing_spec_hash=request.processing_spec_hash,
            derivative_kind="ocr_v1",
            content_hash="content-1",
            units=(unit,),
        )


class _FailingProcessor:
    profile_name = "fake-fail"

    def failure_contract(self) -> object: ...

    def supports(self, request: MediaProcessingRequest) -> bool:
        return True

    def process(self, request: MediaProcessingRequest) -> MediaProcessingResult:
        raise AdapterError(AdapterFailure("fake-fail", "process", "validation", False, "undecodable_image"))


class _UnexpectedProcessor:
    profile_name = "fake-unexpected"

    def failure_contract(self) -> object: ...

    def supports(self, request: MediaProcessingRequest) -> bool:
        return True

    def process(self, request: MediaProcessingRequest) -> MediaProcessingResult:
        raise RuntimeError("Authorization: Bearer raw-secret-token")


@dataclass(frozen=True)
class _Env:
    ctx: RequestContext
    ok_processing: MediaProcessingService
    failing_processing: MediaProcessingService
    unexpected_processing: MediaProcessingService
    engine: object
    version_id: str


def _processing(
    engine: object, repo: object, deriv: object, storage: object, processor: object
) -> MediaProcessingService:
    svc = MediaProcessingService(
        engine=engine,
        policy=PolicyService(),
        media_repository=repo,
        media_derivative_repository=deriv,
        media_storage=storage,
        media_processor=processor,
    )
    svc.bind_collaborators({"runtime_service": _FakeRuntime()})
    return svc


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{tmp_path / 'runs.db'}", future=True)
    db.create_database(engine)
    repo = SqlAlchemyMediaRepository(engine)
    deriv = SqlAlchemyMediaDerivativeRepository(engine)
    storage = LocalMediaStorageAdapter(tmp_path / "media")
    runtime = _FakeRuntime()
    catalog = MediaCatalogService(engine=engine, media_repository=repo)
    catalog.bind_collaborators({"runtime_service": runtime})
    upload = MediaUploadService(engine=engine, media_repository=repo, media_storage=storage)
    transaction = MediaTransactionService(engine=engine, media_repository=repo, media_storage=storage)
    transaction.bind_collaborators({"runtime_service": runtime})
    ctx = RequestContext(tenant_id="tenant-demo")
    media_set = catalog.create_media_set(
        ctx,
        MediaSetSpec(
            namespace="legal",
            name="contracts",
            schema_type="document",
            primary_format="pdf",
            allowed_input_formats=("pdf",),
            classification="confidential",
        ),
    )
    tx = transaction.open(ctx, media_set_id=media_set.media_set_id, idempotency_key="idem-1")
    session = upload.initiate(
        ctx, media_set_id=media_set.media_set_id, logical_path="/a.pdf", supplied_mime_type="application/pdf"
    )
    staged = upload.complete(
        ctx,
        inputs=MediaUploadInput(
            media_set_id=media_set.media_set_id,
            media_transaction_id=tx,
            logical_path="/a.pdf",
            supplied_mime_type="application/pdf",
            schema_type="document",
            format="pdf",
            security_envelope={"tenantId": ctx.tenant_id, "classification": "confidential"},
        ),
        upload=session,
        source=io.BytesIO(b"%PDF-1.4 source"),
    )
    transaction.commit(ctx, media_transaction_id=tx)
    return _Env(
        ctx=ctx,
        ok_processing=_processing(engine, repo, deriv, storage, _OkProcessor()),
        failing_processing=_processing(engine, repo, deriv, storage, _FailingProcessor()),
        unexpected_processing=_processing(engine, repo, deriv, storage, _UnexpectedProcessor()),
        engine=engine,
        version_id=staged.media_item_version_id,
    )


def test_success_records_a_succeeded_run_pointing_at_the_committed_derivative(env: _Env) -> None:
    outcome = env.ok_processing.process(env.ctx, media_item_version_id=env.version_id, spec=_SPEC)
    runs = env.ok_processing.list_media_runs(env.ctx, source_media_item_version_id=env.version_id)
    assert len(runs) == 1
    run = runs[0]
    assert run.status == "SUCCEEDED"
    assert run.media_derivative_id == outcome.media_derivative_id
    assert run.finished_at is not None and run.failure_kind is None


def test_failure_records_a_failed_run_and_no_committed_derivative_is_visible(env: _Env) -> None:
    outcome = env.failing_processing.process(env.ctx, media_item_version_id=env.version_id, spec=_SPEC)
    runs = env.failing_processing.list_media_runs(env.ctx, source_media_item_version_id=env.version_id)
    assert len(runs) == 1
    run = runs[0]
    assert run.status == "FAILED"
    assert run.failure_kind == "validation"
    assert run.failure_reason == "undecodable_image"
    # The run is evidence, not truth: the recorded FAILED derivative is never serving-truth.
    with pytest.raises(NotFound):
        env.failing_processing.resolve_derivative(env.ctx, media_derivative_id=outcome.media_derivative_id)


def test_unexpected_processor_exception_records_failed_run_without_pdf_text_kind(env: _Env) -> None:
    outcome = env.unexpected_processing.process(env.ctx, media_item_version_id=env.version_id, spec=_SPEC)
    runs = env.unexpected_processing.list_media_runs(env.ctx, source_media_item_version_id=env.version_id)
    run = runs[0]
    with env.engine.begin() as conn:  # type: ignore[attr-defined]
        derivative = (
            conn.execute(select(db.media_derivatives).where(db.media_derivatives.c.id == outcome.media_derivative_id))
            .mappings()
            .one()
        )

    assert outcome.status == "failed"
    assert outcome.error is not None
    assert outcome.error["kind"] == "unknown"
    assert outcome.error["operatorMessage"] == "unexpected media processor error"
    assert run.status == "FAILED"
    assert run.failure_kind == "unknown"
    assert run.failure_reason == "unexpected media processor error"
    assert derivative["derivative_kind"] == _SPEC.processor
    with pytest.raises(NotFound):
        env.unexpected_processing.resolve_derivative(env.ctx, media_derivative_id=outcome.media_derivative_id)


def test_media_run_detail_is_tenant_scoped(env: _Env) -> None:
    env.ok_processing.process(env.ctx, media_item_version_id=env.version_id, spec=_SPEC)
    run = env.ok_processing.list_media_runs(env.ctx)[0]
    other = RequestContext(tenant_id="tenant-other")
    with pytest.raises(NotFound):
        env.ok_processing.media_run_detail(other, media_processing_run_id=run.media_processing_run_id)
    found = env.ok_processing.media_run_detail(env.ctx, media_processing_run_id=run.media_processing_run_id)
    assert found.media_processing_run_id == run.media_processing_run_id


def test_replay_records_a_succeeded_run_for_the_duplicate_attempt(env: _Env) -> None:
    env.ok_processing.process(env.ctx, media_item_version_id=env.version_id, spec=_SPEC)
    replay = env.ok_processing.process(env.ctx, media_item_version_id=env.version_id, spec=_SPEC)
    runs = env.ok_processing.list_media_runs(env.ctx, source_media_item_version_id=env.version_id)
    assert replay.is_duplicate is True
    # Both attempts are visible to an operator; both succeeded against the same derivative.
    assert len(runs) == 2
    assert {run.status for run in runs} == {"SUCCEEDED"}
    assert {run.media_derivative_id for run in runs} == {replay.media_derivative_id}
