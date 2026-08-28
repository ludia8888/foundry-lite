"""M7 ASR processing through MediaProcessingService.

ASR is a distinct processor family: its asr_v1 derivative coexists with a pdf_text
derivative on the same version, it pins the speech model version (so a model upgrade
re-processes), it inherits the source security envelope, and its audio_segment content
units persist their time codes / speaker / language — proven over a real SQLite engine
+ local storage with an injected fake ASR engine.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pytest
from foundry_lite.application.ports import TransactionContext
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
from foundry_lite.infrastructure.adapters.asr_processor import (
    AsrProcessingBounds,
    AsrProcessorAdapter,
    TranscriptSegment,
)
from foundry_lite.infrastructure.adapters.local_media_source_workspace import LocalMediaSourceWorkspace
from foundry_lite.infrastructure.adapters.local_media_storage import LocalMediaStorageAdapter
from foundry_lite.infrastructure.repositories import SqlAlchemyMediaDerivativeRepository, SqlAlchemyMediaRepository
from foundry_lite.security.policy import PolicyService

_ASR_SPEC = ProcessorSpec(processor="asr_v1", processor_version="1.0", model="whisper", model_version="large-v3")


def _segments(_path: str, _bounds: AsrProcessingBounds) -> list[TranscriptSegment]:
    return [
        TranscriptSegment(start_ms=0, end_ms=1500, text="hello there", speaker="spk_0", language="en"),
        TranscriptSegment(start_ms=1500, end_ms=3200, text="general kenobi", speaker="spk_1", language="en"),
    ]


class _FakeRuntime:
    def _audit(self, conn: TransactionContext, ctx: RequestContext, *, event_type: str, **_: object) -> None: ...

    def _outbox(
        self, conn: TransactionContext, ctx: RequestContext, event_type: str, *_: object, **__: object
    ) -> str | None:
        return event_type


class _PdfFakeProcessor:
    profile_name = "fake-pdf"

    def failure_contract(self) -> object: ...

    def supports(self, request: MediaProcessingRequest) -> bool:
        return True

    def process(self, request: MediaProcessingRequest) -> MediaProcessingResult:
        unit = ProcessedContentUnit(unit_kind="page", ordinal=0, page_number=1, text="embedded", text_hash="h-pdf")
        return MediaProcessingResult(
            media_item_version_id=request.media_item_version_id,
            processing_spec_hash=request.processing_spec_hash,
            derivative_kind="pdf_text",
            content_hash="pdf-content",
            units=(unit,),
        )


@dataclass(frozen=True)
class _Env:
    ctx: RequestContext
    asr_processing: MediaProcessingService
    pdf_processing: MediaProcessingService
    derivative_repo: SqlAlchemyMediaDerivativeRepository
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
        media_source_workspace=LocalMediaSourceWorkspace(),
        media_processor=processor,
    )
    svc.bind_collaborators({"runtime_service": _FakeRuntime()})
    return svc


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    from foundry_lite.infrastructure import schema as db
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{tmp_path / 'asr.db'}", future=True)
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
    ctx = RequestContext()
    media_set = catalog.create_media_set(
        ctx,
        MediaSetSpec(
            namespace="calls",
            name="recordings",
            schema_type="audio",
            primary_format="wav",
            allowed_input_formats=("wav",),
            classification="confidential",
        ),
    )
    tx = transaction.open(ctx, media_set_id=media_set.media_set_id, idempotency_key="idem-1")
    session = upload.initiate(
        ctx, media_set_id=media_set.media_set_id, logical_path="/a.wav", supplied_mime_type="audio/wav"
    )
    staged = upload.complete(
        ctx,
        inputs=MediaUploadInput(
            media_set_id=media_set.media_set_id,
            media_transaction_id=tx,
            logical_path="/a.wav",
            supplied_mime_type="audio/wav",
            schema_type="audio",
            format="wav",
            security_envelope={"tenantId": ctx.tenant_id, "classification": "confidential"},
        ),
        upload=session,
        source=io.BytesIO(b"RIFF....WAVE source"),
    )
    transaction.commit(ctx, media_transaction_id=tx)
    return _Env(
        ctx=ctx,
        asr_processing=_processing(engine, repo, deriv, storage, AsrProcessorAdapter(asr_engine=_segments)),
        pdf_processing=_processing(engine, repo, deriv, storage, _PdfFakeProcessor()),
        derivative_repo=deriv,
        engine=engine,
        version_id=staged.media_item_version_id,
    )


def test_asr_derivative_commits_with_inherited_envelope_and_timing(env: _Env) -> None:
    outcome = env.asr_processing.process(env.ctx, media_item_version_id=env.version_id, spec=_ASR_SPEC)
    derivative = env.asr_processing.resolve_derivative(env.ctx, media_derivative_id=outcome.media_derivative_id)
    assert outcome.status == "committed"
    assert derivative.derivative_kind == "asr_v1"
    assert derivative.model_version == "large-v3"
    assert derivative.security_envelope.get("classification") == "confidential"
    with env.engine.begin() as conn:  # type: ignore[attr-defined]
        units = env.derivative_repo.get_content_units(
            transaction=conn, tenant_id=env.ctx.tenant_id, derivative_id=outcome.media_derivative_id
        )
    assert [u.unit_kind for u in units] == ["audio_segment", "audio_segment"]
    assert [(u.start_ms, u.end_ms) for u in units] == [(0, 1500), (1500, 3200)]
    assert [u.speaker for u in units] == ["spk_0", "spk_1"]
    assert {u.language for u in units} == {"en"}


def test_asr_is_idempotent_but_model_upgrade_reprocesses(env: _Env) -> None:
    first = env.asr_processing.process(env.ctx, media_item_version_id=env.version_id, spec=_ASR_SPEC)
    replay = env.asr_processing.process(env.ctx, media_item_version_id=env.version_id, spec=_ASR_SPEC)
    assert replay.is_duplicate is True and replay.media_derivative_id == first.media_derivative_id

    upgraded = ProcessorSpec(processor="asr_v1", processor_version="1.0", model="whisper", model_version="large-v4")
    after_upgrade = env.asr_processing.process(env.ctx, media_item_version_id=env.version_id, spec=upgraded)
    assert after_upgrade.media_derivative_id != first.media_derivative_id


def test_asr_and_pdf_text_derivatives_coexist_on_one_version(env: _Env) -> None:
    asr = env.asr_processing.process(env.ctx, media_item_version_id=env.version_id, spec=_ASR_SPEC)
    pdf = env.pdf_processing.process(
        env.ctx,
        media_item_version_id=env.version_id,
        spec=ProcessorSpec(processor="pdf_text_v1", processor_version="1.0"),
    )
    assert asr.media_derivative_id != pdf.media_derivative_id
    with env.engine.begin() as conn:  # type: ignore[attr-defined]
        kinds = {
            d.derivative_kind
            for d in env.derivative_repo.get_derivatives(
                transaction=conn, ids=[asr.media_derivative_id, pdf.media_derivative_id]
            )
        }
    assert kinds == {"asr_v1", "pdf_text"}
