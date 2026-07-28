"""L10 real FFmpeg scene-frame + Tesseract OCR live test (Media/Content Plane).

Proves the real ``video-scene-frames`` profile end-to-end against system ``ffmpeg`` + Tesseract
(both installed in CI): a committed video whose on-screen text cards ("INVOICE 2026" /
"TOTAL 4242 USD" / "NET 30 DAYS") are read by extracting scene frames with real ffmpeg
(``select='eq(n,0)+gt(scene,T)',showinfo`` — one PNG per scene frame, ``pts_time`` timecodes from
showinfo stderr) and OCRing each frame with real Tesseract, committing a ``video_scene_frames``
derivative + ordered ``video_frame`` content units + a SUCCEEDED ``media_processing_runs`` row.
The units then project into a content index and the video is found by searching "invoice"/"4242",
proving on-screen video text is searchable (video -> frame -> OCR -> content_unit -> index ->
search). It also proves operator-evidence: a corrupt video records a FAILED
``media_processing_runs`` row (failure_kind == validation) with no derivative committed.

ffmpeg + Tesseract are hard requirements here (CI installs ``ffmpeg`` + ``tesseract-ocr``); the
test never skips, so the real engine glue stays in the coverage lane.
"""

from __future__ import annotations

import functools
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
from foundry_lite.infrastructure.adapters.local_completion import LocalCompletionAdapter
from foundry_lite.infrastructure.adapters.local_content_index import LocalContentIndexAdapter
from foundry_lite.infrastructure.adapters.local_embedding import LocalEmbeddingAdapter
from foundry_lite.infrastructure.adapters.local_media_storage import LocalMediaStorageAdapter
from foundry_lite.infrastructure.adapters.video_probe_processor import (
    VideoSceneFrameProcessorAdapter,
    _ffmpeg_scene_frame_extractor,
)
from foundry_lite.infrastructure.repositories import (
    SqlAlchemyMediaDerivativeRepository,
    SqlAlchemyMediaRepository,
)
from foundry_lite.security.policy import PolicyService
from sqlalchemy import create_engine

_FRAMES_SPEC = ProcessorSpec(
    processor="video_frames_v1", processor_version="1.0", model="tesseract", model_version="5.5.2"
)
_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "media" / "video_text_frames.mp4"
# MORE_SENSITIVE picks up all three on-screen text cards (per the proven recipe).
_EXTRACTOR = functools.partial(_ffmpeg_scene_frame_extractor, scene_sensitivity="MORE_SENSITIVE")


class _FakeRuntime:
    def _audit(self, conn: TransactionContext, ctx: RequestContext, *, event_type: str, **_: object) -> None: ...

    def _outbox(
        self, conn: TransactionContext, ctx: RequestContext, event_type: str, *_: object, **__: object
    ) -> str | None:
        return event_type


@dataclass(frozen=True)
class _Env:
    ctx: RequestContext
    processing: MediaProcessingService
    indexing: MediaIndexingService
    retrieval: DefaultContentRetrievalService
    transaction: MediaTransactionService
    upload: MediaUploadService
    media_set_id: str


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    engine = create_engine(f"sqlite:///{tmp_path / 'video_frame_live.db'}", future=True)
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
    processing = MediaProcessingService(
        engine=engine,
        policy=PolicyService(),
        media_repository=repo,
        media_derivative_repository=deriv,
        media_storage=storage,
        media_processor=VideoSceneFrameProcessorAdapter(scene_frame_extractor=_EXTRACTOR),
    )
    processing.bind_collaborators({"runtime_service": runtime})
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
    return _Env(ctx, processing, indexing, retrieval, transaction, upload, media_set.media_set_id)


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


@pytest.mark.integration_scenario("media-video-frame-ocr")
def test_real_scene_frame_ocr_makes_on_screen_text_searchable(env: _Env) -> None:
    version_id = _commit_video(env, _FIXTURE.read_bytes(), logical_path="/invoice.mp4")

    outcome = env.processing.process(env.ctx, media_item_version_id=version_id, spec=_FRAMES_SPEC)
    derivative = env.processing.resolve_derivative(env.ctx, media_derivative_id=outcome.media_derivative_id)
    assert outcome.status == "committed"
    assert derivative.derivative_kind == "video_scene_frames"

    with env.processing.engine.begin() as conn:
        units = env.processing.media_derivative_repository.get_content_units(
            transaction=conn, tenant_id=env.ctx.tenant_id, derivative_id=outcome.media_derivative_id
        )
    assert all(unit.unit_kind == "video_frame" for unit in units)
    timecodes = [unit.start_ms for unit in units]
    assert timecodes == sorted(timecodes)
    recognized = " ".join(unit.text for unit in units).upper()
    assert "INVOICE 2026" in recognized
    assert "4242" in recognized
    assert "NET 30" in recognized

    runs = env.processing.list_media_runs(env.ctx, source_media_item_version_id=version_id)
    assert any(run.status == "SUCCEEDED" and run.media_derivative_id == outcome.media_derivative_id for run in runs)

    env.indexing.index_derivative(env.ctx, media_derivative_id=outcome.media_derivative_id, generation="g1")
    env.indexing.promote(env.ctx, expected_active="", generation="g1")
    for term in ("invoice", "4242"):
        hits = env.retrieval.search_content(env.ctx, query=HybridContentQuery(tenant_id=env.ctx.tenant_id, text=term))
        assert any(hit.source_media_item_version_id == version_id for hit in hits)


@pytest.mark.integration_scenario("media-video-frame-ocr")
def test_real_scene_frame_failure_is_visible_as_operator_evidence(env: _Env) -> None:
    version_id = _commit_video(env, os.urandom(2048), logical_path="/garbage.mp4")

    outcome = env.processing.process(env.ctx, media_item_version_id=version_id, spec=_FRAMES_SPEC)
    assert outcome.status == "failed"

    runs = env.processing.list_media_runs(env.ctx, source_media_item_version_id=version_id)
    failed = next(run for run in runs if run.status == "FAILED")
    assert failed.failure_kind == "validation"
    detail = env.processing.media_run_detail(env.ctx, media_processing_run_id=failed.media_processing_run_id)
    assert detail.failure_kind == "validation" and detail.media_derivative_id is None
