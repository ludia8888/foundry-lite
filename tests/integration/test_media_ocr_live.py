"""L1 real Tesseract OCR live test (Media/Content Plane).

Proves the real ``ocr-tesseract`` engine end-to-end against a system Tesseract binary
(installed in CI): a rendered PNG is OCR'd into an ``ocr_v1`` derivative + content units
that commit, project into a content index, and become searchable (raw -> OCR ->
content_unit -> index -> search). It also proves operator-evidence: an undecodable image
records a FAILED ``media_processing_runs`` row (failure_kind == validation) visible through
``list_media_runs`` / ``media_run_detail`` with no derivative committed.

Tesseract is a hard requirement here (CI installs ``tesseract-ocr``); the test never skips
on a missing binary, so the real engine glue stays in the coverage lane.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from importlib import import_module
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
from foundry_lite.infrastructure.adapters.ocr_processor import OcrProcessorAdapter, _tesseract_ocr_engine
from foundry_lite.infrastructure.repositories import (
    SqlAlchemyMediaDerivativeRepository,
    SqlAlchemyMediaRepository,
)
from foundry_lite.security.policy import PolicyService
from sqlalchemy import create_engine

_OCR_SPEC = ProcessorSpec(processor="ocr_v1", processor_version="1.0", model="tesseract", model_version="5.5.2")
_RENDERED_TEXT = "HELLO FOUNDRY"


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


def _render_text_png(path: Path) -> None:
    pil_image = import_module("PIL.Image")
    pil_draw = import_module("PIL.ImageDraw")
    # The default bitmap font is small; draw onto a small canvas and scale up so real
    # Tesseract reads the rendered text reliably.
    small = pil_image.new("RGB", (320, 80), color="white")
    pil_draw.Draw(small).text((10, 30), _RENDERED_TEXT, fill="black")
    small.resize((640, 160), pil_image.NEAREST).save(path, format="PNG")


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    engine = create_engine(f"sqlite:///{tmp_path / 'ocr_live.db'}", future=True)
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
        media_processor=OcrProcessorAdapter(ocr_engine=_tesseract_ocr_engine),
    )
    processing.bind_collaborators({"runtime_service": runtime})
    indexing = MediaIndexingService(
        engine=engine,
        media_repository=SqlAlchemyMediaRepository(engine),
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
            namespace="ops",
            name="scans",
            schema_type="image",
            primary_format="png",
            allowed_input_formats=("png",),
            classification="confidential",
        ),
    )
    return _Env(ctx, processing, indexing, retrieval, storage, transaction, upload, media_set.media_set_id)


def _commit_image(env: _Env, source: bytes, *, logical_path: str) -> str:
    tx = env.transaction.open(env.ctx, media_set_id=env.media_set_id, idempotency_key=f"idem-{logical_path}")
    session = env.upload.initiate(
        env.ctx, media_set_id=env.media_set_id, logical_path=logical_path, supplied_mime_type="image/png"
    )
    staged = env.upload.complete(
        env.ctx,
        inputs=MediaUploadInput(
            media_set_id=env.media_set_id,
            media_transaction_id=tx,
            logical_path=logical_path,
            supplied_mime_type="image/png",
            schema_type="image",
            format="png",
            security_envelope={"tenantId": env.ctx.tenant_id, "classification": "confidential"},
        ),
        upload=session,
        source=io.BytesIO(source),
    )
    env.transaction.commit(env.ctx, media_transaction_id=tx)
    return staged.media_item_version_id


@pytest.mark.integration_scenario("media-ocr")
def test_real_ocr_recognizes_rendered_text_through_to_search(env: _Env, tmp_path: Path) -> None:
    png_path = tmp_path / "hello.png"
    _render_text_png(png_path)
    version_id = _commit_image(env, png_path.read_bytes(), logical_path="/hello.png")

    outcome = env.processing.process(env.ctx, media_item_version_id=version_id, spec=_OCR_SPEC)
    derivative = env.processing.resolve_derivative(env.ctx, media_derivative_id=outcome.media_derivative_id)
    assert outcome.status == "committed"
    assert derivative.derivative_kind == "ocr_v1"

    with env.processing.engine.begin() as conn:
        units = env.processing.media_derivative_repository.get_content_units(
            transaction=conn, tenant_id=env.ctx.tenant_id, derivative_id=outcome.media_derivative_id
        )
    recognized = " ".join(unit.text for unit in units).upper()
    assert "HELLO" in recognized and "FOUNDRY" in recognized

    runs = env.processing.list_media_runs(env.ctx, source_media_item_version_id=version_id)
    assert any(run.status == "SUCCEEDED" and run.media_derivative_id == outcome.media_derivative_id for run in runs)

    env.indexing.index_derivative(env.ctx, media_derivative_id=outcome.media_derivative_id, generation="g1")
    env.indexing.promote(env.ctx, expected_active="", generation="g1")
    hits = env.retrieval.search_content(env.ctx, query=HybridContentQuery(tenant_id=env.ctx.tenant_id, text="hello"))
    assert any(hit.source_media_item_version_id == version_id for hit in hits)


@pytest.mark.integration_scenario("media-ocr")
def test_real_ocr_failure_is_visible_as_operator_evidence(env: _Env) -> None:
    version_id = _commit_image(env, os.urandom(256), logical_path="/garbage.png")

    outcome = env.processing.process(env.ctx, media_item_version_id=version_id, spec=_OCR_SPEC)
    assert outcome.status == "failed"

    runs = env.processing.list_media_runs(env.ctx, source_media_item_version_id=version_id)
    failed = next(run for run in runs if run.status == "FAILED")
    assert failed.failure_kind == "validation"
    detail = env.processing.media_run_detail(env.ctx, media_processing_run_id=failed.media_processing_run_id)
    assert detail.failure_kind == "validation" and detail.media_derivative_id is None
