"""L4 real embedding (fastembed) live test (Media/Content Plane).

Proves semantic / hybrid search REALLY works with a real embedding model. The
``LocalEmbeddingAdapter`` is wired with the real ``_fastembed_embedding_engine``
(BAAI/bge-small-en-v1.5, 384-dim ONNX, no torch — the model fastembed downloads to
~/.cache/huggingface, pre-fetched/cached in CI), exactly as ``local_runtime`` composes
it. Three content docs are committed and projected through the real
``MediaIndexingService``; a query with NO lexical overlap with the target doc ranks that
doc first through ``DefaultContentRetrievalService`` — only the dense path can do this, so
real embeddings provably drive the ranking. A second case proves model-version pinning
fails closed against the real model (no silent vector-space mixing). A third case projects
the real-embedding content units into a LIVE Elasticsearch ``dense_vector`` index
(testcontainers) and runs the same semantic query through ``ElasticsearchContentIndexAdapter``,
proving the dog doc ranks first via real ES ``script_score`` cosine similarity.

fastembed is a hard requirement here (the model is pre-fetched/cached in CI), so the test
never skips on a missing model and the real embedding glue stays in the coverage lane.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.content_index import HybridContentQuery
from foundry_lite.application.ports.media_derivative_repository import ContentUnitRecord, MediaDerivativeRecord
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.application.services.media.indexing import MediaIndexingService
from foundry_lite.application.services.media.retrieval import DefaultContentRetrievalService
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.elasticsearch_content_index import ElasticsearchContentIndexAdapter
from foundry_lite.infrastructure.adapters.elasticsearch_search import ElasticsearchAdapterConfig
from foundry_lite.infrastructure.adapters.es_client import build_es_client
from foundry_lite.infrastructure.adapters.local_completion import LocalCompletionAdapter
from foundry_lite.infrastructure.adapters.local_content_index import LocalContentIndexAdapter
from foundry_lite.infrastructure.adapters.local_embedding import (
    FASTEMBED_MODEL_VERSION,
    LocalEmbeddingAdapter,
    _fastembed_embedding_engine,
)
from foundry_lite.infrastructure.repositories import SqlAlchemyMediaDerivativeRepository, SqlAlchemyMediaRepository
from sqlalchemy import create_engine

from tests.integration.elasticsearch_live_lock import live_elasticsearch_container, live_elasticsearch_lock

_DOG = ("cu-dog", "A golden retriever is a friendly dog breed.")
_CAR = ("cu-car", "The sedan has a powerful petrol engine.")
_PLANT = ("cu-plant", "Photosynthesis converts sunlight into energy.")
# No lexical token overlap with the dog doc — only dense similarity can rank it first.
_SEMANTIC_QUERY = "puppy and canine pets"
_SOURCE = "miv-embed-1"
_ES_IMAGE = "docker.elastic.co/elasticsearch/elasticsearch:9.0.0"


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
    repo: SqlAlchemyMediaDerivativeRepository
    embedding: LocalEmbeddingAdapter
    index: LocalContentIndexAdapter
    indexing: MediaIndexingService
    retrieval: DefaultContentRetrievalService

    def seed(self, units: list[tuple[str, str]]) -> str:
        """Insert a COMMITTED derivative + content units (id, text) — content is the truth."""
        tenant_id = self.ctx.tenant_id
        derivative_id = f"mder-{_SOURCE}"
        envelope: Mapping[str, object] = {"tenantId": tenant_id, "classification": "confidential"}
        with self.engine.begin() as conn:  # type: ignore[attr-defined]
            self.repo.create_derivative_or_get_existing(
                transaction=conn,
                record=MediaDerivativeRecord(
                    media_derivative_id=derivative_id,
                    tenant_id=tenant_id,
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
                    created_at="2026-06-23T00:00:00Z",
                ),
            )
            self.repo.insert_content_units(
                transaction=conn,
                records=[
                    ContentUnitRecord(
                        content_unit_id=cid,
                        tenant_id=tenant_id,
                        source_media_item_version_id=_SOURCE,
                        derivative_id=derivative_id,
                        unit_kind="page",
                        ordinal=index,
                        text=text,
                        text_hash=f"h-{cid}",
                        chunk_spec_hash=f"spec-{_SOURCE}",
                        security_envelope=dict(envelope),
                        page_number=index + 1,
                        created_at="2026-06-23T00:00:00Z",
                    )
                    for index, (cid, text) in enumerate(units)
                ],
            )
        return derivative_id


def _real_embedding() -> LocalEmbeddingAdapter:
    # Composed exactly as local_runtime wires it: the real fastembed engine + pinned version.
    return LocalEmbeddingAdapter(embedding_engine=_fastembed_embedding_engine, model_version=FASTEMBED_MODEL_VERSION)


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    engine = create_engine(f"sqlite:///{tmp_path / 'l4.db'}", future=True)
    db.create_database(engine)
    repo = SqlAlchemyMediaDerivativeRepository(engine)
    index = LocalContentIndexAdapter()
    runtime = _FakeRuntime()
    embedding = _real_embedding()
    indexing = MediaIndexingService(
        engine=engine,
        media_repository=SqlAlchemyMediaRepository(engine),
        media_derivative_repository=repo,
        content_index_adapter=index,
        embedding_model_adapter=embedding,
    )
    indexing.bind_collaborators({"runtime_service": runtime})
    retrieval = DefaultContentRetrievalService(
        engine=engine,
        media_derivative_repository=repo,
        content_index_adapter=index,
        embedding_model_adapter=embedding,
        completion_model_adapter=LocalCompletionAdapter(),
    )
    return _Env(RequestContext(), engine, repo, embedding, index, indexing, retrieval)


@pytest.mark.integration_scenario("media-embeddings")
def test_real_embeddings_rank_a_semantic_match_with_no_lexical_overlap(env: _Env) -> None:
    assert env.embedding.is_available is True
    assert env.embedding.model_version == FASTEMBED_MODEL_VERSION

    derivative_id = env.seed([_DOG, _CAR, _PLANT])
    env.indexing.index_derivative(env.ctx, media_derivative_id=derivative_id, generation="g1")
    env.indexing.promote(env.ctx, expected_active="", generation="g1")

    # Lexical alone could never surface the dog doc: the query shares no token with it. Proven
    # directly against the index with a TEXT-ONLY (no vector) query — the lexical rank is empty.
    lexical_only = env.index.search(HybridContentQuery(tenant_id=env.ctx.tenant_id, text=_SEMANTIC_QUERY, top_k=10))
    assert lexical_only == []

    # Through the real fastembed engine, retrieval embeds the query (is_available True) and the
    # dense path ranks the dog doc FIRST despite the zero lexical overlap proven above.
    hits = env.retrieval.search_content(
        env.ctx, query=HybridContentQuery(tenant_id=env.ctx.tenant_id, text=_SEMANTIC_QUERY)
    )
    assert [hit.content_unit_id for hit in hits][0] == _DOG[0]


@pytest.mark.integration_scenario("media-embeddings")
def test_real_embedding_query_fails_closed_on_model_version_mismatch(env: _Env) -> None:
    derivative_id = env.seed([_DOG, _CAR, _PLANT])
    env.indexing.index_derivative(env.ctx, media_derivative_id=derivative_id, generation="g1")
    env.indexing.promote(env.ctx, expected_active="", generation="g1")

    # A real query vector but carrying a DIFFERENT model version must fail closed against the
    # generation pinned to FASTEMBED_MODEL_VERSION (no silent mixing of vector spaces).
    query_vector = env.embedding.embed([_SEMANTIC_QUERY])[0]
    mismatched = HybridContentQuery(
        tenant_id=env.ctx.tenant_id,
        text=_SEMANTIC_QUERY,
        query_vector=query_vector,
        embedding_model_version="some-other-model-v9",
        top_k=10,
    )
    with pytest.raises(AdapterError) as excinfo:
        env.index.search(mismatched)
    assert excinfo.value.failure.kind == "conflict"


@pytest.fixture(scope="module")
def es_url() -> Iterator[str]:
    with live_elasticsearch_lock():
        container = (
            live_elasticsearch_container(_ES_IMAGE)
            .with_env("xpack.security.enabled", "false")
            .with_env("discovery.type", "single-node")
            .with_env("ES_JAVA_OPTS", "-Xms1g -Xmx1g")
            # tmpfs data dir: ES refresh fsync on the vz virtiofs disk is slow enough to exceed
            # client timeouts; a memory-backed data dir keeps writes fast (same as the live cluster).
            .with_kwargs(tmpfs={"/usr/share/elasticsearch/data": "rw,size=1g"})
        )
        container.start()
        try:
            yield f"http://{container.get_container_host_ip()}:{container.get_exposed_port(9200)}"
        finally:
            container.stop()


@pytest.mark.integration_scenario("media-embeddings")
def test_real_embeddings_rank_semantic_match_through_live_elasticsearch_dense_vector(env: _Env, es_url: str) -> None:
    derivative_id = env.seed([_DOG, _CAR, _PLANT])
    client = build_es_client(endpoint=es_url, request_timeout_seconds=30)
    # readiness barrier (not a sleep): block until the cluster can allocate shards
    client.cluster.health(wait_for_status="yellow", timeout="60s")
    es_index = ElasticsearchContentIndexAdapter(
        ElasticsearchAdapterConfig(endpoint=es_url, index_prefix=f"l4{uuid4().hex[:6]}", request_timeout_seconds=30),
        client=client,
    )
    es_indexing = MediaIndexingService(
        engine=env.engine,  # type: ignore[arg-type]
        media_repository=SqlAlchemyMediaRepository(env.engine),
        media_derivative_repository=env.repo,
        content_index_adapter=es_index,
        embedding_model_adapter=env.embedding,
    )
    es_indexing.bind_collaborators({"runtime_service": _FakeRuntime()})
    es_retrieval = DefaultContentRetrievalService(
        engine=env.engine,  # type: ignore[arg-type]
        media_derivative_repository=env.repo,
        content_index_adapter=es_index,
        embedding_model_adapter=env.embedding,
        completion_model_adapter=LocalCompletionAdapter(),
    )

    es_indexing.index_derivative(env.ctx, media_derivative_id=derivative_id, generation="g1")
    es_indexing.promote(env.ctx, expected_active="", generation="g1")

    # The real ES dense_vector script_score (cosine) ranks the dog doc first for a query with
    # no lexical overlap — proving real embeddings indexed into ES drive semantic search.
    hits = es_retrieval.search_content(
        env.ctx, query=HybridContentQuery(tenant_id=env.ctx.tenant_id, text=_SEMANTIC_QUERY)
    )
    assert [hit.content_unit_id for hit in hits][0] == _DOG[0]
