"""Contract for ``ContentRetrievalService`` (ADR-0001 §10.1 / invariant 5).

Distinct from object-level query: content-unit artifacts are the truth and the index
is a rebuildable projection. Implementations re-read authoritative metadata/security
before returning hits. Contract only this sprint — we lock the search-projection shape.
"""

from __future__ import annotations

from foundry_lite.application.ports.content_index import ContentSearchHit, HybridContentQuery
from foundry_lite.application.ports.content_retrieval import ContentRetrievalService


class _FakeContentRetrieval:
    """Retrieval that returns one cited hit after a (no-op here) security re-read."""

    def search_content(self, query: HybridContentQuery) -> list[ContentSearchHit]:
        return [
            ContentSearchHit(
                source_media_item_version_id="miv-1",
                content_unit_id="cu-1",
                index_generation="g1",
                page_number=2,
                text_hash="th-1",
            )
        ]


def test_content_retrieval_returns_cited_hits() -> None:
    service: ContentRetrievalService = _FakeContentRetrieval()
    hits = service.search_content(HybridContentQuery(tenant_id="t", text="acme", top_k=3))

    assert len(hits) == 1
    assert hits[0].content_unit_id == "cu-1"
    assert hits[0].page_number == 2
    assert hits[0].text_hash == "th-1"
