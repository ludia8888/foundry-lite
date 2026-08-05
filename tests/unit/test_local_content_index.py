"""LocalContentIndexAdapter behaviour (Media/Content Plane M3, local profile).

Deterministic, dependency-free proof of the content-index contract: tenant-scoped lexical
search with citations, version-guarded upserts, partial-failure reporting, source-version
deletion, and shadow-then-switch generation promotion with compare-and-set.
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
from foundry_lite.infrastructure.adapters.local_content_index import LocalContentIndexAdapter


def _unit(
    content_unit_id: str,
    text: str,
    *,
    version: int = 1,
    tenant_id: str = "tenant-demo",
    source: str = "miv-1",
    text_hash: str | None = None,
    page_number: int = 1,
    classification: str = "",
    media_set_id: str = "",
) -> IndexedContentUnit:
    return IndexedContentUnit(
        tenant_id=tenant_id,
        content_unit_id=content_unit_id,
        source_media_item_version_id=source,
        text=text,
        text_hash=text_hash if text_hash is not None else f"h-{content_unit_id}",
        version=version,
        page_number=page_number,
        classification=classification,
        media_set_id=media_set_id,
    )


def _activate(adapter: LocalContentIndexAdapter, generation: str) -> None:
    adapter.configure_generation(ContentIndexSchema(generation=generation))
    adapter.promote_generation("", generation)


def test_upsert_search_returns_tenant_scoped_citations() -> None:
    adapter = LocalContentIndexAdapter()
    _activate(adapter, "g1")
    adapter.upsert_units(
        ContentIndexBatch(
            generation="g1",
            units=(
                _unit("cu-1", "acme termination clause", page_number=1),
                _unit("cu-2", "payment terms net 30", page_number=2),
                _unit("cu-x", "other tenant secret", tenant_id="tenant-other"),
            ),
        )
    )
    hits = adapter.search(HybridContentQuery(tenant_id="tenant-demo", text="payment", top_k=10))
    assert [hit.content_unit_id for hit in hits] == ["cu-2"]
    assert hits[0].page_number == 2 and hits[0].text_hash == "h-cu-2" and hits[0].index_generation == "g1"


def test_pre_filter_excludes_over_classified_unit_from_ranking() -> None:
    # AIP P0a ranking side-channel: an over-classified unit that would otherwise be the TOP
    # lexical hit (it repeats the query token more) must never enter the scored/ranked set when
    # the caller's allowed set does not cover it — only the cleared unit is returned.
    adapter = LocalContentIndexAdapter()
    _activate(adapter, "g1")
    adapter.upsert_units(
        ContentIndexBatch(
            generation="g1",
            units=(
                _unit("cu-secret", "payment payment payment top secret", classification="secret"),
                _unit("cu-public", "payment terms", classification="public", page_number=2),
            ),
        )
    )
    hits = adapter.search(
        HybridContentQuery(tenant_id="tenant-demo", text="payment", top_k=10, allowed_classifications=("public",))
    )
    assert [hit.content_unit_id for hit in hits] == ["cu-public"]


def test_media_set_scope_narrows_candidates_before_ranking() -> None:
    """A scoped search must rank inside the scope, not filter a tenant-wide ranking.

    The out-of-scope unit here would win the lexical ranking outright — it repeats the query
    token. If the scope were applied after ranking with a small top_k, the in-scope unit would
    already have been discarded and the screen would show nothing. Palantir avoids this the same
    way: filter the object set first, then run nearestNeighbors inside it.
    """
    adapter = LocalContentIndexAdapter()
    _activate(adapter, "g1")
    adapter.upsert_units(
        ContentIndexBatch(
            generation="g1",
            units=(
                _unit("cu-other", "payment payment payment", media_set_id="ms-other"),
                _unit("cu-mine", "payment terms", media_set_id="ms-mine", page_number=2),
            ),
        )
    )

    hits = adapter.search(
        HybridContentQuery(tenant_id="tenant-demo", text="payment", top_k=1, media_set_ids=("ms-mine",))
    )

    assert [hit.content_unit_id for hit in hits] == ["cu-mine"]
    assert hits[0].media_set_id == "ms-mine"


def test_media_set_scope_of_none_searches_every_readable_set() -> None:
    adapter = LocalContentIndexAdapter()
    _activate(adapter, "g1")
    adapter.upsert_units(
        ContentIndexBatch(
            generation="g1",
            units=(
                _unit("cu-a", "payment a", media_set_id="ms-a"),
                _unit("cu-b", "payment b", media_set_id="ms-b", page_number=2),
            ),
        )
    )

    hits = adapter.search(HybridContentQuery(tenant_id="tenant-demo", text="payment", top_k=10))

    assert {hit.content_unit_id for hit in hits} == {"cu-a", "cu-b"}


def test_media_set_scope_and_classification_scope_both_apply() -> None:
    adapter = LocalContentIndexAdapter()
    _activate(adapter, "g1")
    adapter.upsert_units(
        ContentIndexBatch(
            generation="g1",
            units=(
                _unit("cu-in-secret", "payment", media_set_id="ms-mine", classification="secret"),
                _unit("cu-out-public", "payment", media_set_id="ms-other", classification="public"),
                _unit("cu-in-public", "payment", media_set_id="ms-mine", classification="public", page_number=3),
            ),
        )
    )

    hits = adapter.search(
        HybridContentQuery(
            tenant_id="tenant-demo",
            text="payment",
            top_k=10,
            allowed_classifications=("public",),
            media_set_ids=("ms-mine",),
        )
    )

    assert [hit.content_unit_id for hit in hits] == ["cu-in-public"]


def test_full_clearance_when_allowed_classifications_is_none() -> None:
    # Back-compat: allowed_classifications=None means every classification passes (unchanged).
    adapter = LocalContentIndexAdapter()
    _activate(adapter, "g1")
    adapter.upsert_units(
        ContentIndexBatch(
            generation="g1",
            units=(
                _unit("cu-secret", "payment one", classification="secret"),
                _unit("cu-public", "payment two", classification="public"),
            ),
        )
    )
    hits = adapter.search(HybridContentQuery(tenant_id="tenant-demo", text="payment", top_k=10))
    assert {hit.content_unit_id for hit in hits} == {"cu-secret", "cu-public"}


def test_upsert_reports_partial_failure_for_malformed_units() -> None:
    adapter = LocalContentIndexAdapter()
    _activate(adapter, "g1")
    result = adapter.upsert_units(
        ContentIndexBatch(generation="g1", units=(_unit("cu-1", "good"), _unit("", "missing id", text_hash="")))
    )
    assert result.indexed == 1 and result.failed == 1


def test_version_guard_keeps_newer_projection() -> None:
    adapter = LocalContentIndexAdapter()
    _activate(adapter, "g1")
    adapter.upsert_units(ContentIndexBatch(generation="g1", units=(_unit("cu-1", "new text", version=2),)))
    adapter.upsert_units(ContentIndexBatch(generation="g1", units=(_unit("cu-1", "stale text", version=1),)))
    hits = adapter.search(HybridContentQuery(tenant_id="tenant-demo", text="new", top_k=10))
    assert len(hits) == 1
    assert adapter.search(HybridContentQuery(tenant_id="tenant-demo", text="stale", top_k=10)) == []


def test_delete_source_version_removes_units() -> None:
    adapter = LocalContentIndexAdapter()
    _activate(adapter, "g1")
    adapter.upsert_units(
        ContentIndexBatch(
            generation="g1",
            units=(_unit("cu-1", "alpha", source="miv-1"), _unit("cu-2", "beta", source="miv-2")),
        )
    )
    adapter.delete_source_version("miv-1")
    hits = adapter.search(HybridContentQuery(tenant_id="tenant-demo", text="", top_k=10))
    assert [hit.content_unit_id for hit in hits] == ["cu-2"]


def test_promote_is_shadow_then_switch() -> None:
    adapter = LocalContentIndexAdapter()
    _activate(adapter, "g1")
    adapter.upsert_units(ContentIndexBatch(generation="g1", units=(_unit("cu-1", "old generation"),)))
    # Build the shadow generation without affecting the active one.
    adapter.configure_generation(ContentIndexSchema(generation="g2"))
    adapter.upsert_units(ContentIndexBatch(generation="g2", units=(_unit("cu-2", "new generation"),)))
    assert [h.content_unit_id for h in adapter.search(HybridContentQuery(tenant_id="tenant-demo", top_k=10))] == [
        "cu-1"
    ]
    adapter.promote_generation("g1", "g2")
    assert [h.content_unit_id for h in adapter.search(HybridContentQuery(tenant_id="tenant-demo", top_k=10))] == [
        "cu-2"
    ]


def test_promote_conflict_when_active_moved() -> None:
    adapter = LocalContentIndexAdapter()
    _activate(adapter, "g1")
    adapter.configure_generation(ContentIndexSchema(generation="g2"))
    with pytest.raises(AdapterError) as excinfo:
        adapter.promote_generation("g-wrong", "g2")
    assert excinfo.value.failure.kind == "conflict"


def test_promote_unknown_shadow_raises() -> None:
    adapter = LocalContentIndexAdapter()
    _activate(adapter, "g1")
    with pytest.raises(AdapterError):
        adapter.promote_generation("g1", "g-missing")


def test_search_without_active_generation_is_empty() -> None:
    adapter = LocalContentIndexAdapter()
    assert adapter.search(HybridContentQuery(tenant_id="tenant-demo", text="x", top_k=5)) == []
    assert adapter.failure_contract().adapter_profile == "local-content-index"
