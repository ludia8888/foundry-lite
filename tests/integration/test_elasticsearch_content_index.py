"""ElasticsearchContentIndexAdapter against a deterministic in-memory fake client.

Proves the index-per-generation projection contract without Docker: version-guarded
upsert, tenant-scoped lexical search over the active alias, source-version deletion,
shadow-then-switch alias promotion (+ conflict), and typed error classification. A live
Elasticsearch round-trip + active-covered ratchet are deferred with the media Operations
surface (same as M1/M2), so this layer is deterministic-only.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.content_index import (
    ContentIndexBatch,
    ContentIndexSchema,
    HybridContentQuery,
    IndexedContentUnit,
)
from foundry_lite.infrastructure.adapters.elasticsearch_content_index import ElasticsearchContentIndexAdapter
from foundry_lite.infrastructure.adapters.elasticsearch_search import ElasticsearchAdapterConfig


class _FakeNotFound(Exception):
    def __init__(self) -> None:
        super().__init__("index_not_found_exception")
        self.meta = type("Meta", (), {"status": 404})()
        self.body = "index_not_found_exception"


class _FakeIndices:
    def __init__(self, es: _FakeES) -> None:
        self._es = es

    def exists(self, *, index: str) -> bool:
        return index in self._es.indices_set

    def create(self, *, index: str, body: Any) -> None:
        del body
        self._es.indices_set.add(index)
        self._es.docs.setdefault(index, {})

    def get_alias(self, *, name: str) -> dict[str, object]:
        if name not in self._es.alias_to_index:
            raise _FakeNotFound()
        return {self._es.alias_to_index[name]: {"aliases": {name: {}}}}

    def update_aliases(self, *, body: Any) -> None:
        for action in body["actions"]:
            if "remove" in action:
                self._es.alias_to_index.pop(action["remove"]["alias"], None)
            if "add" in action:
                add = action["add"]
                self._es.alias_to_index[add["alias"]] = add["index"]


class _FakeES:
    def __init__(self) -> None:
        self.indices_set: set[str] = set()
        self.docs: dict[str, dict[str, dict[str, Any]]] = {}
        self.alias_to_index: dict[str, str] = {}
        self.indices = _FakeIndices(self)

    def update(self, *, index: str, id: str, body: Any, **_options: Any) -> None:
        params = body["script"]["params"]
        document = dict(params["document"])
        store = self.docs.setdefault(index, {})
        existing = store.get(id)
        if existing is not None and existing.get("version", 0) > params["version"]:
            return
        store[id] = document

    def search(self, *, index: str, body: Any) -> dict[str, object]:
        real = self.alias_to_index.get(index, index)
        if real not in self.docs and real not in self.indices_set:
            raise _FakeNotFound()
        if "script_score" in body["query"]:
            return self._dense_search(real, body)
        bool_q = body["query"]["bool"]
        tenant = bool_q["filter"][0]["term"]["tenant_id"]
        must = bool_q["must"][0]
        text = must["match"]["text"] if "match" in must else None
        hits = [
            {"_index": real, "_source": doc}
            for doc in self.docs.get(real, {}).values()
            if doc.get("tenant_id") == tenant and _text_match(doc.get("text", ""), text)
        ]
        return {"hits": {"hits": hits[: body.get("size", 10)]}}

    def _dense_search(self, real: str, body: Any) -> dict[str, object]:
        script = body["query"]["script_score"]
        filters = script["query"]["bool"]["filter"]
        tenant = filters[0]["term"]["tenant_id"]
        model = filters[1]["term"]["embedding_model_version"]
        query_vector = script["script"]["params"]["query_vector"]
        matched = [
            (_cosine(doc.get("embedding", []), query_vector), doc)
            for doc in self.docs.get(real, {}).values()
            if doc.get("tenant_id") == tenant and doc.get("embedding_model_version") == model
        ]
        matched.sort(key=lambda pair: -pair[0])
        hits = [{"_index": real, "_source": doc} for _, doc in matched]
        return {"hits": {"hits": hits[: body.get("size", 10)]}}

    def delete_by_query(self, *, index: str, body: Any, **_options: Any) -> None:
        prefix = index.rstrip("*")
        term = body["query"]["term"]
        field, value = next(iter(term.items()))
        for name, store in self.docs.items():
            if not name.startswith(prefix):
                continue
            for doc_id in [doc_id for doc_id, doc in store.items() if doc.get(field) == value]:
                del store[doc_id]


def _text_match(haystack: str, text: str | None) -> bool:
    if not text:
        return True
    low = haystack.casefold()
    return all(token in low for token in text.casefold().split())


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def _adapter(client: Any | None = None) -> ElasticsearchContentIndexAdapter:
    return ElasticsearchContentIndexAdapter(
        ElasticsearchAdapterConfig(endpoint="http://fake:9200", index_prefix="flite"),
        client=client if client is not None else _FakeES(),
    )


def _unit(
    content_unit_id: str,
    text: str,
    *,
    version: int = 1,
    tenant: str = "t",
    source: str = "miv-1",
    embedding: tuple[float, ...] = (),
    embedding_model_version: str = "",
) -> IndexedContentUnit:
    return IndexedContentUnit(
        tenant_id=tenant,
        content_unit_id=content_unit_id,
        source_media_item_version_id=source,
        text=text,
        text_hash=f"h-{content_unit_id}",
        version=version,
        page_number=1,
        embedding=embedding,
        embedding_model_version=embedding_model_version,
    )


def _activate(adapter: ElasticsearchContentIndexAdapter, generation: str) -> None:
    adapter.configure_generation(ContentIndexSchema(generation=generation))
    adapter.promote_generation("", generation)


def test_configure_upsert_search_round_trip() -> None:
    adapter = _adapter()
    _activate(adapter, "g1")
    result = adapter.upsert_units(
        ContentIndexBatch(generation="g1", units=(_unit("cu-1", "acme payment terms"), _unit("cu-2", "termination")))
    )
    assert result.indexed == 2 and result.failed == 0
    hits = adapter.search(HybridContentQuery(tenant_id="t", text="payment", top_k=10))
    assert [hit.content_unit_id for hit in hits] == ["cu-1"]
    assert hits[0].text_hash == "h-cu-1"


def test_upsert_partial_failure_for_malformed() -> None:
    adapter = _adapter()
    _activate(adapter, "g1")
    result = adapter.upsert_units(ContentIndexBatch(generation="g1", units=(_unit("cu-1", "ok"), _unit("", "bad"))))
    assert result.indexed == 1 and result.failed == 1


def test_version_guard_noop_on_stale() -> None:
    adapter = _adapter()
    _activate(adapter, "g1")
    adapter.upsert_units(ContentIndexBatch(generation="g1", units=(_unit("cu-1", "fresh text", version=5),)))
    adapter.upsert_units(ContentIndexBatch(generation="g1", units=(_unit("cu-1", "stale text", version=2),)))
    assert adapter.search(HybridContentQuery(tenant_id="t", text="stale", top_k=10)) == []
    assert len(adapter.search(HybridContentQuery(tenant_id="t", text="fresh", top_k=10))) == 1


def test_delete_source_version_removes_across_generations() -> None:
    adapter = _adapter()
    _activate(adapter, "g1")
    adapter.upsert_units(
        ContentIndexBatch(
            generation="g1", units=(_unit("cu-1", "a", source="miv-1"), _unit("cu-2", "b", source="miv-2"))
        )
    )
    adapter.delete_source_version("miv-1")
    hits = adapter.search(HybridContentQuery(tenant_id="t", text="", top_k=10))
    assert [hit.content_unit_id for hit in hits] == ["cu-2"]


def test_promote_swaps_alias_shadow_then_switch() -> None:
    adapter = _adapter()
    _activate(adapter, "g1")
    adapter.upsert_units(ContentIndexBatch(generation="g1", units=(_unit("cu-1", "old"),)))
    adapter.configure_generation(ContentIndexSchema(generation="g2"))
    adapter.upsert_units(ContentIndexBatch(generation="g2", units=(_unit("cu-2", "new"),)))
    assert [h.content_unit_id for h in adapter.search(HybridContentQuery(tenant_id="t", top_k=10))] == ["cu-1"]
    adapter.promote_generation("g1", "g2")
    assert [h.content_unit_id for h in adapter.search(HybridContentQuery(tenant_id="t", top_k=10))] == ["cu-2"]


def test_promote_conflict_when_active_moved() -> None:
    adapter = _adapter()
    _activate(adapter, "g1")
    adapter.configure_generation(ContentIndexSchema(generation="g2"))
    with pytest.raises(AdapterError) as excinfo:
        adapter.promote_generation("g-wrong", "g2")
    assert excinfo.value.failure.kind == "conflict"


def test_search_without_active_alias_is_empty() -> None:
    adapter = _adapter()
    assert adapter.search(HybridContentQuery(tenant_id="t", text="x", top_k=5)) == []


def test_transport_error_is_classified() -> None:
    transport = importlib.import_module("elastic_transport")

    class _FaultyES(_FakeES):
        def update(self, *, index: str, id: str, body: Any, **_options: Any) -> None:
            raise transport.ConnectionError("cluster down")

    adapter = _adapter(_FaultyES())
    _activate(adapter, "g1")
    with pytest.raises(AdapterError) as excinfo:
        adapter.upsert_units(ContentIndexBatch(generation="g1", units=(_unit("cu-1", "x"),)))
    assert excinfo.value.failure.kind == "unavailable"
    assert excinfo.value.failure.is_retryable is True


def test_dense_vector_search_ranks_by_similarity() -> None:
    adapter = _adapter()
    _activate(adapter, "g1")
    adapter.upsert_units(
        ContentIndexBatch(
            generation="g1",
            units=(
                _unit("cu-a", "alpha", embedding=(1.0, 0.0, 0.0), embedding_model_version="m1"),
                _unit("cu-b", "beta", embedding=(0.0, 0.0, 1.0), embedding_model_version="m1"),
            ),
        )
    )
    hits = adapter.search(
        HybridContentQuery(tenant_id="t", query_vector=(0.0, 0.0, 1.0), embedding_model_version="m1", top_k=10)
    )
    assert hits[0].content_unit_id == "cu-b"  # ranked first by cosine similarity


def test_failure_contract_declares_modes() -> None:
    contract = _adapter().failure_contract()
    assert contract.adapter_profile == "elasticsearch-content"
    assert {mode.operation for mode in contract.modes} >= {"upsert_units", "search", "promote_generation"}
