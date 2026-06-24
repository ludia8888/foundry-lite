"""Ontology-anchored unified search (L12b — Palantir Ontology-Augmented Generation / OAG).

One natural-language query returns ranked ONTOLOGY OBJECTS, fusing (a) the objects' own
structured/keyword/semantic matches with (b) matches in the unstructured media bound to those
objects. This mirrors Palantir OAG: "OAG retrieves the objects and the data that define them" —
the result unit is the OBJECT, not a bag of text. An unstructured content match is reached via
the media-reference link and LIFTED to its owning object (chunk-object -> owning object). The two
ranked lists are fused with reciprocal rank fusion (RRF), the documented OAG hybrid-retrieval
technique, so an object matched BOTH by its own properties AND by its bound media ranks higher.

Permission is anchored at the object: every surfaced object is re-read through the object query
ACL (object:read + masking), and a media hit only lifts to an object the caller may read — media
that no object references, or whose owning object is unreadable, never appears (no cross-tenant
leakage). The committed object/media version stays serving truth; the index is a projection.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from foundry_lite.application.ports import ObjectPayload, ObjectQueryItem, ObjectQueryResult
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.content_index import ContentSearchHit, HybridContentQuery
from foundry_lite.application.ports.media_reference_binding_repository import MediaReferenceBindingRecord
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, PermissionDenied, ValidationFailed

RRF_K = 60
UNIFIED_SEARCH_MAX_LIMIT = 100


@dataclass(frozen=True)
class MediaCitation:
    """Why a media match contributed to an object: the content unit + its source version."""

    content_unit_id: str
    source_media_item_version_id: str
    property_name: str
    index_generation: str


@dataclass(frozen=True)
class UnifiedSearchHit:
    """One ranked ontology object, with the media content (if any) that lifted it."""

    object_type: str
    object_id: str
    object_version: int
    properties: Mapping[str, object]
    score: float
    has_own_text_match: bool
    media_citations: tuple[MediaCitation, ...] = field(default=())


class UnifiedObjectSearch(Protocol):
    """Object-set producer + object-anchored ACL re-read (the L12a object search planner)."""

    def query_objects(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = ...,
        filter_ast: Mapping[str, object] | None = ...,
        order_by: Sequence[Mapping[str, str]] | None = ...,
        limit: int = ...,
        cursor: str | None = ...,
        search_text: str | None = ...,
        semantic_text: str | None = ...,
    ) -> ObjectQueryResult: ...

    def get_object(
        self,
        object_type_api_name: str,
        object_id: str,
        *,
        ctx: RequestContext | None = ...,
        include_explain: bool = ...,
    ) -> ObjectPayload: ...


class UnifiedContentSearch(Protocol):
    """Unstructured text content retrieval over media content units (bge / L4)."""

    def search_content(self, ctx: RequestContext, *, query: HybridContentQuery) -> list[ContentSearchHit]: ...


class UnifiedVisualSearch(Protocol):
    """Open-vocabulary visual content retrieval over scene frames (CLIP / L11)."""

    def search_visual(self, ctx: RequestContext, *, text: str, top_k: int = ...) -> list[ContentSearchHit]: ...


@dataclass(frozen=True)
class _LiftedObject:
    """An object reached through a media-reference edge, plus the media citation that reached it."""

    object_id: str
    payload: ObjectPayload
    citation: MediaCitation


class OntologySearchService(CoreService):
    """Fuse object search + media-content search into ONE ranked object set (OAG, object-anchored)."""

    required_dependencies = ("policy", "engine", "media_reference_binding_repository")
    required_collaborators = ("object_query_service", "content_retrieval_service", "media_visual_search_service")
    object_query_service: UnifiedObjectSearch
    content_retrieval_service: UnifiedContentSearch
    media_visual_search_service: UnifiedVisualSearch

    def unified_search(
        self,
        ctx: RequestContext,
        *,
        query_text: str,
        object_type: str,
        filters: Mapping[str, object] | None = None,
        limit: int = 20,
    ) -> list[UnifiedSearchHit]:
        """Return ontology objects ranked by RRF over their own matches and bound-media matches."""
        self.policy.require(ctx, "object:read")
        query_limit = _unified_limit(limit)
        object_items = self._object_hits(ctx, query_text, object_type, filters, query_limit)
        media_hits = self._media_hits(ctx, query_text, query_limit)
        lifted = self._lift_media_hits(ctx, object_type, media_hits)
        fused = _fuse(object_items, lifted)
        return fused[:query_limit]

    def _object_hits(
        self,
        ctx: RequestContext,
        query_text: str,
        object_type: str,
        filters: Mapping[str, object] | None,
        limit: int,
    ) -> list[ObjectQueryItem]:
        """Rank the object set by the objects' OWN structured/keyword/semantic matches (L12a)."""
        result = self.object_query_service.query_objects(
            object_type, ctx=ctx, filter_ast=filters, semantic_text=query_text, limit=limit
        )
        return result["items"]

    def _media_hits(self, ctx: RequestContext, query_text: str, limit: int) -> list[ContentSearchHit]:
        """Text (bge) + visual (CLIP) content hits, each carrying its source media version."""
        text_hits = self.content_retrieval_service.search_content(
            ctx, query=HybridContentQuery(tenant_id=ctx.tenant_id, text=query_text, top_k=limit)
        )
        return _dedupe_hits([*text_hits, *self._visual_hits(ctx, query_text, limit)])

    def _visual_hits(self, ctx: RequestContext, query_text: str, limit: int) -> list[ContentSearchHit]:
        """Best-effort CLIP visual pass; it augments but never gates the text-anchored result.

        The visual generation is a SEPARATE CLIP-pinned space; when no CLIP generation is active
        the content index fails closed (model mismatch) — a correct guard that here means simply
        "no visual augmentation", so it is absorbed rather than aborting the whole object query.
        """
        try:
            return self.media_visual_search_service.search_visual(ctx, text=query_text, top_k=limit)
        except AdapterError:
            return []

    def _lift_media_hits(
        self, ctx: RequestContext, object_type: str, media_hits: Sequence[ContentSearchHit]
    ) -> list[_LiftedObject]:
        """Lift each content hit to the owning object(s) via the media-reference edge, ACL-anchored."""
        if not media_hits:
            return []
        bindings = self._bindings_for_hits(ctx, media_hits)
        hit_by_version = {hit.source_media_item_version_id: hit for hit in media_hits}
        lifted: list[_LiftedObject] = []
        for hit in media_hits:
            lifted.extend(self._lift_one(ctx, object_type, hit, bindings, hit_by_version))
        return lifted

    def _lift_one(
        self,
        ctx: RequestContext,
        object_type: str,
        hit: ContentSearchHit,
        bindings: Sequence[MediaReferenceBindingRecord],
        hit_by_version: Mapping[str, ContentSearchHit],
    ) -> list[_LiftedObject]:
        """Lift one content hit to every readable object of this type that references its version."""
        lifted: list[_LiftedObject] = []
        for binding in bindings:
            if binding.media_item_version_id != hit.source_media_item_version_id or binding.holder_type != object_type:
                continue  # the content unit is not bound to this object type's property
            payload = self._readable_object(ctx, object_type, binding.holder_id)
            if payload is None:
                continue  # ACL: only lift to an object the caller may read (no leakage)
            lifted.append(_LiftedObject(object_id=binding.holder_id, payload=payload, citation=_citation(hit, binding)))
        return lifted

    def _bindings_for_hits(
        self, ctx: RequestContext, media_hits: Sequence[ContentSearchHit]
    ) -> list[MediaReferenceBindingRecord]:
        version_ids = sorted({hit.source_media_item_version_id for hit in media_hits})
        with self.engine.begin() as conn:
            return self.media_reference_binding_repository.bindings_for_media_versions(
                transaction=conn, tenant_id=ctx.tenant_id, media_item_version_ids=version_ids
            )

    def _readable_object(self, ctx: RequestContext, object_type: str, object_id: str) -> ObjectPayload | None:
        try:
            payload = self.object_query_service.get_object(object_type, object_id, ctx=ctx)
        except (NotFound, PermissionDenied):
            return None
        return None if payload.get("deleted") else payload


def _unified_limit(limit: int) -> int:
    if limit < 1:
        raise ValidationFailed("unified search limit must be positive", details={"limit": limit})
    if limit > UNIFIED_SEARCH_MAX_LIMIT:
        raise ValidationFailed(
            "unified search limit exceeds maximum",
            details={"limit": limit, "max_limit": UNIFIED_SEARCH_MAX_LIMIT},
        )
    return limit


def _dedupe_hits(hits: Sequence[ContentSearchHit]) -> list[ContentSearchHit]:
    """Keep first occurrence per content unit so a unit found by both towers counts once."""
    seen: set[str] = set()
    deduped: list[ContentSearchHit] = []
    for hit in hits:
        if hit.content_unit_id in seen:
            continue
        seen.add(hit.content_unit_id)
        deduped.append(hit)
    return deduped


def _citation(hit: ContentSearchHit, binding: MediaReferenceBindingRecord) -> MediaCitation:
    return MediaCitation(
        content_unit_id=hit.content_unit_id,
        source_media_item_version_id=hit.source_media_item_version_id,
        property_name=binding.property_name,
        index_generation=hit.index_generation,
    )


def _fuse(object_items: Sequence[ObjectQueryItem], lifted: Sequence[_LiftedObject]) -> list[UnifiedSearchHit]:
    """Reciprocal-rank-fuse the object-search rank and the media-derived rank into one object set."""
    by_id: dict[str, _Accumulator] = {}
    for rank, item in enumerate(object_items):
        acc = by_id.setdefault(item["objectId"], _Accumulator())
        acc.seed_from_item(item)
        acc.score += _rrf(rank)
        acc.has_own_text_match = True
    for rank, object_id in enumerate(_object_order(lifted)):
        acc = by_id.setdefault(object_id, _Accumulator())
        acc.score += _rrf(rank)
    for lift in lifted:
        acc = by_id[lift.object_id]
        acc.seed_from_payload(lift.payload)
        acc.add_citation(lift.citation)
    return sorted((acc.to_hit() for acc in by_id.values()), key=lambda hit: (-hit.score, hit.object_id))


def _rrf(rank: int) -> float:
    return 1.0 / (RRF_K + rank + 1)


def _object_order(lifted: Sequence[_LiftedObject]) -> list[str]:
    """First-seen order of distinct objects in the media-derived list = their media rank."""
    order: list[str] = []
    seen: set[str] = set()
    for lift in lifted:
        if lift.object_id in seen:
            continue
        seen.add(lift.object_id)
        order.append(lift.object_id)
    return order


@dataclass
class _Accumulator:
    """Mutable fold target for RRF; converted to an immutable hit at the end."""

    object_type: str = ""
    object_id: str = ""
    object_version: int = 0
    properties: Mapping[str, object] = field(default_factory=dict)
    score: float = 0.0
    has_own_text_match: bool = False
    citations: list[MediaCitation] = field(default_factory=list)

    def seed_from_item(self, item: ObjectQueryItem) -> None:
        self._seed(item["objectType"], item["objectId"], item["objectVersion"], item["properties"])

    def seed_from_payload(self, payload: ObjectPayload) -> None:
        self._seed(payload["objectType"], payload["objectId"], payload["objectVersion"], payload["properties"])

    def _seed(self, object_type: str, object_id: str, object_version: int, properties: Mapping[str, object]) -> None:
        self.object_type = object_type
        self.object_id = object_id
        self.object_version = object_version
        self.properties = properties

    def add_citation(self, citation: MediaCitation) -> None:
        self.citations.append(citation)

    def to_hit(self) -> UnifiedSearchHit:
        return UnifiedSearchHit(
            object_type=self.object_type,
            object_id=self.object_id,
            object_version=self.object_version,
            properties=self.properties,
            score=self.score,
            has_own_text_match=self.has_own_text_match,
            media_citations=tuple(self.citations),
        )
