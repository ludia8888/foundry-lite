"""Hybrid (lexical + dense) retrieval over the local content index (Media/Content Plane M8).

Dense retrieval rides the existing projection: a query with a vector fuses lexical and
cosine-kNN ranks via Reciprocal Rank Fusion, so a unit that no lexical token matches is
still recalled by similarity. A query whose embedding model version does not match the
generation's fails closed (no silent mixing of vector spaces) — the regression test for the
promoted ``embedding_artifact_pins_model_version_and_chunk_spec`` invariant.
"""

from __future__ import annotations

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.content_index import (
    ContentIndexBatch,
    ContentIndexSchema,
    HybridContentQuery,
    IndexedContentUnit,
)
from foundry_lite.application.ports.embedding_model import EmbeddingVector
from foundry_lite.infrastructure.adapters.local_content_index import LocalContentIndexAdapter

_MODEL = "fake-v1"


def _unit(cid: str, text: str, embedding: EmbeddingVector) -> IndexedContentUnit:
    return IndexedContentUnit(
        tenant_id="t",
        content_unit_id=cid,
        source_media_item_version_id="miv-1",
        text=text,
        text_hash=f"h-{cid}",
        version=1,
        chunk_spec_hash="chunk-1",
        embedding=embedding,
        embedding_model_version=_MODEL,
    )


def _active_index() -> LocalContentIndexAdapter:
    adapter = LocalContentIndexAdapter()
    adapter.configure_generation(ContentIndexSchema(generation="g1", embedding_model_version=_MODEL))
    adapter.upsert_units(
        ContentIndexBatch(
            generation="g1",
            units=(
                _unit("cu-a", "alpha doc", (1.0, 0.0, 0.0)),
                _unit("cu-b", "beta doc", (0.0, 1.0, 0.0)),
                _unit("cu-c", "gamma", (0.0, 0.0, 1.0)),
            ),
        )
    )
    adapter.promote_generation("", "g1")
    return adapter


def test_hybrid_recalls_a_lexical_miss_via_dense_similarity() -> None:
    adapter = _active_index()
    query = HybridContentQuery(
        tenant_id="t", text="doc", query_vector=(0.0, 0.0, 1.0), embedding_model_version=_MODEL, top_k=10
    )
    hits = [hit.content_unit_id for hit in adapter.search(query)]

    assert hits[0] == "cu-a"  # strongest under fusion (lexical + dense)
    assert "cu-c" in hits  # recalled purely by dense similarity despite no lexical token


def test_lexical_only_query_does_not_recall_dense_only_match() -> None:
    adapter = _active_index()
    hits = [hit.content_unit_id for hit in adapter.search(HybridContentQuery(tenant_id="t", text="doc", top_k=10))]

    assert hits == ["cu-a", "cu-b"]  # cu-c has no "doc" token and no vector was supplied


def test_hybrid_search_fails_closed_on_model_version_mismatch() -> None:
    adapter = _active_index()
    query = HybridContentQuery(
        tenant_id="t", text="doc", query_vector=(0.0, 0.0, 1.0), embedding_model_version="other-model", top_k=10
    )
    with pytest.raises(AdapterError) as excinfo:
        adapter.search(query)
    assert excinfo.value.failure.kind == "conflict"
