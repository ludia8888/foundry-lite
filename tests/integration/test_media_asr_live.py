"""L2 real faster-whisper ASR live test (Media/Content Plane).

Proves the real ``asr-whisper`` engine end-to-end against a real Whisper ``tiny`` model
(ctranslate2 backend, no torch): a committed audio clip is transcribed into an ``asr_v1``
derivative + time-coded ``audio_segment`` content units that commit, project into a content
index, and become searchable (raw -> ASR -> content_unit -> index -> search). It also proves
operator-evidence: undecodable audio records a FAILED ``media_processing_runs`` row
(failure_kind == validation) visible through ``list_media_runs`` / ``media_run_detail`` with
no derivative committed.

faster-whisper is a hard requirement here (installed via uv; the ``tiny`` model is fetched/
cached in CI); the test never skips on a missing model, so the real engine glue stays in the
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
from foundry_lite.infrastructure.repositories import (
    SqlAlchemyMediaDerivativeRepository,
    SqlAlchemyMediaRepository,
)
from sqlalchemy import create_engine

_ASR_SPEC = ProcessorSpec(processor="asr_v1", processor_version="1.0", model="whisper", model_version="tiny")
_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "media" / "asr_quick_brown_fox.wav"


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
    storage: LocalMediaStorageAdapter
    transaction: MediaTransactionService
    upload: MediaUploadService
    media_set_id: str


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    engine = create_engine(f"sqlite:///{tmp_path / 'asr_live.db'}", future=True)
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
        media_repository=repo,
        media_derivative_repository=deriv,
        media_storage=storage,
        media_processor=AsrProcessorAdapter(asr_engine=_faster_whisper_asr_engine),
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
            namespace="calls",
            name="recordings",
            schema_type="audio",
            primary_format="wav",
            allowed_input_formats=("wav",),
            classification="confidential",
        ),
    )
    return _Env(ctx, processing, indexing, retrieval, storage, transaction, upload, media_set.media_set_id)


def _commit_audio(env: _Env, source: bytes, *, logical_path: str) -> str:
    tx = env.transaction.open(env.ctx, media_set_id=env.media_set_id, idempotency_key=f"idem-{logical_path}")
    session = env.upload.initiate(
        env.ctx, media_set_id=env.media_set_id, logical_path=logical_path, supplied_mime_type="audio/wav"
    )
    staged = env.upload.complete(
        env.ctx,
        inputs=MediaUploadInput(
            media_set_id=env.media_set_id,
            media_transaction_id=tx,
            logical_path=logical_path,
            supplied_mime_type="audio/wav",
            schema_type="audio",
            format="wav",
            security_envelope={"tenantId": env.ctx.tenant_id, "classification": "confidential"},
        ),
        upload=session,
        source=io.BytesIO(source),
    )
    env.transaction.commit(env.ctx, media_transaction_id=tx)
    return staged.media_item_version_id


@pytest.mark.integration_scenario("media-asr")
def test_real_asr_transcribes_audio_through_to_search(env: _Env) -> None:
    version_id = _commit_audio(env, _FIXTURE.read_bytes(), logical_path="/fox.wav")

    outcome = env.processing.process(env.ctx, media_item_version_id=version_id, spec=_ASR_SPEC)
    derivative = env.processing.resolve_derivative(env.ctx, media_derivative_id=outcome.media_derivative_id)
    assert outcome.status == "committed"
    assert derivative.derivative_kind == "asr_v1"

    with env.processing.engine.begin() as conn:
        units = env.processing.media_derivative_repository.get_content_units(
            transaction=conn, tenant_id=env.ctx.tenant_id, derivative_id=outcome.media_derivative_id
        )
    assert all(unit.unit_kind == "audio_segment" for unit in units)
    transcript = " ".join(unit.text for unit in units).lower()
    assert "quick brown fox" in transcript and "lazy dog" in transcript
    for unit in units:
        assert unit.start_ms is not None and unit.end_ms is not None
        assert unit.end_ms >= unit.start_ms

    runs = env.processing.list_media_runs(env.ctx, source_media_item_version_id=version_id)
    assert any(run.status == "SUCCEEDED" and run.media_derivative_id == outcome.media_derivative_id for run in runs)

    env.indexing.index_derivative(env.ctx, media_derivative_id=outcome.media_derivative_id, generation="g1")
    env.indexing.promote(env.ctx, expected_active="", generation="g1")
    hits = env.retrieval.search_content(env.ctx, query=HybridContentQuery(tenant_id=env.ctx.tenant_id, text="fox"))
    assert any(hit.source_media_item_version_id == version_id for hit in hits)


@pytest.mark.integration_scenario("media-asr")
def test_real_asr_failure_is_visible_as_operator_evidence(env: _Env) -> None:
    version_id = _commit_audio(env, os.urandom(2048), logical_path="/garbage.wav")

    outcome = env.processing.process(env.ctx, media_item_version_id=version_id, spec=_ASR_SPEC)
    assert outcome.status == "failed"

    runs = env.processing.list_media_runs(env.ctx, source_media_item_version_id=version_id)
    failed = next(run for run in runs if run.status == "FAILED")
    assert failed.failure_kind == "validation"
    detail = env.processing.media_run_detail(env.ctx, media_processing_run_id=failed.media_processing_run_id)
    assert detail.failure_kind == "validation" and detail.media_derivative_id is None
