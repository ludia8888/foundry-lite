"""M8 dense projection + hybrid retrieval through the media services (Media/Content Plane).

Content units in the DB stay the truth; the embedding is a derived projection attached to
the index unit. With an embedding model available, projection pins the model version and the
chunk spec onto each indexed unit (the ``embedding_artifact_pins_model_version_and_chunk_spec``
invariant), a rebuild re-embeds purely from committed DB rows, and retrieval embeds the query
to return semantic hits — still re-reading authoritative truth for citation/ACL.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest
from foundry_lite.application.ports.content_index import HybridContentQuery
from foundry_lite.application.ports.embedding_model import EmbeddingVector
from foundry_lite.application.ports.media_derivative_repository import ContentUnitRecord, MediaDerivativeRecord
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.application.services.media.indexing import MediaIndexingService
from foundry_lite.application.services.media.retrieval import DefaultContentRetrievalService
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.local_completion import LocalCompletionAdapter
from foundry_lite.infrastructure.adapters.local_content_index import LocalContentIndexAdapter
from foundry_lite.infrastructure.adapters.local_embedding import LocalEmbeddingAdapter
from foundry_lite.infrastructure.repositories import SqlAlchemyMediaDerivativeRepository
from sqlalchemy import create_engine

_SOURCE = "miv-1"
_MODEL = "fake-v1"
_DIM = 8


def _fake_engine(texts: Sequence[str]) -> list[EmbeddingVector]:
    vectors: list[EmbeddingVector] = []
    for text in texts:
        buckets = [0.0] * _DIM
        for token in text.casefold().split():
            buckets[int(hashlib.sha1(token.encode()).hexdigest(), 16) % _DIM] += 1.0
        vectors.append(tuple(buckets))
    return vectors


class _FakeRuntime:
    def _audit(self, conn: TransactionContext, ctx: RequestContext, *, event_type: str, **_: object) -> None:
        return None

    def _outbox(
        self, conn: TransactionContext, ctx: RequestContext, event_type: str, *_: object, **__: object
    ) -> str | None:
        return event_type


@dataclass(frozen=True)
class _Env:
    ctx: RequestContext
    engine: object
    index: LocalContentIndexAdapter
    indexing: MediaIndexingService
    retrieval: DefaultContentRetrievalService

    def seed(self, units: list[tuple[str, str, str]]) -> str:
        derivative_id = f"mder-{_SOURCE}"
        envelope: Mapping[str, object] = {"tenantId": self.ctx.tenant_id, "classification": "confidential"}
        repo = SqlAlchemyMediaDerivativeRepository(self.engine)  # type: ignore[arg-type]
        with self.engine.begin() as conn:  # type: ignore[attr-defined]
            repo.create_derivative_or_get_existing(
                transaction=conn,
                record=MediaDerivativeRecord(
                    media_derivative_id=derivative_id,
                    tenant_id=self.ctx.tenant_id,
                    source_media_item_version_id=_SOURCE,
                    derivative_kind="pdf_text",
                    processor_spec_hash=f"spec-{_SOURCE}",
                    processor_name="pdf_text_v1",
                    processor_version="1.0.0",
                    model_name=None,
                    model_version="",
                    params_hash=f"spec-{_SOURCE}",
                    security_envelope=dict(envelope),
                    status="COMMITTED",
                    created_at="2026-06-24T00:00:00Z",
                ),
            )
            repo.insert_content_units(
                transaction=conn,
                records=[
                    ContentUnitRecord(
                        content_unit_id=cid,
                        tenant_id=self.ctx.tenant_id,
                        source_media_item_version_id=_SOURCE,
                        derivative_id=derivative_id,
                        unit_kind="page",
                        ordinal=index,
                        text=text,
                        text_hash=text_hash,
                        chunk_spec_hash=f"chunk-{_SOURCE}",
                        security_envelope=dict(envelope),
                        page_number=index + 1,
                        created_at="2026-06-24T00:00:00Z",
                    )
                    for index, (cid, text, text_hash) in enumerate(units)
                ],
            )
        return derivative_id


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    engine = create_engine(f"sqlite:///{tmp_path / 'm8.db'}", future=True)
    db.create_database(engine)
    repo = SqlAlchemyMediaDerivativeRepository(engine)
    index = LocalContentIndexAdapter()
    embedding = LocalEmbeddingAdapter(embedding_engine=_fake_engine, model_version=_MODEL)
    indexing = MediaIndexingService(
        engine=engine,
        media_derivative_repository=repo,
        content_index_adapter=index,
        embedding_model_adapter=embedding,
    )
    indexing.bind_collaborators({"runtime_service": _FakeRuntime()})
    retrieval = DefaultContentRetrievalService(
        engine=engine,
        media_derivative_repository=repo,
        content_index_adapter=index,
        embedding_model_adapter=embedding,
        completion_model_adapter=LocalCompletionAdapter(),
    )
    return _Env(RequestContext(), engine, index, indexing, retrieval)


def test_embedding_artifact_pins_model_and_chunk_spec(env: _Env) -> None:
    derivative_id = env.seed([("cu-1", "acme termination clause", "h1")])
    env.indexing.index_derivative(env.ctx, media_derivative_id=derivative_id, generation="g1")

    indexed = env.index._generations["g1"]["cu-1"]
    assert indexed.embedding_model_version == _MODEL
    assert indexed.chunk_spec_hash == f"chunk-{_SOURCE}"
    assert len(indexed.embedding) == _DIM and any(value > 0.0 for value in indexed.embedding)


def test_rebuild_reembeds_from_committed_truth(env: _Env) -> None:
    derivative_id = env.seed([("cu-1", "alpha doc", "h1"), ("cu-2", "beta doc", "h2")])
    env.indexing.index_derivative(env.ctx, media_derivative_id=derivative_id, generation="g1")
    env.indexing.promote(env.ctx, expected_active="", generation="g1")

    env.indexing.rebuild(env.ctx, generation="g2", source_media_item_version_ids=[_SOURCE])
    rebuilt = env.index._generations["g2"]["cu-1"]
    assert rebuilt.embedding_model_version == _MODEL and len(rebuilt.embedding) == _DIM


def test_dense_retrieval_returns_authoritative_hits(env: _Env) -> None:
    derivative_id = env.seed([("cu-1", "acme payment terms", "h1"), ("cu-2", "net 30 invoice", "h2")])
    env.indexing.index_derivative(env.ctx, media_derivative_id=derivative_id, generation="g1")
    env.indexing.promote(env.ctx, expected_active="", generation="g1")

    hits = env.retrieval.search_content(env.ctx, query=HybridContentQuery(tenant_id=env.ctx.tenant_id, text="payment"))
    ids = [hit.content_unit_id for hit in hits]
    assert ids[0] == "cu-1"  # the lexical+dense match ranks first
    assert hits[0].text_hash == "h1"  # citation re-read against authoritative truth
