"""L12b ontology-anchored unified search (OAG) invariants.

The result unit is the ONTOLOGY OBJECT. These tests prove: object hits + media hits lifted
through the media-reference edge fuse via RRF into one ranked object set; an object matched by
BOTH its own text and its bound media outranks one matched by only one; an unbound media hit
never appears; a media hit whose owning object the caller cannot read is excluded (ACL anchored
at the object); and the pure-object / pure-media paths still work.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from foundry_lite.application.ports import ObjectPayload, ObjectQueryItem, ObjectQueryResult
from foundry_lite.application.ports.content_index import ContentSearchHit, HybridContentQuery
from foundry_lite.application.ports.media_reference_binding_repository import MediaReferenceBindingRecord
from foundry_lite.application.services.ontology_search import OntologySearchService, UnifiedSearchHit
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, PermissionDenied
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyMediaReferenceBindingRepository
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

_TYPE = "Order"


def _item(object_id: str, *, version: int = 1) -> ObjectQueryItem:
    return {"objectType": _TYPE, "objectId": object_id, "objectVersion": version, "properties": {"name": object_id}}


def _payload(object_id: str, *, version: int = 1, deleted: bool = False) -> ObjectPayload:
    payload: ObjectPayload = {
        "objectType": _TYPE,
        "objectId": object_id,
        "objectVersion": version,
        "properties": {"name": object_id},
        "sourceDatasetVersionId": None,
    }
    if deleted:
        payload["deleted"] = True
    return payload


def _content_hit(content_unit_id: str, version_id: str) -> ContentSearchHit:
    return ContentSearchHit(
        source_media_item_version_id=version_id,
        content_unit_id=content_unit_id,
        index_generation="g1",
        text_hash="h",
    )


class _FakePolicy:
    def require(self, ctx: RequestContext, permission: str) -> None:
        return None


@dataclass
class _FakeObjectQuery:
    items: list[ObjectQueryItem] = field(default_factory=list)
    readable: dict[str, ObjectPayload] = field(default_factory=dict)
    forbidden: set[str] = field(default_factory=set)

    def query_objects(
        self, object_type_api_name: str, *, semantic_text: str | None = None, **_: object
    ) -> ObjectQueryResult:
        return {"items": list(self.items), "nextCursor": None}

    def get_object(self, object_type_api_name: str, object_id: str, **_: object) -> ObjectPayload:
        if object_id in self.forbidden:
            raise PermissionDenied("masked")
        payload = self.readable.get(object_id)
        if payload is None:
            raise NotFound("object not found")
        return payload


@dataclass
class _FakeContent:
    hits: list[ContentSearchHit] = field(default_factory=list)

    def search_content(self, ctx: RequestContext, *, query: HybridContentQuery) -> list[ContentSearchHit]:
        return list(self.hits)


@dataclass
class _FakeVisual:
    hits: list[ContentSearchHit] = field(default_factory=list)

    def search_visual(self, ctx: RequestContext, *, text: str, top_k: int = 10) -> list[ContentSearchHit]:
        return list(self.hits)


@dataclass(frozen=True)
class _Env:
    service: OntologySearchService
    objects: _FakeObjectQuery
    content: _FakeContent
    visual: _FakeVisual

    def seed_binding(self, *, holder_id: str, version_id: str, property_name: str = "scan") -> None:
        record = MediaReferenceBindingRecord(
            media_reference_binding_id=f"mrb-{holder_id}-{version_id}",
            tenant_id="tenant-demo",
            holder_type=_TYPE,
            holder_id=holder_id,
            property_name=property_name,
            media_set_id="ms-1",
            media_item_id="mi-1",
            media_item_version_id=version_id,
            logical_path=f"/{holder_id}.pdf",
            content_hash="h1",
            security_envelope={"classification": "confidential"},
            idempotency_key=f"idem-{holder_id}-{version_id}",
            created_at="2026-06-23T00:00:00Z",
            updated_at="2026-06-23T00:00:00Z",
        )
        with self.engine.begin() as conn:  # type: ignore[attr-defined]
            self.service.media_reference_binding_repository.create_binding(transaction=conn, record=record)

    @property
    def engine(self) -> Engine:
        return self.service.engine  # type: ignore[return-value]


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    engine = create_engine(f"sqlite:///{tmp_path / 'l12b.db'}", future=True)
    db.create_database(engine)
    repo = SqlAlchemyMediaReferenceBindingRepository(engine)
    objects = _FakeObjectQuery()
    content = _FakeContent()
    visual = _FakeVisual()
    service = OntologySearchService(policy=_FakePolicy(), engine=engine, media_reference_binding_repository=repo)
    service.bind_collaborators(
        {
            "object_query_service": objects,
            "content_retrieval_service": content,
            "media_visual_search_service": visual,
        }
    )
    return _Env(service, objects, content, visual)


def _ids(hits: Sequence[UnifiedSearchHit]) -> list[str]:
    return [hit.object_id for hit in hits]


def test_object_matched_by_own_text_and_bound_media_outranks_single_match(env: _Env) -> None:
    # order-both matches by its own text AND its bound media; order-text only matches own text.
    env.objects.items = [_item("order-text"), _item("order-both")]
    env.objects.readable = {"order-both": _payload("order-both")}
    env.content.hits = [_content_hit("cu-1", "miv-both")]
    env.seed_binding(holder_id="order-both", version_id="miv-both")

    hits = env.service.unified_search(RequestContext(), query_text="termination clause", object_type=_TYPE, limit=10)

    assert _ids(hits)[0] == "order-both"  # fused across both signals -> higher RRF score
    top = hits[0]
    assert top.has_own_text_match is True
    assert [c.content_unit_id for c in top.media_citations] == ["cu-1"]
    assert hits[0].score > hits[1].score


def test_unbound_media_hit_does_not_surface_any_object(env: _Env) -> None:
    env.objects.items = [_item("order-a")]
    env.content.hits = [_content_hit("cu-orphan", "miv-unbound")]  # no binding for miv-unbound
    hits = env.service.unified_search(RequestContext(), query_text="q", object_type=_TYPE, limit=10)
    assert _ids(hits) == ["order-a"]
    assert hits[0].media_citations == ()


def test_media_hit_to_unreadable_object_is_excluded(env: _Env) -> None:
    env.objects.items = []
    env.objects.forbidden = {"order-secret"}
    env.content.hits = [_content_hit("cu-1", "miv-secret")]
    env.seed_binding(holder_id="order-secret", version_id="miv-secret")
    hits = env.service.unified_search(RequestContext(), query_text="q", object_type=_TYPE, limit=10)
    assert hits == []  # ACL anchored at the object: an unreadable owner never surfaces


def test_pure_object_query_still_works(env: _Env) -> None:
    env.objects.items = [_item("order-a"), _item("order-b")]
    hits = env.service.unified_search(RequestContext(), query_text="q", object_type=_TYPE, limit=10)
    assert _ids(hits) == ["order-a", "order-b"]
    assert all(not hit.media_citations for hit in hits)


def test_pure_media_query_lifts_owning_object(env: _Env) -> None:
    env.objects.items = []  # no object matched by its own text
    env.objects.readable = {"order-doc": _payload("order-doc")}
    env.content.hits = [_content_hit("cu-1", "miv-doc")]
    env.seed_binding(holder_id="order-doc", version_id="miv-doc")
    hits = env.service.unified_search(RequestContext(), query_text="invoice", object_type=_TYPE, limit=10)
    assert _ids(hits) == ["order-doc"]
    assert hits[0].has_own_text_match is False
    assert hits[0].media_citations[0].source_media_item_version_id == "miv-doc"


def test_visual_and_text_hit_for_same_unit_count_once(env: _Env) -> None:
    env.objects.readable = {"order-v": _payload("order-v")}
    env.content.hits = [_content_hit("cu-1", "miv-v")]
    env.visual.hits = [_content_hit("cu-1", "miv-v")]  # same content unit from both towers
    env.seed_binding(holder_id="order-v", version_id="miv-v")
    hits = env.service.unified_search(RequestContext(), query_text="logo", object_type=_TYPE, limit=10)
    assert _ids(hits) == ["order-v"]
    assert len(hits[0].media_citations) == 1


def test_deleted_owning_object_is_dropped(env: _Env) -> None:
    env.objects.readable = {"order-del": _payload("order-del", deleted=True)}
    env.content.hits = [_content_hit("cu-1", "miv-del")]
    env.seed_binding(holder_id="order-del", version_id="miv-del")
    hits = env.service.unified_search(RequestContext(), query_text="q", object_type=_TYPE, limit=10)
    assert hits == []
