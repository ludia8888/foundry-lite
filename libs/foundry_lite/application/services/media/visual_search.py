"""Application service helpers for visual search workflows."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.ports.content_index import (
    ContentIndexBatch,
    ContentIndexSchema,
    ContentSearchHit,
    HybridContentQuery,
    IndexedContentUnit,
    is_classification_cleared,
)
from foundry_lite.application.ports.media_derivative_repository import ContentUnitRecord
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.media.clearance import allowed_media_classifications
from foundry_lite.application.services.media.protocols import MediaRuntimeBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound

_PROJECTION_VERSION = 1


@dataclass(frozen=True)
class VisualIndexingOutcome:
    """Result of projecting visual frame units into one CLIP-pinned index generation."""

    generation: str
    indexed: int
    failed: int


class MediaVisualSearchService(CoreService):
    """Open-vocabulary VISUAL search over video scene frames (Media/Content Plane L11).

    L11 mirrors Foundry's documented embedding/semantic-search vision mode: video ->
    ``extract_scene_frames`` -> per-frame ``imageToEmbeddingsV1`` (a Vector property) ->
    ``nearestNeighbors(o => o.embeddings.near(v)).orderByRelevance()`` with the index-time and
    query-time embedding model PINNED to the same CLIP model. A ``video_scene_vision`` derivative
    already committed each frame's CLIP IMAGE embedding onto its ``video_frame_visual`` content
    unit (truth); this service projects those vectors AS-IS into a CLIP-pinned generation (the
    vector is the searchable content — no re-embedding from text) and answers a natural-language
    query by embedding it with the CLIP TEXT tower. Because the generation is pinned to the CLIP
    model version, the content index's model-version guard fails closed against a bge query (and
    vice-versa): the two vector spaces never mix. The bge text/hybrid path is untouched.
    """

    required_dependencies = (
        "engine",
        "policy",
        "media_derivative_repository",
        "content_index_adapter",
        "vision_embedding_model_adapter",
    )
    required_collaborators = ("runtime_service",)
    runtime_service: MediaRuntimeBoundary

    def index_visual_derivative(
        self, ctx: RequestContext, *, media_derivative_id: str, generation: str
    ) -> VisualIndexingOutcome:
        with self.engine.begin() as conn:
            derivative = self.media_derivative_repository.derivative_by_id(
                transaction=conn, tenant_id=ctx.tenant_id, media_derivative_id=media_derivative_id
            )
            if derivative is None:
                raise NotFound("media derivative not found", details={"media_derivative_id": media_derivative_id})
            if derivative.status != "COMMITTED":
                raise ConflictDetected(
                    "only a COMMITTED derivative can be indexed",
                    details={"media_derivative_id": media_derivative_id, "status": derivative.status},
                )
            units = self.media_derivative_repository.get_content_units(
                transaction=conn, tenant_id=ctx.tenant_id, derivative_id=media_derivative_id
            )
        outcome = self._project(generation, units)
        self._audit_indexed(ctx, generation, media_derivative_id, outcome)
        return outcome

    def promote(self, ctx: RequestContext, *, expected_active: str, generation: str) -> None:
        self.content_index_adapter.promote_generation(expected_active, generation)
        with self.engine.begin() as conn:
            self.runtime_service._audit(
                conn,
                ctx,
                event_type="media.visual_index.promoted",
                resource_type="content_index_generation",
                resource_id=generation,
                action="update",
                after_ref={"expectedActive": expected_active, "generation": generation},
                correlation_id=ctx.request_id,
            )

    def search_visual(
        self,
        ctx: RequestContext,
        *,
        text: str,
        top_k: int = 10,
        allowed_classifications: tuple[str, ...] | None = None,
    ) -> list[ContentSearchHit]:
        self.policy.require(ctx, "media:search")
        # The classification clearance is a SERVER decision from ctx (mirrors the text path's
        # PRE-filter). A caller value is only an internal narrowing hint; it can never widen the
        # ctx-derived ceiling, so an over-classified frame never enters ranking or is re-read out.
        allowed = _clearance(ctx, allowed_classifications)
        if not self.vision_embedding_model_adapter.is_available:
            return []
        hits = self.content_index_adapter.search(self._visual_query(ctx, text, top_k, allowed))
        if not hits:
            return []
        unit_by_id = self._content_units_by_id(ctx, hits)
        return _authoritative_hits(hits, unit_by_id, ctx.tenant_id, allowed)

    def _visual_query(
        self,
        ctx: RequestContext,
        text: str,
        top_k: int,
        allowed_classifications: tuple[str, ...] | None,
    ) -> HybridContentQuery:
        """Build a CLIP-text query without lexical score contamination."""
        query_vector = self.vision_embedding_model_adapter.embed_query(text)
        return HybridContentQuery(
            tenant_id=ctx.tenant_id,
            text=None,
            top_k=top_k,
            query_vector=query_vector,
            embedding_model_version=self.vision_embedding_model_adapter.model_version,
            allowed_classifications=allowed_classifications,
        )

    def _content_units_by_id(
        self,
        ctx: RequestContext,
        hits: list[ContentSearchHit],
    ) -> dict[str, ContentUnitRecord]:
        with self.engine.begin() as conn:
            units = self.media_derivative_repository.get_content_units_by_ids(
                transaction=conn, ids=[hit.content_unit_id for hit in hits]
            )
        return {unit.content_unit_id: unit for unit in units}

    def _project(self, generation: str, units: list[ContentUnitRecord]) -> VisualIndexingOutcome:
        model_version = self.vision_embedding_model_adapter.model_version
        self.content_index_adapter.configure_generation(
            ContentIndexSchema(generation=generation, embedding_model_version=model_version)
        )
        batch = ContentIndexBatch(generation=generation, units=tuple(_indexed(unit, model_version) for unit in units))
        result = self.content_index_adapter.upsert_units(batch)
        return VisualIndexingOutcome(generation=generation, indexed=result.indexed, failed=result.failed)

    def _audit_indexed(
        self, ctx: RequestContext, generation: str, resource_id: str, outcome: VisualIndexingOutcome
    ) -> None:
        with self.engine.begin() as conn:
            self.runtime_service._audit(
                conn,
                ctx,
                event_type="media.visual_index.upserted",
                resource_type="content_index_generation",
                resource_id=resource_id,
                action="update",
                after_ref={"generation": generation, "indexed": outcome.indexed, "failed": outcome.failed},
                correlation_id=ctx.request_id,
            )


def _clearance(ctx: RequestContext, requested: tuple[str, ...] | None) -> tuple[str, ...] | None:
    """Intersect any caller-supplied narrowing hint with the ctx-derived clearance ceiling."""
    ceiling = allowed_media_classifications(ctx)
    if requested is None:
        return ceiling
    if ceiling is None:
        return requested
    return tuple(name for name in requested if name in ceiling)


def _indexed(unit: ContentUnitRecord, embedding_model_version: str) -> IndexedContentUnit:
    return IndexedContentUnit(
        tenant_id=unit.tenant_id,
        content_unit_id=unit.content_unit_id,
        source_media_item_version_id=unit.source_media_item_version_id,
        text=unit.text,
        text_hash=unit.text_hash,
        version=_PROJECTION_VERSION,
        page_number=unit.page_number,
        start_ms=unit.start_ms,
        end_ms=unit.end_ms,
        chunk_spec_hash=unit.chunk_spec_hash,
        embedding=unit.embedding,
        embedding_model_version=embedding_model_version,
        # Pin the frame's classification onto the projection (like the text path) so the index
        # can PRE-filter by clearance before the kNN ranks — an over-classified frame is never a
        # candidate for an uncleared caller.
        classification=str(unit.security_envelope.get("classification", "")),
    )


def _authoritative_hit(
    hit: ContentSearchHit,
    unit: ContentUnitRecord | None,
    tenant_id: str,
    allowed_classifications: tuple[str, ...] | None,
) -> ContentSearchHit | None:
    if unit is None:
        return None  # stale: the source content unit no longer exists
    if unit.tenant_id != tenant_id or str(unit.security_envelope.get("tenantId", unit.tenant_id)) != tenant_id:
        return None  # ACL: never leak another tenant's content
    # Defense-in-depth: re-apply the clearance gate against the authoritative DB row so even a
    # stale/over-broad index that leaked an over-classified frame is dropped here.
    classification = str(unit.security_envelope.get("classification", ""))
    if not is_classification_cleared(classification, allowed_classifications):
        return None
    if hit.text_hash != unit.text_hash:
        return None
    return _content_search_hit(hit, unit, classification)


def _authoritative_hits(
    hits: list[ContentSearchHit],
    unit_by_id: dict[str, ContentUnitRecord],
    tenant_id: str,
    allowed_classifications: tuple[str, ...] | None,
) -> list[ContentSearchHit]:
    return [
        authoritative
        for hit in hits
        if (
            authoritative := _authoritative_hit(
                hit,
                unit_by_id.get(hit.content_unit_id),
                tenant_id,
                allowed_classifications,
            )
        )
        is not None
    ]


def _content_search_hit(
    hit: ContentSearchHit,
    unit: ContentUnitRecord,
    classification: str,
) -> ContentSearchHit:
    return ContentSearchHit(
        source_media_item_version_id=unit.source_media_item_version_id,
        content_unit_id=unit.content_unit_id,
        index_generation=hit.index_generation,
        media_derivative_id=unit.derivative_id,
        page_number=unit.page_number,
        start_ms=unit.start_ms,
        end_ms=unit.end_ms,
        bbox=dict(unit.bbox) if unit.bbox is not None else None,
        timecode=_timecode(unit),
        source_locator=dict(unit.source_locator) if unit.source_locator is not None else None,
        text_hash=unit.text_hash,
        text=unit.text,
        chunk_spec_hash=unit.chunk_spec_hash,
        classification=classification,
    )


def _timecode(unit: ContentUnitRecord) -> dict[str, object] | None:
    if unit.start_ms is None and unit.end_ms is None:
        return None
    return {"startMs": unit.start_ms, "endMs": unit.end_ms}


__all__ = ["MediaVisualSearchService", "VisualIndexingOutcome"]
