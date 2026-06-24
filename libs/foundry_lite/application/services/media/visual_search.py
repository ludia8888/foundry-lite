from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.ports.content_index import (
    ContentIndexBatch,
    ContentIndexSchema,
    ContentSearchHit,
    HybridContentQuery,
    IndexedContentUnit,
)
from foundry_lite.application.ports.media_derivative_repository import ContentUnitRecord
from foundry_lite.application.services.base import CoreService
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

    def search_visual(self, ctx: RequestContext, *, text: str, top_k: int = 10) -> list[ContentSearchHit]:
        # Embed the query with the CLIP TEXT tower; match only the dense path (text=None) so a
        # frame's descriptor never lexically biases the visual ranking.
        query_vector = self.vision_embedding_model_adapter.embed_query(text)
        query = HybridContentQuery(
            tenant_id=ctx.tenant_id,
            text=None,
            top_k=top_k,
            query_vector=query_vector,
            embedding_model_version=self.vision_embedding_model_adapter.model_version,
        )
        hits = self.content_index_adapter.search(query)
        if not hits:
            return []
        with self.engine.begin() as conn:
            units = self.media_derivative_repository.get_content_units_by_ids(
                transaction=conn, ids=[hit.content_unit_id for hit in hits]
            )
        unit_by_id = {unit.content_unit_id: unit for unit in units}
        return [hit for hit in hits if _is_authoritative(hit, unit_by_id.get(hit.content_unit_id), ctx.tenant_id)]

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
    )


def _is_authoritative(hit: ContentSearchHit, unit: ContentUnitRecord | None, tenant_id: str) -> bool:
    if unit is None:
        return False  # stale: the source content unit no longer exists
    if unit.tenant_id != tenant_id or str(unit.security_envelope.get("tenantId", unit.tenant_id)) != tenant_id:
        return False  # ACL: never leak another tenant's content
    return hit.text_hash == unit.text_hash  # citation integrity: index must match the truth


__all__ = ["MediaVisualSearchService", "VisualIndexingOutcome"]
