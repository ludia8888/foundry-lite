"""On-demand access-pattern preview cache (Media/Content Plane M9, doc §M9).

An access pattern (thumbnail/preview/waveform) is a *rebuildable* derived view of a committed
media version. The cache is an optimization, NEVER serving truth (invariant
``access_pattern_cache_is_not_truth``): every preview verifies the COMMITTED source first
(blob present + hash matches the DB row), serves a cache hit only when it is unexpired AND its
pinned ``source_content_hash`` still matches the source AND its blob is present, and otherwise
re-renders from the source. References and content retrieval read the source, never this cache.
A renderer with no bundled engine fails closed — never a fabricated/empty preview.
"""

from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timedelta

from foundry_lite.application.media_source_materialization import (
    MediaByteVerificationFailure,
    materialized_verified_media,
)
from foundry_lite.application.ports.media_access_cache_repository import MediaAccessCacheRecord
from foundry_lite.application.ports.media_preview_renderer import (
    PreviewRenderError,
    PreviewRenderRequest,
    RenderedPreview,
)
from foundry_lite.application.ports.media_repository import MediaItemVersionRecord
from foundry_lite.application.ports.media_storage import MediaReadGrant, MediaReadGrantRequest
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation, NotFound, ValidationFailed


class MediaAccessPatternService(CoreService):
    """Serve/recompute on-demand access-pattern previews (caches, never serving truth)."""

    required_dependencies = (
        "engine",
        "media_repository",
        "media_storage",
        "media_source_workspace",
        "media_access_cache_repository",
        "media_preview_renderer",
    )
    required_collaborators = ()

    def preview(
        self,
        ctx: RequestContext,
        *,
        media_item_version_id: str,
        access_pattern: str,
        spec: dict[str, object] | None = None,
        persistence_policy: str = "persist",
        ttl_seconds: int | None = None,
    ) -> MediaReadGrant:
        spec = spec or {}
        spec_hash = _spec_hash(access_pattern, spec)
        version = self._verified_source(ctx, media_item_version_id)
        with self.engine.begin() as conn:
            cached = self.media_access_cache_repository.access_cache_by_key(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                source_media_item_version_id=media_item_version_id,
                access_pattern=access_pattern,
                access_pattern_spec_hash=spec_hash,
            )
        fresh = self._serve_if_fresh(ctx, cached, version)
        if fresh is not None:
            return fresh
        return self._recompute(ctx, version, access_pattern, spec, spec_hash, persistence_policy, ttl_seconds)

    def purge_access_caches(self, ctx: RequestContext, *, media_item_version_id: str) -> list[str]:
        with self.engine.begin() as conn:
            return self.media_access_cache_repository.delete_access_caches_for_version(
                transaction=conn, tenant_id=ctx.tenant_id, source_media_item_version_id=media_item_version_id
            )

    def _verified_source(self, ctx: RequestContext, media_item_version_id: str) -> MediaItemVersionRecord:
        with self.engine.begin() as conn:
            version = self.media_repository.media_item_version_by_id(
                transaction=conn, tenant_id=ctx.tenant_id, media_item_version_id=media_item_version_id
            )
        if version is None or version.status != "COMMITTED":
            raise NotFound(
                "committed media version not found", details={"media_item_version_id": media_item_version_id}
            )
        stat = self.media_storage.stat(version.blob_key)
        if not stat.is_present or stat.content_hash != version.content_hash:
            raise InvariantViolation(
                "committed_media_version_storage_unverifiable",
                details={"media_item_version_id": media_item_version_id, "blob_key": version.blob_key},
            )
        return version

    def _serve_if_fresh(
        self, ctx: RequestContext, cached: MediaAccessCacheRecord | None, version: MediaItemVersionRecord
    ) -> MediaReadGrant | None:
        if cached is None or _is_expired(cached.expires_at) or cached.source_content_hash != version.content_hash:
            return None
        stat = self.media_storage.stat(cached.blob_key)
        if not stat.is_present or stat.content_hash != cached.content_hash:
            return None
        return self._grant(ctx, version.media_item_version_id, cached.blob_key)

    def _recompute(
        self,
        ctx: RequestContext,
        version: MediaItemVersionRecord,
        access_pattern: str,
        spec: dict[str, object],
        spec_hash: str,
        persistence_policy: str,
        ttl_seconds: int | None,
    ) -> MediaReadGrant:
        if not self.media_preview_renderer.supports(access_pattern):
            raise ValidationFailed("unsupported access pattern", details={"access_pattern": access_pattern})
        rendered = self._render(version, access_pattern, spec)
        blob_key = f"cache/{version.media_item_version_id}/{access_pattern}/{spec_hash}"
        self.media_storage.write_staged(blob_key, io.BytesIO(rendered.content))
        stat = self.media_storage.stat(blob_key)
        record = MediaAccessCacheRecord(
            access_cache_id=_new_id("mac"),
            tenant_id=ctx.tenant_id,
            source_media_item_version_id=version.media_item_version_id,
            access_pattern=access_pattern,
            access_pattern_spec_hash=spec_hash,
            persistence_policy=persistence_policy,
            source_content_hash=version.content_hash,
            blob_key=blob_key,
            content_hash=stat.content_hash,
            byte_size=stat.byte_size,
            mime_type=rendered.mime_type,
            created_at=_now(),
            expires_at=_expiry(ttl_seconds) if persistence_policy == "cache" else None,
        )
        with self.engine.begin() as conn:
            self.media_access_cache_repository.upsert_access_cache(transaction=conn, record=record)
        return self._grant(ctx, version.media_item_version_id, blob_key)

    def _render(self, version: MediaItemVersionRecord, access_pattern: str, spec: dict[str, object]) -> RenderedPreview:
        try:
            workspace = materialized_verified_media(
                self.media_source_workspace, self.media_storage, version, file_name="source"
            )
            with workspace as source_path:
                request = PreviewRenderRequest(access_pattern=access_pattern, source_path=source_path, spec=spec)
                return self._render_request(version, request)
        except MediaByteVerificationFailure as exc:
            raise InvariantViolation(
                "committed_media_version_storage_unverifiable",
                details={
                    "media_item_version_id": version.media_item_version_id,
                    "reason": exc.reason,
                },
            ) from exc

    def _render_request(self, version: MediaItemVersionRecord, request: PreviewRenderRequest) -> RenderedPreview:
        try:
            return self.media_preview_renderer.render(request)
        except PreviewRenderError as exc:
            raise InvariantViolation(
                "access_pattern_render_failed",
                details={"media_item_version_id": version.media_item_version_id, "reason": exc.reason},
            ) from exc

    def _grant(self, ctx: RequestContext, media_item_version_id: str, object_key: str) -> MediaReadGrant:
        return self.media_storage.issue_read_grant(
            MediaReadGrantRequest(
                tenant_id=ctx.tenant_id, media_item_version_id=media_item_version_id, object_key=object_key
            )
        )


def _spec_hash(access_pattern: str, spec: dict[str, object]) -> str:
    canonical = json.dumps({"accessPattern": access_pattern, "spec": spec}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_expired(expires_at: str | None) -> bool:
    if expires_at is None:
        return False
    return datetime.fromisoformat(expires_at) <= datetime.now().astimezone()


def _expiry(ttl_seconds: int | None) -> str | None:
    if ttl_seconds is None:
        return None
    return (datetime.now().astimezone() + timedelta(seconds=ttl_seconds)).isoformat()
