"""M3 content indexing + retrieval invariants (Media/Content Plane).

Content units in the DB are the truth; the index is a rebuildable projection. These tests
prove: index → search returns cited hits; the projection rebuilds purely from committed DB
rows; and retrieval re-reads authoritative truth to drop citation-hash mismatches (citation
integrity), cross-tenant hits (ACL — no leakage), and hits whose source unit is gone (stale).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest
from foundry_lite.application.ports.content_index import (
    ContentSearchHit,
    HybridContentQuery,
    IndexedContentUnit,
)
from foundry_lite.application.ports.media_derivative_repository import ContentUnitRecord, MediaDerivativeRecord
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.application.services.media.indexing import MediaIndexingService
from foundry_lite.application.services.media.retrieval import DefaultContentRetrievalService
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.local_content_index import LocalContentIndexAdapter
from foundry_lite.infrastructure.repositories import SqlAlchemyMediaDerivativeRepository
from sqlalchemy import create_engine

_SOURCE = "miv-1"


class _FakeRuntime:
    def _audit(self, conn: TransactionContext, ctx: RequestContext, *, event_type: str, **_: object) -> None:
        return None

    def _outbox(
        self, conn: TransactionContext, ctx: RequestContext, event_type: str, *_: object, **__: object
    ) -> str | None:
        return event_type


class _StubIndex:
    """Returns hand-crafted hits so retrieval's authoritative re-read can be exercised."""

    def __init__(self, hits: list[ContentSearchHit]) -> None:
        self._hits = hits

    def search(self, query: HybridContentQuery) -> list[ContentSearchHit]:
        return list(self._hits)


@dataclass(frozen=True)
class _Env:
    ctx: RequestContext
    engine: object
    repo: SqlAlchemyMediaDerivativeRepository
    index: LocalContentIndexAdapter
    indexing: MediaIndexingService
    retrieval: DefaultContentRetrievalService

    def seed(self, units: list[tuple[str, str, str]], *, tenant: str | None = None, source: str = _SOURCE) -> str:
        """Insert a COMMITTED derivative + content units. units = (id, text, text_hash)."""
        tenant_id = tenant or self.ctx.tenant_id
        derivative_id = f"mder-{source}"
        envelope: Mapping[str, object] = {"tenantId": tenant_id, "classification": "confidential"}
        with self.engine.begin() as conn:  # type: ignore[attr-defined]
            self.repo.create_derivative_or_get_existing(
                transaction=conn,
                record=MediaDerivativeRecord(
                    media_derivative_id=derivative_id,
                    tenant_id=tenant_id,
                    source_media_item_version_id=source,
                    derivative_kind="pdf_text",
                    processor_spec_hash=f"spec-{source}",
                    processor_name="pdf_text_v1",
                    processor_version="1.0.0",
                    model_name=None,
                    model_version="",
                    params_hash=f"spec-{source}",
                    security_envelope=dict(envelope),
                    status="COMMITTED",
                    created_at="2026-06-23T00:00:00Z",
                ),
            )
            self.repo.insert_content_units(
                transaction=conn,
                records=[
                    ContentUnitRecord(
                        content_unit_id=cid,
                        tenant_id=tenant_id,
                        source_media_item_version_id=source,
                        derivative_id=derivative_id,
                        unit_kind="page",
                        ordinal=index,
                        text=text,
                        text_hash=text_hash,
                        chunk_spec_hash=f"spec-{source}",
                        security_envelope=dict(envelope),
                        page_number=index + 1,
                        created_at="2026-06-23T00:00:00Z",
                    )
                    for index, (cid, text, text_hash) in enumerate(units)
                ],
            )
        return derivative_id


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    engine = create_engine(f"sqlite:///{tmp_path / 'm3.db'}", future=True)
    db.create_database(engine)
    repo = SqlAlchemyMediaDerivativeRepository(engine)
    index = LocalContentIndexAdapter()
    runtime = _FakeRuntime()
    indexing = MediaIndexingService(engine=engine, media_derivative_repository=repo, content_index_adapter=index)
    indexing.bind_collaborators({"runtime_service": runtime})
    retrieval = DefaultContentRetrievalService(
        engine=engine, media_derivative_repository=repo, content_index_adapter=index
    )
    return _Env(RequestContext(), engine, repo, index, indexing, retrieval)


def test_index_then_search_returns_cited_hits(env: _Env) -> None:
    derivative_id = env.seed([("cu-1", "acme termination clause", "h1"), ("cu-2", "net 30 payment terms", "h2")])
    env.indexing.configure(env.ctx, generation="g1")
    outcome = env.indexing.index_derivative(env.ctx, media_derivative_id=derivative_id, generation="g1")
    env.indexing.promote(env.ctx, expected_active="", generation="g1")
    assert outcome.indexed == 2 and outcome.failed == 0

    hits = env.retrieval.search_content(env.ctx, query=HybridContentQuery(tenant_id=env.ctx.tenant_id, text="payment"))
    assert [hit.content_unit_id for hit in hits] == ["cu-2"]
    assert hits[0].page_number == 2 and hits[0].text_hash == "h2"


def test_content_index_rebuilds_from_committed_artifacts(env: _Env) -> None:
    derivative_id = env.seed([("cu-1", "alpha doc", "h1"), ("cu-2", "beta doc", "h2")])
    env.indexing.index_derivative(env.ctx, media_derivative_id=derivative_id, generation="g1")
    env.indexing.promote(env.ctx, expected_active="", generation="g1")
    before = env.retrieval.search_content(env.ctx, query=HybridContentQuery(tenant_id=env.ctx.tenant_id, text="doc"))

    # Rebuild a fresh generation purely from committed DB content units, then switch.
    env.indexing.rebuild(env.ctx, generation="g2", source_media_item_version_ids=[_SOURCE])
    env.indexing.promote(env.ctx, expected_active="g1", generation="g2")
    after = env.retrieval.search_content(env.ctx, query=HybridContentQuery(tenant_id=env.ctx.tenant_id, text="doc"))

    assert {hit.content_unit_id for hit in before} == {"cu-1", "cu-2"}
    assert {hit.content_unit_id for hit in after} == {"cu-1", "cu-2"}


def test_retrieval_drops_citation_hash_mismatch(env: _Env) -> None:
    derivative_id = env.seed([("cu-1", "acme payment terms", "h1")])
    env.indexing.index_derivative(env.ctx, media_derivative_id=derivative_id, generation="g1")
    env.indexing.promote(env.ctx, expected_active="", generation="g1")
    # Tamper the projection: a stale/poisoned index hit whose text_hash no longer matches DB.
    env.index.upsert_units(
        _batch("g1", IndexedContentUnit("tenant-demo", "cu-1", _SOURCE, "acme payment terms", "TAMPERED", version=99))
    )
    hits = env.retrieval.search_content(env.ctx, query=HybridContentQuery(tenant_id=env.ctx.tenant_id, text="payment"))
    assert hits == []


def test_retrieval_filters_cross_tenant_acl_leakage(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'm3acl.db'}", future=True)
    db.create_database(engine)
    repo = SqlAlchemyMediaDerivativeRepository(engine)
    # The DB content unit belongs to ANOTHER tenant; a leaky index returns it anyway.
    leaked = ContentSearchHit(
        source_media_item_version_id=_SOURCE, content_unit_id="cu-secret", index_generation="g1", text_hash="hs"
    )
    retrieval = DefaultContentRetrievalService(
        engine=engine, media_derivative_repository=repo, content_index_adapter=_StubIndex([leaked])
    )
    env = _Env(RequestContext(), engine, repo, LocalContentIndexAdapter(), None, retrieval)  # type: ignore[arg-type]
    env.seed([("cu-secret", "other tenant secret", "hs")], tenant="tenant-other")

    hits = retrieval.search_content(RequestContext(), query=HybridContentQuery(tenant_id="tenant-demo", text="secret"))
    assert hits == []


def test_retrieval_drops_stale_source_unit(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'm3stale.db'}", future=True)
    db.create_database(engine)
    repo = SqlAlchemyMediaDerivativeRepository(engine)
    # The index returns a hit for a content unit that no longer exists in the DB.
    ghost = ContentSearchHit(
        source_media_item_version_id=_SOURCE, content_unit_id="cu-ghost", index_generation="g1", text_hash="hg"
    )
    retrieval = DefaultContentRetrievalService(
        engine=engine, media_derivative_repository=repo, content_index_adapter=_StubIndex([ghost])
    )
    hits = retrieval.search_content(RequestContext(), query=HybridContentQuery(tenant_id="tenant-demo", text="x"))
    assert hits == []


def test_indexing_surfaces_partial_failure(env: _Env) -> None:
    # A content unit with an empty text_hash is malformed for the index; it is reported, not lost.
    derivative_id = env.seed([("cu-1", "good", "h1"), ("cu-2", "bad", "")])
    outcome = env.indexing.index_derivative(env.ctx, media_derivative_id=derivative_id, generation="g1")
    assert outcome.indexed == 1 and outcome.failed == 1


def _batch(generation: str, *units: IndexedContentUnit):
    from foundry_lite.application.ports.content_index import ContentIndexBatch

    return ContentIndexBatch(generation=generation, units=units)
