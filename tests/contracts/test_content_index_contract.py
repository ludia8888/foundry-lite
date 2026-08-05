"""Contract for ``ContentIndexAdapter`` (ADR-0001 §5.3 / invariant 5).

The content index is a rebuildable projection of committed content units; generation
promotion is shadow-then-switch. Contract only this sprint. We lock the Protocol
shape, the generation-scoped schema/batch DTOs, and partial-failure reporting.
"""

from __future__ import annotations

from foundry_lite.application.ports.content_index import (
    ContentIndexAdapter,
    ContentIndexBatch,
    ContentIndexBatchResult,
    ContentIndexSchema,
    ContentSearchHit,
    HybridContentQuery,
)


class _FakeContentIndex:
    """In-memory projection pinning the configure/upsert/search/promote shape."""

    def __init__(self) -> None:
        self.active = "g0"

    def configure_generation(self, schema: ContentIndexSchema) -> None:
        return None

    def upsert_units(self, batch: ContentIndexBatch) -> ContentIndexBatchResult:
        return ContentIndexBatchResult(indexed=len(batch.units), failed=0)

    def delete_source_version(self, source_version_id: str) -> None:
        return None

    def search(self, query: HybridContentQuery) -> list[ContentSearchHit]:
        # Scope is part of the query contract, not a courtesy: an adapter that ignores
        # media_set_ids would let a scoped screen serve hits from other media sets.
        if query.media_set_ids is not None and "ms-1" not in query.media_set_ids:
            return []
        return [
            ContentSearchHit(
                source_media_item_version_id="miv-1",
                content_unit_id="cu-1",
                index_generation=self.active,
                page_number=1,
                media_set_id="ms-1",
            )
        ]

    def active_generation(self) -> str:
        return self.active

    def promote_generation(self, expected_active: str, shadow: str) -> None:
        if self.active != expected_active:
            raise AssertionError(f"active moved: expected {expected_active!r}, found {self.active!r}")
        self.active = shadow


def test_content_index_projection_shape() -> None:
    adapter: ContentIndexAdapter = _FakeContentIndex()
    adapter.configure_generation(ContentIndexSchema(generation="g1", fields=("text",)))
    result = adapter.upsert_units(ContentIndexBatch(generation="g1", units=(object(), object())))
    assert adapter.active_generation() == "g0", "a fresh index must report its active generation"
    adapter.promote_generation(adapter.active_generation(), "g1")
    assert adapter.active_generation() == "g1"
    hits = adapter.search(HybridContentQuery(tenant_id="t", text="acme", top_k=5))

    scoped_out = adapter.search(HybridContentQuery(tenant_id="t", text="acme", top_k=5, media_set_ids=("ms-other",)))
    scoped_in = adapter.search(HybridContentQuery(tenant_id="t", text="acme", top_k=5, media_set_ids=("ms-1",)))

    assert scoped_out == [], "a media-set scope must be applied inside the query"
    assert [hit.content_unit_id for hit in scoped_in] == ["cu-1"]
    assert scoped_in[0].media_set_id == "ms-1", "hits carry the media set so callers can group them"
    assert result.indexed == 2 and result.failed == 0
    assert hits[0].source_media_item_version_id == "miv-1"
    assert hits[0].index_generation == "g1"
