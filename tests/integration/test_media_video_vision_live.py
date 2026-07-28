"""L11 real CLIP scene-frame VISUAL search live test (Media/Content Plane).

Proves OPEN-VOCABULARY "is there a car in this video?" REALLY works against real ffmpeg + the
real fastembed CLIP pair (``Qdrant/clip-ViT-B-32-vision`` + ``-text``, ONNX, no torch — the model
fastembed downloads to ~/.cache/huggingface, pre-fetched/cached in CI). A committed video with
three distinct scenes (a car / a tree / a dog, distinct background colors) is processed by the
real ``video-scene-vision`` profile: real ffmpeg extracts the three scene frames, real CLIP
embeds each frame image, and a ``video_scene_vision`` derivative + ordered ``video_frame_visual``
content units (each carrying its CLIP vector) + a SUCCEEDED ``media_processing_runs`` row commit.
Then a real CLIP-TEXT visual query "a photo of a car" ranks the CAR frame first (and "a photo of
a dog" ranks the DOG frame first) — only a contrastive image+text shared space can do this, so
the visual ranking is provably driven by real visual understanding (Foundry's extract_scene_frames
-> imageToEmbeddingsV1 -> nearestNeighbors mode, with index/query model pinned to one CLIP model).
It also proves operator-evidence: a corrupt video records a FAILED ``media_processing_runs`` row
(failure_kind == validation) with no derivative committed.

ffmpeg is a hard requirement (CI installs it) and the CLIP model is pre-fetched in CI, so the test
never skips and the real vision glue stays in the coverage lane.
"""

from __future__ import annotations

import functools
import io
import os
from dataclasses import dataclass
from pathlib import Path

import pytest
from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.media_processor import ProcessorSpec
from foundry_lite.application.services.media.catalog import MediaCatalogService, MediaSetSpec
from foundry_lite.application.services.media.processing import MediaProcessingService
from foundry_lite.application.services.media.transactions import MediaTransactionService
from foundry_lite.application.services.media.uploads import MediaUploadInput, MediaUploadService
from foundry_lite.application.services.media.visual_search import MediaVisualSearchService
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.local_content_index import LocalContentIndexAdapter
from foundry_lite.infrastructure.adapters.local_media_storage import LocalMediaStorageAdapter
from foundry_lite.infrastructure.adapters.local_vision_embedding import (
    CLIP_MODEL_VERSION,
    LocalVisionEmbeddingAdapter,
    _fastembed_clip_image_engine,
    _fastembed_clip_text_engine,
)
from foundry_lite.infrastructure.adapters.video_probe_processor import (
    VideoSceneVisionProcessorAdapter,
    _ffmpeg_scene_frame_paths,
)
from foundry_lite.infrastructure.repositories import SqlAlchemyMediaDerivativeRepository, SqlAlchemyMediaRepository
from foundry_lite.security.policy import PolicyService
from sqlalchemy import create_engine

_VISION_SPEC = ProcessorSpec(
    processor="video_vision_v1", processor_version="1.0", model="clip", model_version=CLIP_MODEL_VERSION
)
_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "media" / "video_scenes.mp4"
# MORE_SENSITIVE detects all three distinct scenes (the proven recipe; backgrounds are distinct
# colors specifically so ffmpeg scene detection triggers on each one).
_EXTRACTOR = functools.partial(_ffmpeg_scene_frame_paths, scene_sensitivity="MORE_SENSITIVE")


class _FakeRuntime:
    def _audit(self, conn: TransactionContext, ctx: RequestContext, *, event_type: str, **_: object) -> None: ...

    def _outbox(
        self, conn: TransactionContext, ctx: RequestContext, event_type: str, *_: object, **__: object
    ) -> str | None:
        return event_type


def _real_vision_adapter() -> LocalVisionEmbeddingAdapter:
    # Composed exactly as local_runtime wires it: the real fastembed CLIP image+text towers.
    return LocalVisionEmbeddingAdapter(
        image_engine=_fastembed_clip_image_engine,
        text_engine=_fastembed_clip_text_engine,
        model_version=CLIP_MODEL_VERSION,
    )


@dataclass(frozen=True)
class _Env:
    ctx: RequestContext
    processing: MediaProcessingService
    visual_search: MediaVisualSearchService
    transaction: MediaTransactionService
    upload: MediaUploadService
    media_set_id: str


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    engine = create_engine(f"sqlite:///{tmp_path / 'video_vision_live.db'}", future=True)
    db.create_database(engine)
    repo = SqlAlchemyMediaRepository(engine)
    deriv = SqlAlchemyMediaDerivativeRepository(engine)
    storage = LocalMediaStorageAdapter(tmp_path / "media")
    runtime = _FakeRuntime()
    index = LocalContentIndexAdapter()
    vision = _real_vision_adapter()
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
        media_processor=VideoSceneVisionProcessorAdapter(vision, scene_frame_path_extractor=_EXTRACTOR),
    )
    processing.bind_collaborators({"runtime_service": runtime})
    visual_search = MediaVisualSearchService(
        engine=engine,
        policy=PolicyService(allow_unwired_classification_provider=True),
        media_derivative_repository=deriv,
        content_index_adapter=index,
        vision_embedding_model_adapter=vision,
    )
    visual_search.bind_collaborators({"runtime_service": runtime})
    ctx = RequestContext(roles=("ops_manager",))
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
    return _Env(ctx, processing, visual_search, transaction, upload, media_set.media_set_id)


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


@pytest.mark.integration_scenario("media-video-vision")
def test_real_clip_visual_search_finds_the_car_frame_in_the_video(env: _Env) -> None:
    version_id = _commit_video(env, _FIXTURE.read_bytes(), logical_path="/scenes.mp4")

    outcome = env.processing.process(env.ctx, media_item_version_id=version_id, spec=_VISION_SPEC)
    derivative = env.processing.resolve_derivative(env.ctx, media_derivative_id=outcome.media_derivative_id)
    assert outcome.status == "committed"
    assert derivative.derivative_kind == "video_scene_vision"

    with env.processing.engine.begin() as conn:
        units = env.processing.media_derivative_repository.get_content_units(
            transaction=conn, tenant_id=env.ctx.tenant_id, derivative_id=outcome.media_derivative_id
        )
    assert [unit.unit_kind for unit in units] == ["video_frame_visual"] * 3
    timecodes = [unit.start_ms for unit in units]
    assert timecodes == sorted(timecodes)
    assert all(len(unit.embedding) == 512 for unit in units)  # the real CLIP image space is 512-dim

    runs = env.processing.list_media_runs(env.ctx, source_media_item_version_id=version_id)
    assert any(run.status == "SUCCEEDED" and run.media_derivative_id == outcome.media_derivative_id for run in runs)

    env.visual_search.index_visual_derivative(env.ctx, media_derivative_id=outcome.media_derivative_id, generation="v1")
    env.visual_search.promote(env.ctx, expected_active="", generation="v1")

    # Open-vocabulary visual query via the CLIP TEXT tower: the CAR frame ranks first for "a photo
    # of a car" and the DOG frame first for "a photo of a dog" — real cross-modal visual search.
    car_hits = env.visual_search.search_visual(env.ctx, text="a photo of a car")
    assert car_hits[0].source_media_item_version_id == version_id
    assert car_hits[0].start_ms == timecodes[0]  # the first scene is the car

    dog_hits = env.visual_search.search_visual(env.ctx, text="a photo of a dog")
    assert dog_hits[0].source_media_item_version_id == version_id
    assert dog_hits[0].start_ms == timecodes[2]  # the third scene is the dog


@pytest.mark.integration_scenario("media-video-vision")
def test_real_scene_vision_failure_is_visible_as_operator_evidence(env: _Env) -> None:
    version_id = _commit_video(env, os.urandom(2048), logical_path="/garbage.mp4")

    outcome = env.processing.process(env.ctx, media_item_version_id=version_id, spec=_VISION_SPEC)
    assert outcome.status == "failed"

    runs = env.processing.list_media_runs(env.ctx, source_media_item_version_id=version_id)
    failed = next(run for run in runs if run.status == "FAILED")
    assert failed.failure_kind == "validation"
    detail = env.processing.media_run_detail(env.ctx, media_processing_run_id=failed.media_processing_run_id)
    assert detail.failure_kind == "validation" and detail.media_derivative_id is None
