"""M9 on-demand access-pattern preview cache (invariant ``access_pattern_cache_is_not_truth``).

A preview cache is a rebuildable optimization, never serving truth: a fresh hit serves without
recomputing; a stale source (its content hash drifted) or an expired entry re-renders from the
COMMITTED source; an unrenderable source fails closed (never a fabricated preview); and
reference resolution reads the committed source, never the cache.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.application.ports.media_access_cache_repository import MediaAccessCacheRecord
from foundry_lite.application.ports.media_preview_renderer import (
    PreviewRenderError,
    PreviewRenderRequest,
    RenderedPreview,
)
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.media.access_patterns import MediaAccessPatternService
from foundry_lite.application.services.media.catalog import MediaCatalogService, MediaSetSpec
from foundry_lite.application.services.media.references import MediaReferenceService
from foundry_lite.application.services.media.transactions import MediaTransactionService
from foundry_lite.application.services.media.uploads import MediaUploadInput, MediaUploadService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation, NotFound
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.local_media_source_workspace import LocalMediaSourceWorkspace
from foundry_lite.infrastructure.adapters.local_media_storage import LocalMediaStorageAdapter
from foundry_lite.infrastructure.adapters.local_preview_renderer import LocalPreviewRendererAdapter
from foundry_lite.infrastructure.repositories import (
    SqlAlchemyMediaAccessCacheRepository,
    SqlAlchemyMediaReferenceBindingRepository,
    SqlAlchemyMediaRepository,
)
from foundry_lite.security.policy import PolicyService
from PIL import Image

_THUMBNAIL = "thumbnail"


class _FakeRuntime:
    def _audit(self, *_: object, **__: object) -> None: ...

    def _outbox(self, *_: object, **__: object) -> str | None:
        return "evt"


class _RecordingRenderer:
    """Renders fixed bytes and counts calls so a cache hit can be proven not to recompute."""

    profile_name = "fake-preview"

    def __init__(self, *, content: bytes = b"PREVIEW", supported: bool = True) -> None:
        self.calls = 0
        self._content = content
        self._supported = supported

    def supports(self, access_pattern: str) -> bool:
        return self._supported and access_pattern == _THUMBNAIL

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=())

    def render(self, request: PreviewRenderRequest) -> RenderedPreview:
        self.calls += 1
        return RenderedPreview(access_pattern=request.access_pattern, content=self._content, mime_type="image/png")


class _UnavailableRenderer(_RecordingRenderer):
    def render(self, request: PreviewRenderRequest) -> RenderedPreview:
        raise PreviewRenderError("preview_renderer_unavailable")


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), color="blue").save(buffer, format="PNG")
    return buffer.getvalue()


@dataclass(frozen=True)
class _Env:
    ctx: RequestContext
    engine: object
    repo: SqlAlchemyMediaRepository
    cache_repo: SqlAlchemyMediaAccessCacheRepository
    storage: LocalMediaStorageAdapter
    version_id: str
    source_hash: str


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{tmp_path / 'm9.db'}", future=True)
    db.create_database(engine)
    repo = SqlAlchemyMediaRepository(engine)
    storage = LocalMediaStorageAdapter(tmp_path / "media")
    runtime = _FakeRuntime()
    catalog = MediaCatalogService(engine=engine, media_repository=repo)
    catalog.bind_collaborators({"runtime_service": runtime})
    upload = MediaUploadService(engine=engine, media_repository=repo, media_storage=storage)
    transaction = MediaTransactionService(engine=engine, media_repository=repo, media_storage=storage)
    transaction.bind_collaborators({"runtime_service": runtime})
    ctx = RequestContext(roles=("admin",))
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
    with engine.begin() as conn:
        version = repo.get_media_item_versions(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ids=[staged.media_item_version_id],
        )[0]
    return _Env(
        ctx=ctx,
        engine=engine,
        repo=repo,
        cache_repo=SqlAlchemyMediaAccessCacheRepository(engine),
        storage=storage,
        version_id=staged.media_item_version_id,
        source_hash=version.content_hash,
    )


def _service(env: _Env, renderer: object) -> MediaAccessPatternService:
    return MediaAccessPatternService(
        engine=env.engine,
        media_repository=env.repo,
        media_storage=env.storage,
        media_source_workspace=LocalMediaSourceWorkspace(),
        media_access_cache_repository=env.cache_repo,
        media_preview_renderer=renderer,
    )


def test_access_pattern_cache_hit_does_not_recompute(env: _Env) -> None:
    renderer = _RecordingRenderer()
    service = _service(env, renderer)
    first = service.preview(env.ctx, media_item_version_id=env.version_id, access_pattern=_THUMBNAIL)
    second = service.preview(env.ctx, media_item_version_id=env.version_id, access_pattern=_THUMBNAIL)
    assert first.object_key == second.object_key
    assert renderer.calls == 1  # the second call served the cache, it did not re-render


def test_access_pattern_cache_recomputes_on_stale_source(env: _Env) -> None:
    renderer = _RecordingRenderer()
    service = _service(env, renderer)
    with env.engine.begin() as conn:  # type: ignore[attr-defined]
        env.cache_repo.upsert_access_cache(
            transaction=conn,
            record=MediaAccessCacheRecord(
                access_cache_id=_new_id("mac"),
                tenant_id=env.ctx.tenant_id,
                source_media_item_version_id=env.version_id,
                access_pattern=_THUMBNAIL,
                access_pattern_spec_hash=_spec_hash(),
                persistence_policy="persist",
                source_content_hash="stale-does-not-match",
                blob_key="cache/stale",
                content_hash="x",
                byte_size=1,
                mime_type="image/png",
                created_at=_now(),
            ),
        )
    service.preview(env.ctx, media_item_version_id=env.version_id, access_pattern=_THUMBNAIL)
    assert renderer.calls == 1  # stale cache (source hash drifted) forced a re-render


def test_access_pattern_cache_recomputes_on_expired_entry(env: _Env) -> None:
    renderer = _RecordingRenderer()
    service = _service(env, renderer)
    with env.engine.begin() as conn:  # type: ignore[attr-defined]
        env.cache_repo.upsert_access_cache(
            transaction=conn,
            record=MediaAccessCacheRecord(
                access_cache_id=_new_id("mac"),
                tenant_id=env.ctx.tenant_id,
                source_media_item_version_id=env.version_id,
                access_pattern=_THUMBNAIL,
                access_pattern_spec_hash=_spec_hash(),
                persistence_policy="cache",
                source_content_hash=env.source_hash,
                blob_key="cache/expired",
                content_hash="x",
                byte_size=1,
                mime_type="image/png",
                created_at="2000-01-01T00:00:00+00:00",
                expires_at="2000-01-01T00:00:00+00:00",
            ),
        )
    service.preview(
        env.ctx,
        media_item_version_id=env.version_id,
        access_pattern=_THUMBNAIL,
        persistence_policy="cache",
        ttl_seconds=60,
    )
    assert renderer.calls == 1  # expired entry re-rendered; a fresh cache row replaced it


def test_access_pattern_renderer_unavailable_fails_closed(env: _Env) -> None:
    service = _service(env, _UnavailableRenderer())
    with pytest.raises(InvariantViolation):
        service.preview(env.ctx, media_item_version_id=env.version_id, access_pattern=_THUMBNAIL)


def test_access_pattern_rejects_bytes_changed_after_storage_stat(
    env: _Env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renderer = _RecordingRenderer()
    monkeypatch.setattr(
        env.storage,
        "open_stream",
        lambda _blob_key: io.BytesIO(b"tampered"),
    )

    with pytest.raises(InvariantViolation) as captured:
        _service(env, renderer).preview(
            env.ctx,
            media_item_version_id=env.version_id,
            access_pattern=_THUMBNAIL,
        )

    assert captured.value.details["reason"] == "stream_size_mismatch"
    assert renderer.calls == 0


class _NoHolderObjectQuery:
    """Attachment holder lookup that never resolves.

    This fixture stores normal media, never an ``attachment``-kind parameter, so
    ``has_visible_attachment_holder`` short-circuits before consulting this. Failing the
    lookup keeps that assumption honest instead of silently granting access.
    """

    def get_object(
        self,
        object_type_api_name: str,
        object_id: str,
        *,
        ctx: RequestContext | None = None,
        include_explain: bool = False,
    ) -> object:
        raise NotFound(
            "no attachment holder in this fixture",
            details={"objectType": object_type_api_name, "objectId": object_id},
        )


def test_reference_resolve_never_reads_access_cache(env: _Env) -> None:
    # Populate a cache, then poison it; resolving the reference must still return the committed
    # source bytes' hash, proving reference resolution reads the source, never the cache.
    service = _service(env, _RecordingRenderer(content=b"DIFFERENT-PREVIEW-BYTES"))
    service.preview(env.ctx, media_item_version_id=env.version_id, access_pattern=_THUMBNAIL)
    reference = MediaReferenceService(
        engine=env.engine,
        policy=PolicyService(),
        media_repository=env.repo,
        media_reference_binding_repository=SqlAlchemyMediaReferenceBindingRepository(env.engine),
        media_storage=env.storage,
    )
    reference.bind_collaborators({"object_query_service": _NoHolderObjectQuery()})
    resolved = reference.resolve(env.ctx, media_item_version_id=env.version_id)
    assert resolved.version.content_hash == env.source_hash
    assert resolved.reference.content_hash == env.source_hash


def test_purge_access_caches_evicts_and_forces_recompute(env: _Env) -> None:
    renderer = _RecordingRenderer()
    service = _service(env, renderer)
    service.preview(env.ctx, media_item_version_id=env.version_id, access_pattern=_THUMBNAIL)
    purged = service.purge_access_caches(env.ctx, media_item_version_id=env.version_id)
    assert len(purged) == 1
    service.preview(env.ctx, media_item_version_id=env.version_id, access_pattern=_THUMBNAIL)
    assert renderer.calls == 2  # the cache was evicted, so the second preview re-rendered


def test_real_pillow_renderer_produces_png_thumbnail(env: _Env) -> None:
    service = _service(env, LocalPreviewRendererAdapter())
    grant = service.preview(env.ctx, media_item_version_id=env.version_id, access_pattern=_THUMBNAIL)
    with env.storage.open_stream(grant.object_key) as stream:
        head = stream.read(8)
    assert head.startswith(b"\x89PNG\r\n\x1a\n")  # the cached preview is a real PNG thumbnail


def _spec_hash() -> str:
    from foundry_lite.application.services.media.access_patterns import _spec_hash as compute

    return compute(_THUMBNAIL, {})
