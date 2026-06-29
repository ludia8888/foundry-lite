"""L11 scene-frame VISUAL embedding + open-vocabulary visual search (Media/Content Plane).

Deterministic (no model download): a fake CLIP vision adapter maps each frame path to a vector
and the query text to a vector in the SAME space. Proves the ``video-scene-vision`` processor
emits ordered ``video_frame_visual`` units carrying the injected image vectors + a
``video_scene_vision`` derivative; the default vision engine fails closed
(``vision_model_unavailable``); a CLIP-text visual query ranks the matching frame first through
the CLIP-pinned generation; and the model-version guard fails closed so a CLIP query can never
match a bge-pinned generation (no silent vector-space mixing). The real CLIP model is exercised
by the live ratchet ``test_media_video_vision_live.py``.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.content_index import HybridContentQuery
from foundry_lite.application.ports.embedding_model import EmbeddingModelError, EmbeddingVector
from foundry_lite.application.ports.media_processor import ProcessorSpec
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.application.services.media.catalog import MediaCatalogService, MediaSetSpec
from foundry_lite.application.services.media.processing import MediaProcessingService
from foundry_lite.application.services.media.transactions import MediaTransactionService
from foundry_lite.application.services.media.uploads import MediaUploadInput, MediaUploadService
from foundry_lite.application.services.media.visual_search import MediaVisualSearchService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.local_content_index import LocalContentIndexAdapter
from foundry_lite.infrastructure.adapters.local_media_storage import LocalMediaStorageAdapter
from foundry_lite.infrastructure.adapters.local_vision_embedding import (
    CLIP_MODEL_VERSION,
    LocalVisionEmbeddingAdapter,
)
from foundry_lite.infrastructure.adapters.video_probe_processor import VideoSceneVisionProcessorAdapter
from foundry_lite.infrastructure.repositories import SqlAlchemyMediaDerivativeRepository, SqlAlchemyMediaRepository
from foundry_lite.security.policy import PolicyService
from sqlalchemy import create_engine

_VISION_SPEC = ProcessorSpec(processor="video_vision_v1", processor_version="1.0", model="clip", model_version="b32")
# Three frames at ordered timecodes; each maps to a distinct one-hot vector in the shared space.
_CAR = (0, "a_car.png", (1.0, 0.0, 0.0))
_TREE = (1000, "b_tree.png", (0.0, 1.0, 0.0))
_DOG = (2000, "c_dog.png", (0.0, 0.0, 1.0))
_FRAMES = (_CAR, _TREE, _DOG)
_VECTOR_BY_BASENAME = {basename: vector for _, basename, vector in _FRAMES}
# Query "vectors" align to the matching frame's space (a real CLIP text tower does this).
_QUERY_VECTOR = {"a photo of a car": (0.9, 0.1, 0.0), "a photo of a dog": (0.0, 0.1, 0.9)}


def _fake_frame_path_extractor(source_path: str, outdir: str) -> list[tuple[int, str]]:
    del source_path
    return [(start_ms, f"{outdir}/{basename}") for start_ms, basename, _ in _FRAMES]


def _fake_image_engine(image_paths: Sequence[str]) -> list[EmbeddingVector]:
    return [_VECTOR_BY_BASENAME[Path(path).name] for path in image_paths]


def _fake_text_engine(texts: Sequence[str]) -> list[EmbeddingVector]:
    return [_QUERY_VECTOR[text] for text in texts]


def _fake_vision_adapter() -> LocalVisionEmbeddingAdapter:
    return LocalVisionEmbeddingAdapter(
        image_engine=_fake_image_engine, text_engine=_fake_text_engine, model_version=CLIP_MODEL_VERSION
    )


class _FakeRuntime:
    def _audit(self, conn: TransactionContext, ctx: RequestContext, *, event_type: str, **_: object) -> None:
        return None

    def _outbox(
        self, conn: TransactionContext, ctx: RequestContext, event_type: str, *_: object, **__: object
    ) -> str | None:
        return event_type


@dataclass(frozen=True)
class _Env:
    ctx: RequestContext
    processing: MediaProcessingService
    visual_search: MediaVisualSearchService
    index: LocalContentIndexAdapter
    version_id: str

    def process_and_index(self) -> str:
        outcome = self.processing.process(self.ctx, media_item_version_id=self.version_id, spec=_VISION_SPEC)
        self.visual_search.index_visual_derivative(
            self.ctx, media_derivative_id=outcome.media_derivative_id, generation="v1"
        )
        self.visual_search.promote(self.ctx, expected_active="", generation="v1")
        return outcome.media_derivative_id


def _commit_video(
    transaction: MediaTransactionService, upload: MediaUploadService, ctx: RequestContext, set_id: str
) -> str:
    tx = transaction.open(ctx, media_set_id=set_id, idempotency_key="idem-vision")
    session = upload.initiate(ctx, media_set_id=set_id, logical_path="/clip.mp4", supplied_mime_type="video/mp4")
    staged = upload.complete(
        ctx,
        inputs=MediaUploadInput(
            media_set_id=set_id,
            media_transaction_id=tx,
            logical_path="/clip.mp4",
            supplied_mime_type="video/mp4",
            schema_type="video",
            format="mp4",
            security_envelope={"tenantId": ctx.tenant_id, "classification": "confidential"},
        ),
        upload=session,
        source=io.BytesIO(b"fake mp4 bytes"),
    )
    transaction.commit(ctx, media_transaction_id=tx)
    return staged.media_item_version_id


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    engine = create_engine(f"sqlite:///{tmp_path / 'l11.db'}", future=True)
    db.create_database(engine)
    repo = SqlAlchemyMediaRepository(engine)
    deriv = SqlAlchemyMediaDerivativeRepository(engine)
    storage = LocalMediaStorageAdapter(tmp_path / "media")
    runtime = _FakeRuntime()
    index = LocalContentIndexAdapter()
    vision = _fake_vision_adapter()
    catalog = MediaCatalogService(engine=engine, media_repository=repo)
    catalog.bind_collaborators({"runtime_service": runtime})
    upload = MediaUploadService(engine=engine, media_repository=repo, media_storage=storage)
    transaction = MediaTransactionService(engine=engine, media_repository=repo, media_storage=storage)
    transaction.bind_collaborators({"runtime_service": runtime})
    processing = MediaProcessingService(
        engine=engine,
        media_repository=repo,
        media_derivative_repository=deriv,
        media_storage=storage,
        media_processor=VideoSceneVisionProcessorAdapter(vision, scene_frame_path_extractor=_fake_frame_path_extractor),
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
    version_id = _commit_video(transaction, upload, ctx, media_set.media_set_id)
    return _Env(ctx, processing, visual_search, index, version_id)


def test_vision_processor_emits_ordered_visual_units_with_clip_embeddings(env: _Env) -> None:
    outcome = env.processing.process(env.ctx, media_item_version_id=env.version_id, spec=_VISION_SPEC)
    derivative = env.processing.resolve_derivative(env.ctx, media_derivative_id=outcome.media_derivative_id)
    assert derivative.derivative_kind == "video_scene_vision"

    with env.processing.engine.begin() as conn:
        units = env.processing.media_derivative_repository.get_content_units(
            transaction=conn, tenant_id=env.ctx.tenant_id, derivative_id=outcome.media_derivative_id
        )
    assert [unit.unit_kind for unit in units] == ["video_frame_visual"] * 3
    assert [unit.start_ms for unit in units] == [0, 1000, 2000]
    assert [unit.embedding for unit in units] == [_CAR[2], _TREE[2], _DOG[2]]

    runs = env.processing.list_media_runs(env.ctx, source_media_item_version_id=env.version_id)
    assert any(run.status == "SUCCEEDED" and run.media_derivative_id == outcome.media_derivative_id for run in runs)


def test_default_vision_engine_fails_closed_as_unavailable() -> None:
    adapter = LocalVisionEmbeddingAdapter()
    assert adapter.is_available is False
    with pytest.raises(EmbeddingModelError) as image_exc:
        adapter.embed_images(["frame.png"])
    assert image_exc.value.reason == "vision_model_unavailable"
    with pytest.raises(EmbeddingModelError):
        adapter.embed_query("a photo of a car")


def test_clip_text_visual_query_ranks_matching_frame_first(env: _Env) -> None:
    env.process_and_index()

    car_hits = env.visual_search.search_visual(env.ctx, text="a photo of a car")
    assert car_hits[0].start_ms == 0  # the car frame, by visual content
    assert all(hit.source_media_item_version_id == env.version_id for hit in car_hits)

    dog_hits = env.visual_search.search_visual(env.ctx, text="a photo of a dog")
    assert dog_hits[0].start_ms == 2000  # the dog frame


def test_visual_search_returns_empty_when_no_generation_is_active(env: _Env) -> None:
    # No derivative indexed/promoted yet: the active generation is empty, so a CLIP-text query
    # returns no hits (and never re-reads the DB) rather than raising.
    assert env.visual_search.search_visual(env.ctx, text="a photo of a car") == []


def test_visual_search_requires_media_search_permission(env: _Env) -> None:
    env.process_and_index()
    viewer = RequestContext(tenant_id=env.ctx.tenant_id, roles=("viewer",))

    with pytest.raises(PermissionDenied):
        env.visual_search.search_visual(viewer, text="a photo of a car")


def test_visual_search_drops_a_hit_whose_source_unit_was_purged(env: _Env) -> None:
    derivative_id = env.process_and_index()
    # The index is a projection; if the authoritative content units are gone (e.g. purged),
    # retrieval drops the stale hit rather than returning a dangling citation.
    with env.visual_search.engine.begin() as conn:
        units = env.visual_search.media_derivative_repository.get_content_units(
            transaction=conn, tenant_id=env.ctx.tenant_id, derivative_id=derivative_id
        )
        conn.execute(db.content_units.delete().where(db.content_units.c.id.in_([u.content_unit_id for u in units])))
    assert env.visual_search.search_visual(env.ctx, text="a photo of a car") == []


def test_clip_pinned_query_does_not_match_a_bge_pinned_generation(env: _Env) -> None:
    env.process_and_index()
    # A query carrying the bge model version must fail closed against the CLIP-pinned generation
    # (the existing content-index model-version guard prevents cross-space mixing).
    bge_query = HybridContentQuery(
        tenant_id=env.ctx.tenant_id,
        query_vector=(1.0, 0.0, 0.0),
        embedding_model_version="bge-small-en-v1.5",
        top_k=10,
    )
    with pytest.raises(AdapterError) as excinfo:
        env.index.search(bge_query)
    assert excinfo.value.failure.kind == "conflict"
