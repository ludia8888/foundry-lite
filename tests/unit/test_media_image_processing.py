"""M6 image processing through MediaProcessingService.

A thumbnail derivative commits atomically with an inherited security envelope and is
idempotent (real Pillow over a real PNG materialized from local storage). An oversized image
(M-T3-001 resource cap) and a hung decode (M-T3-002 fail-closed timeout) each record FAILED
durable evidence and commit NO derivative — nothing is ever partially visible.
"""

from __future__ import annotations

import io
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest
from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.media_processor import ProcessorSpec
from foundry_lite.application.services.media.catalog import MediaCatalogService, MediaSetSpec
from foundry_lite.application.services.media.processing import MediaProcessingService
from foundry_lite.application.services.media.transactions import MediaTransactionService
from foundry_lite.application.services.media.uploads import MediaUploadInput, MediaUploadService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.image_processor import ImageDescription, ImageProcessorAdapter
from foundry_lite.infrastructure.adapters.local_media_storage import LocalMediaStorageAdapter
from foundry_lite.infrastructure.repositories import SqlAlchemyMediaDerivativeRepository, SqlAlchemyMediaRepository
from foundry_lite.security.policy import PolicyService
from PIL import Image

_IMAGE_SPEC = ProcessorSpec(processor="image_v1", processor_version="1.0")


class _FakeRuntime:
    def _audit(self, conn: TransactionContext, ctx: RequestContext, *, event_type: str, **_: object) -> None: ...

    def _outbox(
        self, conn: TransactionContext, ctx: RequestContext, event_type: str, *_: object, **__: object
    ) -> str | None:
        return event_type


def _png_bytes(size: tuple[int, int] = (320, 200)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color="blue").save(buffer, format="PNG")
    return buffer.getvalue()


@dataclass(frozen=True)
class _Env:
    ctx: RequestContext
    engine: object
    repo: SqlAlchemyMediaRepository
    deriv: SqlAlchemyMediaDerivativeRepository
    storage: LocalMediaStorageAdapter
    version_id: str


def _processing(env: _Env, processor: object) -> MediaProcessingService:
    svc = MediaProcessingService(
        engine=env.engine,
        policy=PolicyService(),
        media_repository=env.repo,
        media_derivative_repository=env.deriv,
        media_storage=env.storage,
        media_processor=processor,
    )
    svc.bind_collaborators({"runtime_service": _FakeRuntime()})
    return svc


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{tmp_path / 'image.db'}", future=True)
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
            namespace="brand",
            name="photos",
            schema_type="image",
            primary_format="png",
            allowed_input_formats=("png",),
            classification="confidential",
        ),
    )
    tx = transaction.open(ctx, media_set_id=media_set.media_set_id, idempotency_key="idem-1")
    session = upload.initiate(
        ctx, media_set_id=media_set.media_set_id, logical_path="/a.png", supplied_mime_type="image/png"
    )
    staged = upload.complete(
        ctx,
        inputs=MediaUploadInput(
            media_set_id=media_set.media_set_id,
            media_transaction_id=tx,
            logical_path="/a.png",
            supplied_mime_type="image/png",
            schema_type="image",
            format="png",
            security_envelope={"tenantId": ctx.tenant_id, "classification": "confidential"},
        ),
        upload=session,
        source=io.BytesIO(_png_bytes()),
    )
    transaction.commit(ctx, media_transaction_id=tx)
    return _Env(
        ctx=ctx, engine=engine, repo=repo, deriv=deriv, storage=storage, version_id=staged.media_item_version_id
    )


def test_thumbnail_derivative_commits_with_inherited_envelope(env: _Env) -> None:
    processing = _processing(env, ImageProcessorAdapter())  # real Pillow
    outcome = processing.process(env.ctx, media_item_version_id=env.version_id, spec=_IMAGE_SPEC)
    derivative = processing.resolve_derivative(env.ctx, media_derivative_id=outcome.media_derivative_id)
    assert outcome.status == "committed"
    assert derivative.derivative_kind == "thumbnail"
    assert derivative.security_envelope.get("classification") == "confidential"

    replay = processing.process(env.ctx, media_item_version_id=env.version_id, spec=_IMAGE_SPEC)
    assert replay.is_duplicate is True and replay.media_derivative_id == outcome.media_derivative_id


def test_oversized_image_records_failed_evidence_and_commits_nothing(env: _Env) -> None:
    processing = _processing(env, ImageProcessorAdapter(max_pixels=10))  # M-T3-001 resource cap
    outcome = processing.process(env.ctx, media_item_version_id=env.version_id, spec=_IMAGE_SPEC)
    assert outcome.status == "failed"
    assert outcome.error is not None and outcome.error.get("kind") == "validation"
    with pytest.raises(NotFound):
        processing.resolve_derivative(env.ctx, media_derivative_id=outcome.media_derivative_id)


def test_timeout_records_failed_evidence_and_commits_nothing(env: _Env) -> None:
    release = threading.Event()

    def _blocking(_path: str, _pixels: int, _dim: int) -> ImageDescription:
        release.wait(timeout=10)
        return ImageDescription("PNG", "RGB", 1, 1, 1, 1, "never")

    processing = _processing(env, ImageProcessorAdapter(timeout_seconds=0, image_describer=_blocking))
    try:
        outcome = processing.process(env.ctx, media_item_version_id=env.version_id, spec=_IMAGE_SPEC)
        assert outcome.status == "failed"
        assert outcome.error is not None and outcome.error.get("kind") == "timeout"
        with pytest.raises(NotFound):
            processing.resolve_derivative(env.ctx, media_derivative_id=outcome.media_derivative_id)
    finally:
        release.set()
