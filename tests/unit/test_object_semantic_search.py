"""L12a object semantic search — deterministic unit tests with a fake bge embedding adapter.

These prove the Palantir Vector-property mirror without a real model:
- an object's searchable text is embedded into the search document with a pinned model version,
- a nearestNeighbors query returns the semantically-closest object first,
- a query whose embedding model version mismatches the index FAILS CLOSED,
- with NO embedding adapter wired, object search stays keyword-only exactly as before.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import ObjectRecordRow
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.embedding_model import EmbeddingVector
from foundry_lite.application.ports.search_adapter import (
    SearchDocument,
    SearchIndexMapping,
    SearchQuery,
)
from foundry_lite.application.services.object_store.semantic_search import (
    object_embedding,
    search_document,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure.adapters import LocalEmbeddingAdapter, LocalSearchAdapter
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies

from tests.conftest import prepare_indexed_demo

_MODEL = "fake-bge-v1"
_DIM = 8


def _fake_engine(texts: Sequence[str]) -> list[EmbeddingVector]:
    """Deterministic bag-of-tokens vectors: shared tokens raise cosine similarity."""
    vectors: list[EmbeddingVector] = []
    for text in texts:
        buckets = [0.0] * _DIM
        for token in text.casefold().split():
            buckets[sum(ord(ch) for ch in token) % _DIM] += 1.0
        vectors.append(tuple(buckets))
    return vectors


def _fake_adapter() -> LocalEmbeddingAdapter:
    return LocalEmbeddingAdapter(embedding_engine=_fake_engine, model_version=_MODEL)


def _document(object_id: str, text: str, adapter: LocalEmbeddingAdapter) -> SearchDocument:
    mapping = SearchIndexMapping("t", "Doc", searchable_properties=("body",))
    embedding, model_version = object_embedding(adapter, {"body": text}, mapping)
    return SearchDocument("t", "Doc", object_id, 1, {"body": text}, embedding, model_version)


def test_object_embedding_pins_model_version_when_adapter_available() -> None:
    mapping = SearchIndexMapping("t", "Doc", searchable_properties=("body",))
    embedding, model_version = object_embedding(_fake_adapter(), {"body": "cold chain truck"}, mapping)
    assert model_version == _MODEL
    assert len(embedding) == _DIM and any(value > 0.0 for value in embedding)


def test_object_embedding_is_empty_when_no_model_wired() -> None:
    keyword_only = LocalEmbeddingAdapter()  # default engine, is_available False
    mapping = SearchIndexMapping("t", "Doc", searchable_properties=("body",))
    embedding, model_version = object_embedding(keyword_only, {"body": "cold chain truck"}, mapping)
    assert embedding == () and model_version == ""


def test_search_document_attaches_vector_from_searchable_properties() -> None:
    mapping = SearchIndexMapping("t", "Doc", searchable_properties=("body",), indexed_properties=("status",))
    row: ObjectRecordRow = cast(
        "ObjectRecordRow",
        {"object_id": "D1", "object_version": 3, "properties": {"body": "refrigerated truck", "status": "OPEN"}},
    )
    document = search_document(RequestContext(tenant_id="t"), "Doc", row, mapping, _fake_adapter())
    assert document.embedding_model_version == _MODEL
    assert document.embedding and len(document.embedding) == _DIM


def test_nearest_neighbors_ranks_semantically_closest_first() -> None:
    adapter = _fake_adapter()
    index = LocalSearchAdapter()
    index.upsert_document(_document("truck", "refrigerated truck delivery", adapter))
    index.upsert_document(_document("invoice", "an invoice dispute", adapter))
    query_vector = adapter.embed(["refrigerated truck delivery"])[0]
    hits = index.search(
        SearchQuery("t", "Doc", terms={}, query_vector=query_vector, embedding_model_version=_MODEL, limit=10)
    )
    assert [hit.document_id for hit in hits][0] == "truck"


def test_nearest_neighbors_fails_closed_on_model_version_mismatch() -> None:
    adapter = _fake_adapter()
    index = LocalSearchAdapter()
    index.upsert_document(_document("truck", "refrigerated truck delivery", adapter))
    query_vector = adapter.embed(["truck"])[0]
    with pytest.raises(AdapterError) as excinfo:
        index.search(
            SearchQuery(
                "t", "Doc", terms={}, query_vector=query_vector, embedding_model_version="other-model", limit=10
            )
        )
    assert excinfo.value.failure.kind == "conflict"


def test_keyword_path_unchanged_when_no_vector_on_query() -> None:
    adapter = _fake_adapter()
    index = LocalSearchAdapter()
    index.configure_index(SearchIndexMapping("t", "Doc", searchable_properties=("body",)))
    index.upsert_document(_document("truck", "refrigerated truck delivery", adapter))
    hits = index.search(SearchQuery("t", "Doc", terms={}, text="truck", searchable_properties=("body",)))
    assert [hit.document_id for hit in hits] == ["truck"]
    assert hits[0].score == 1.0  # keyword path keeps the uniform score


def test_semantic_query_without_model_is_rejected(tmp_path: Path) -> None:
    # The composed runtime wires real fastembed, so force a keyword-only deployment here.
    deps = replace(
        create_local_core_dependencies(storage_root=tmp_path / "flite"),
        embedding_model_adapter=LocalEmbeddingAdapter(),
    )
    foundry = FoundryLite(dependencies=deps)
    ctx = prepare_indexed_demo(foundry)
    with pytest.raises(ValidationFailed, match="requires a wired embedding model"):
        foundry.objects.query("Order", ctx=ctx, semantic_text="cold chain logistics", limit=5)


def _approve_order(foundry: FoundryLite, ctx: RequestContext, object_id: str, reason: str) -> None:
    order = foundry.objects.get("Order", object_id, ctx=ctx)
    foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id=object_id,
        expected_object_version=order["objectVersion"],
        params={"reason": reason},
        idempotency_key=f"semantic-{object_id}",
        ctx=ctx,
    )


def test_semantic_object_search_ranks_closest_object_first(tmp_path: Path) -> None:
    deps = replace(
        create_local_core_dependencies(storage_root=tmp_path / "flite"),
        embedding_model_adapter=_fake_adapter(),
    )
    foundry = FoundryLite(dependencies=deps)
    ctx = prepare_indexed_demo(foundry)
    # operatorNote is the Order's searchable property; populate two distinct notes via the action.
    _approve_order(foundry, ctx, "O-1001", "refrigerated truck delivery")
    _approve_order(foundry, ctx, "O-1002", "an invoice dispute")
    foundry.objects.rebuild_search("Order", ctx=ctx)

    page = foundry.objects.query("Order", ctx=ctx, semantic_text="refrigerated truck delivery", limit=5)

    assert page["nextCursor"] is None
    assert page["items"]  # nearestNeighbors returns a relevance-ordered Object Set
    assert page["items"][0]["objectId"] == "O-1001"


def test_semantic_and_keyword_cannot_combine(tmp_path: Path) -> None:
    deps = replace(
        create_local_core_dependencies(storage_root=tmp_path / "flite"),
        embedding_model_adapter=_fake_adapter(),
    )
    foundry = FoundryLite(dependencies=deps)
    ctx = prepare_indexed_demo(foundry)
    with pytest.raises(ValidationFailed, match="cannot combine"):
        foundry.objects.query("Order", ctx=ctx, search_text="x", semantic_text="y", limit=5)
