"""L13 query-side enrichment: HyDE + query distillation (Palantir OAG query-side techniques).

Two layers, mirroring how every engine started here (fake LLM + REAL bge embedder):

  * Deterministic unit tests with a FAKE completion engine prove the HyDE path embeds the
    LLM hypothetical (search-bait) rather than the raw query, distillation feeds the cleaned
    keyword text to the lexical leg, the default (unavailable) adapter is byte-for-byte the raw
    query (regression guard), and the default engine raises ``completion_model_unavailable``.
  * A retrieval-improvement test runs the REAL fastembed bge engine + a fake LLM and proves HyDE
    measurably ranks the target content unit higher than embedding the short raw question — the
    de-risked premise (short "cold chain rules?" scores the target ~0.59; the hypothetical answer
    scores it ~0.81). bge runs in CI; only the LLM is faked, so this is deterministic.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest
from foundry_lite.application.ports.completion_model import CompletionModelError
from foundry_lite.application.ports.content_index import HybridContentQuery
from foundry_lite.application.ports.media_derivative_repository import ContentUnitRecord, MediaDerivativeRecord
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.application.services.media.indexing import MediaIndexingService
from foundry_lite.application.services.media.query_enrichment import (
    distill_prompt,
    enrich_query,
    hyde_prompt,
)
from foundry_lite.application.services.media.retrieval import DefaultContentRetrievalService
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.local_completion import CompletionEngine, LocalCompletionAdapter
from foundry_lite.infrastructure.adapters.local_content_index import LocalContentIndexAdapter
from foundry_lite.infrastructure.adapters.local_embedding import (
    FASTEMBED_MODEL_VERSION,
    LocalEmbeddingAdapter,
    _fastembed_embedding_engine,
)
from foundry_lite.infrastructure.repositories import SqlAlchemyMediaDerivativeRepository, SqlAlchemyMediaRepository
from sqlalchemy import create_engine

# A cold-chain clause is the retrieval target; an invoice + an unrelated HR doc are distractors.
_COLD_CHAIN = (
    "cu-cold",
    "Refrigerated goods must stay between 2 and 8 degrees Celsius throughout transport, with "
    "continuous temperature logging and an unbroken cold chain from depot to delivery.",
)
_INVOICE = ("cu-invoice", "Invoice #4471 is payable within 30 days of the issue date to the supplier account.")
_HR = ("cu-hr", "Employees accrue twenty paid leave days per calendar year, resetting each January.")

_RAW_QUERY = "cold chain rules?"
# The fake LLM's HyDE hypothetical: a fuller answer-shaped passage that is strong search-bait.
_HYDE_ANSWER = (
    "Cold chain rules require refrigerated shipments to be held between 2 and 8 degrees Celsius "
    "for the entire journey, with continuous temperature logging and no break in refrigeration "
    "from depot to final delivery."
)
_DISTILLED = "cold chain refrigerated transport temperature"
_SOURCE = "miv-l13"


class _FakeCompletionEngine:
    """Deterministic fake LLM: HyDE prompt -> hypothetical answer, distill prompt -> keyword terms."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if prompt == hyde_prompt(_RAW_QUERY):
            return _HYDE_ANSWER
        if prompt == distill_prompt(_RAW_QUERY):
            return _DISTILLED
        return ""


class _RecordingEmbedding:
    """Records the texts handed to the embedder so we can assert WHAT got embedded."""

    model_version = "rec-v1"
    is_available = True

    def __init__(self) -> None:
        self.embedded: list[str] = []

    def embed(self, texts: list[str]) -> tuple[tuple[float, ...], ...]:
        self.embedded.extend(texts)
        return tuple((float(len(text)),) for text in texts)


def test_default_completion_engine_raises_unavailable() -> None:
    adapter = LocalCompletionAdapter()
    assert adapter.is_available is False
    with pytest.raises(CompletionModelError) as excinfo:
        adapter.complete("anything")
    assert excinfo.value.reason == "completion_model_unavailable"


def test_enrich_query_default_off_returns_raw_query() -> None:
    enriched = enrich_query(_RAW_QUERY, LocalCompletionAdapter())
    assert enriched.dense_text == _RAW_QUERY
    assert enriched.keyword_text == _RAW_QUERY


def test_enrich_query_hyde_and_distillation_with_fake_llm() -> None:
    adapter = LocalCompletionAdapter(completion_engine=_FakeCompletionEngine())
    enriched = enrich_query(_RAW_QUERY, adapter)
    # HyDE: the dense leg embeds the hypothetical ANSWER, not the short question (search-bait).
    assert enriched.dense_text == _HYDE_ANSWER
    # Distillation: the keyword leg uses the cleaned terms.
    assert enriched.keyword_text == _DISTILLED


def test_enrich_query_empty_completion_falls_back_to_raw() -> None:
    # An LLM that returns blank completions must not blank out retrieval — fall back to the raw query.
    adapter = LocalCompletionAdapter(completion_engine=lambda _prompt: "")
    enriched = enrich_query(_RAW_QUERY, adapter)
    assert enriched.dense_text == _RAW_QUERY
    assert enriched.keyword_text == _RAW_QUERY


def test_enrich_query_empty_text_skips_completion() -> None:
    # No query text -> nothing to enrich, and the completion model is never called.
    adapter = LocalCompletionAdapter(completion_engine=_FakeCompletionEngine())
    enriched = enrich_query("", adapter)
    assert enriched.dense_text == ""
    assert enriched.keyword_text == ""


def test_retrieval_embeds_hyde_hypothetical_not_raw_query() -> None:
    recording = _RecordingEmbedding()
    retrieval = DefaultContentRetrievalService(
        engine=create_engine("sqlite://", future=True),
        media_derivative_repository=SqlAlchemyMediaDerivativeRepository(create_engine("sqlite://", future=True)),
        content_index_adapter=LocalContentIndexAdapter(),
        embedding_model_adapter=recording,
        completion_model_adapter=LocalCompletionAdapter(completion_engine=_FakeCompletionEngine()),
    )
    scoped = retrieval._with_query_vector(HybridContentQuery(tenant_id="tenant-demo", text=_RAW_QUERY))
    # The embedder was fed the HyDE hypothetical, and the lexical leg carries the distilled text.
    assert recording.embedded == [_HYDE_ANSWER]
    assert scoped.text == _DISTILLED
    assert scoped.query_vector == (float(len(_HYDE_ANSWER)),)


def test_retrieval_default_off_embeds_raw_query() -> None:
    recording = _RecordingEmbedding()
    retrieval = DefaultContentRetrievalService(
        engine=create_engine("sqlite://", future=True),
        media_derivative_repository=SqlAlchemyMediaDerivativeRepository(create_engine("sqlite://", future=True)),
        content_index_adapter=LocalContentIndexAdapter(),
        embedding_model_adapter=recording,
        completion_model_adapter=LocalCompletionAdapter(),
    )
    scoped = retrieval._with_query_vector(HybridContentQuery(tenant_id="tenant-demo", text=_RAW_QUERY))
    # Regression guard: no completion model -> embed the raw query, keyword leg unchanged.
    assert recording.embedded == [_RAW_QUERY]
    assert scoped.text == _RAW_QUERY


class _FakeRuntime:
    def _audit(self, conn: TransactionContext, ctx: RequestContext, *, event_type: str, **_: object) -> None:
        return None

    def _outbox(
        self, conn: TransactionContext, ctx: RequestContext, event_type: str, *_: object, **__: object
    ) -> str | None:
        return event_type


@dataclass(frozen=True)
class _BgeEnv:
    ctx: RequestContext
    engine: object
    repo: SqlAlchemyMediaDerivativeRepository
    embedding: LocalEmbeddingAdapter
    index: LocalContentIndexAdapter
    indexing: MediaIndexingService

    def seed(self, units: list[tuple[str, str]]) -> str:
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

    def retrieval(self, completion_engine: CompletionEngine | None) -> DefaultContentRetrievalService:
        return DefaultContentRetrievalService(
            engine=self.engine,
            media_derivative_repository=self.repo,
            content_index_adapter=self.index,
            embedding_model_adapter=self.embedding,
            completion_model_adapter=LocalCompletionAdapter(completion_engine=completion_engine),
        )


@pytest.fixture
def bge_env(tmp_path: Path) -> _BgeEnv:
    engine = create_engine(f"sqlite:///{tmp_path / 'l13.db'}", future=True)
    db.create_database(engine)
    repo = SqlAlchemyMediaDerivativeRepository(engine)
    index = LocalContentIndexAdapter()
    embedding = LocalEmbeddingAdapter(
        embedding_engine=_fastembed_embedding_engine, model_version=FASTEMBED_MODEL_VERSION
    )
    indexing = MediaIndexingService(
        engine=engine,
        media_repository=SqlAlchemyMediaRepository(engine),
        media_derivative_repository=repo,
        content_index_adapter=index,
        embedding_model_adapter=embedding,
    )
    indexing.bind_collaborators({"runtime_service": _FakeRuntime()})
    return _BgeEnv(RequestContext(), engine, repo, embedding, index, indexing)


@pytest.mark.integration_scenario("media-embeddings")
def test_hyde_improves_real_bge_retrieval_over_raw_query(bge_env: _BgeEnv) -> None:
    """REAL bge + fake LLM: HyDE ranks the cold-chain target ABOVE the raw short question."""
    assert bge_env.embedding.is_available is True
    derivative_id = bge_env.seed([_COLD_CHAIN, _INVOICE, _HR])
    bge_env.indexing.index_derivative(bge_env.ctx, media_derivative_id=derivative_id, generation="g1")
    bge_env.indexing.promote(bge_env.ctx, expected_active="", generation="g1")
    query = HybridContentQuery(tenant_id=bge_env.ctx.tenant_id, text=_RAW_QUERY)

    # Embed the short raw question directly: its dense rank of the cold-chain target.
    raw_hits = bge_env.retrieval(None).search_content(bge_env.ctx, query=query)
    raw_rank = [hit.content_unit_id for hit in raw_hits].index(_COLD_CHAIN[0])

    # HyDE: embed the fuller hypothetical answer instead — the target should rank FIRST and at
    # least as high as the raw path (the de-risked premise: ~0.59 raw vs ~0.81 hypothetical).
    hyde_hits = bge_env.retrieval(_FakeCompletionEngine()).search_content(bge_env.ctx, query=query)
    assert hyde_hits[0].content_unit_id == _COLD_CHAIN[0]
    hyde_rank = [hit.content_unit_id for hit in hyde_hits].index(_COLD_CHAIN[0])
    assert hyde_rank <= raw_rank
