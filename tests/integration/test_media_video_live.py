"""L3 real FFmpeg/ffprobe video live test (Media/Content Plane).

Proves the real ``ffprobe`` profile end-to-end against a system ``ffprobe`` binary (ffmpeg,
installed in CI): a committed video version is probed into a ``video_probe`` derivative
carrying real container/stream metadata, and a SUCCEEDED ``media_processing_runs`` row is
recorded. It also proves the downstream flow raw video -> subtitles -> searchable: the SAME
mp4 is transcribed by the real ``asr-whisper`` engine (faster-whisper/PyAV decodes the video's
audio track directly) into ``audio_segment`` content units that project into a content index
and become searchable. Finally it proves operator-evidence: a corrupt video records a FAILED
``media_processing_runs`` row (failure_kind == validation) visible through ``list_media_runs``
/ ``media_run_detail`` with no derivative committed.

ffmpeg/ffprobe and faster-whisper are hard requirements here (CI installs ``ffmpeg`` and
caches the Whisper ``tiny`` model); the test never skips, so the real engine glue stays in the
coverage lane.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from pathlib import Path

import pytest
from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.content_index import HybridContentQuery
from foundry_lite.application.ports.media_processor import ProcessorSpec
from foundry_lite.application.services.media.catalog import MediaCatalogService, MediaSetSpec
from foundry_lite.application.services.media.indexing import MediaIndexingService
from foundry_lite.application.services.media.processing import MediaProcessingService
from foundry_lite.application.services.media.retrieval import DefaultContentRetrievalService
from foundry_lite.application.services.media.transactions import MediaTransactionService
from foundry_lite.application.services.media.uploads import MediaUploadInput, MediaUploadService
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.asr_processor import AsrProcessorAdapter, _faster_whisper_asr_engine
from foundry_lite.infrastructure.adapters.local_completion import LocalCompletionAdapter
from foundry_lite.infrastructure.adapters.local_content_index import LocalContentIndexAdapter
from foundry_lite.infrastructure.adapters.local_embedding import LocalEmbeddingAdapter
from foundry_lite.infrastructure.adapters.local_media_storage import LocalMediaStorageAdapter
from foundry_lite.infrastructure.adapters.video_probe_processor import (
    VideoProbeProcessorAdapter,
    _ffprobe_video_probe_runner,
)
from foundry_lite.infrastructure.repositories import (
    SqlAlchemyMediaDerivativeRepository,
    SqlAlchemyMediaRepository,
)
from foundry_lite.security.policy import PolicyService
from sqlalchemy import create_engine

_PROBE_SPEC = ProcessorSpec(processor="video_probe_v1", processor_version="1.0", model="ffprobe", model_version="8.0")
_ASR_SPEC = ProcessorSpec(processor="asr_v1", processor_version="1.0", model="whisper", model_version="tiny")
_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "media" / "video_quick_brown_fox.mp4"


class _FakeRuntime:
    def _audit(self, conn: TransactionContext, ctx: RequestContext, *, event_type: str, **_: object) -> None: ...

    def _outbox(
        self, conn: TransactionContext, ctx: RequestContext, event_type: str, *_: object, **__: object
    ) -> str | None:
        return event_type


@dataclass(frozen=True)
class _Env:
    ctx: RequestContext
    probe_processing: MediaProcessingService
    asr_processing: MediaProcessingService
    indexing: MediaIndexingService
    retrieval: DefaultContentRetrievalService
    transaction: MediaTransactionService
    upload: MediaUploadService
    media_set_id: str


def _processing(engine, repo, deriv, storage, runtime, processor) -> MediaProcessingService:
    service = MediaProcessingService(
        engine=engine,
        policy=PolicyService(),
        media_repository=repo,
        media_derivative_repository=deriv,
        media_storage=storage,
        media_processor=processor,
    )
    service.bind_collaborators({"runtime_service": runtime})
    return service


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    engine = create_engine(f"sqlite:///{tmp_path / 'video_live.db'}", future=True)
    db.create_database(engine)
    repo = SqlAlchemyMediaRepository(engine)
    deriv = SqlAlchemyMediaDerivativeRepository(engine)
    storage = LocalMediaStorageAdapter(tmp_path / "media")
    runtime = _FakeRuntime()
    embedding = LocalEmbeddingAdapter()
    index = LocalContentIndexAdapter()
    catalog = MediaCatalogService(engine=engine, media_repository=repo)
    catalog.bind_collaborators({"runtime_service": runtime})
    upload = MediaUploadService(engine=engine, media_repository=repo, media_storage=storage)
    transaction = MediaTransactionService(engine=engine, media_repository=repo, media_storage=storage)
    transaction.bind_collaborators({"runtime_service": runtime})
    probe_processing = _processing(
        engine, repo, deriv, storage, runtime, VideoProbeProcessorAdapter(probe_runner=_ffprobe_video_probe_runner)
    )
    asr_processing = _processing(
        engine, repo, deriv, storage, runtime, AsrProcessorAdapter(asr_engine=_faster_whisper_asr_engine)
    )
    indexing = MediaIndexingService(
        engine=engine,
        media_derivative_repository=deriv,
        content_index_adapter=index,
        embedding_model_adapter=embedding,
    )
    indexing.bind_collaborators({"runtime_service": runtime})
    retrieval = DefaultContentRetrievalService(
        engine=engine,
        media_derivative_repository=deriv,
        content_index_adapter=index,
        embedding_model_adapter=embedding,
        completion_model_adapter=LocalCompletionAdapter(),
    )
    ctx = RequestContext()
    media_set = catalog.create_media_set(
        ctx,
        MediaSetSpec(
            namespace="clips",
            name="recordings",
            schema_type="video",
            primary_format="mp4",
            allowed_input_formats=("mp4",),
            classification="confidential",
        ),
    )
    return _Env(ctx, probe_processing, asr_processing, indexing, retrieval, transaction, upload, media_set.media_set_id)


def _commit_video(env: _Env, source: bytes, *, logical_path: str) -> str:
    tx = env.transaction.open(env.ctx, media_set_id=env.media_set_id, idempotency_key=f"idem-{logical_path}")
    session = env.upload.initiate(
        env.ctx, media_set_id=env.media_set_id, logical_path=logical_path, supplied_mime_type="video/mp4"
    )
    staged = env.upload.complete(
        env.ctx,
        inputs=MediaUploadInput(
            media_set_id=env.media_set_id,
            media_transaction_id=tx,
            logical_path=logical_path,
            supplied_mime_type="video/mp4",
            schema_type="video",
            format="mp4",
            security_envelope={"tenantId": env.ctx.tenant_id, "classification": "confidential"},
        ),
        upload=session,
        source=io.BytesIO(source),
    )
    env.transaction.commit(env.ctx, media_transaction_id=tx)
    return staged.media_item_version_id


@pytest.mark.integration_scenario("media-video")
def test_real_ffprobe_extracts_video_metadata(env: _Env) -> None:
    version_id = _commit_video(env, _FIXTURE.read_bytes(), logical_path="/fox.mp4")

    outcome = env.probe_processing.process(env.ctx, media_item_version_id=version_id, spec=_PROBE_SPEC)
    derivative = env.probe_processing.resolve_derivative(env.ctx, media_derivative_id=outcome.media_derivative_id)
    assert outcome.status == "committed"
    assert derivative.derivative_kind == "video_probe"

    with env.probe_processing.engine.begin() as conn:
        units = env.probe_processing.media_derivative_repository.get_content_units(
            transaction=conn, tenant_id=env.ctx.tenant_id, derivative_id=outcome.media_derivative_id
        )
    text = units[0].text
    assert "codec=h264" in text and "width=320 height=240" in text
    assert "has_audio=True" in text
    duration_token = next(token for token in text.split() if token.startswith("duration="))
    duration_seconds = float(duration_token.removeprefix("duration="))
    assert abs(duration_seconds - 2.737) < 0.2

    runs = env.probe_processing.list_media_runs(env.ctx, source_media_item_version_id=version_id)
    assert any(run.status == "SUCCEEDED" and run.media_derivative_id == outcome.media_derivative_id for run in runs)


@pytest.mark.integration_scenario("media-video")
def test_real_video_transcribes_audio_track_through_to_search(env: _Env) -> None:
    version_id = _commit_video(env, _FIXTURE.read_bytes(), logical_path="/fox.mp4")

    outcome = env.asr_processing.process(env.ctx, media_item_version_id=version_id, spec=_ASR_SPEC)
    derivative = env.asr_processing.resolve_derivative(env.ctx, media_derivative_id=outcome.media_derivative_id)
    assert outcome.status == "committed"
    assert derivative.derivative_kind == "asr_v1"

    with env.asr_processing.engine.begin() as conn:
        units = env.asr_processing.media_derivative_repository.get_content_units(
            transaction=conn, tenant_id=env.ctx.tenant_id, derivative_id=outcome.media_derivative_id
        )
    assert all(unit.unit_kind == "audio_segment" for unit in units)
    transcript = " ".join(unit.text for unit in units).lower()
    assert "quick brown fox" in transcript

    env.indexing.index_derivative(env.ctx, media_derivative_id=outcome.media_derivative_id, generation="g1")
    env.indexing.promote(env.ctx, expected_active="", generation="g1")
    hits = env.retrieval.search_content(env.ctx, query=HybridContentQuery(tenant_id=env.ctx.tenant_id, text="fox"))
    assert any(hit.source_media_item_version_id == version_id for hit in hits)


@pytest.mark.integration_scenario("media-video")
def test_real_ffprobe_failure_is_visible_as_operator_evidence(env: _Env) -> None:
    version_id = _commit_video(env, os.urandom(2048), logical_path="/garbage.mp4")

    outcome = env.probe_processing.process(env.ctx, media_item_version_id=version_id, spec=_PROBE_SPEC)
    assert outcome.status == "failed"

    runs = env.probe_processing.list_media_runs(env.ctx, source_media_item_version_id=version_id)
    failed = next(run for run in runs if run.status == "FAILED")
    assert failed.failure_kind == "validation"
    detail = env.probe_processing.media_run_detail(env.ctx, media_processing_run_id=failed.media_processing_run_id)
    assert detail.failure_kind == "validation" and detail.media_derivative_id is None
