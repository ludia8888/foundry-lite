from __future__ import annotations

import pytest
from foundry_lite.application.ports.search_adapter import SearchAdapter, SearchDocument, SearchIndexMapping, SearchQuery
from foundry_lite.infrastructure.adapters import (
    ElasticsearchAdapter,
    ElasticsearchAdapterConfig,
    FakeSearchAdapter,
    LocalSearchAdapter,
)


class FakeElasticsearchIndices:
    def __init__(self) -> None:
        self.created: set[str] = set()

    def exists(self, *, index: str) -> bool:
        return index in self.created

    def create(self, *, index: str, body: dict[str, object]) -> object:
        self.created.add(index)
        return {"acknowledged": True, "body": body}

    def put_mapping(self, *, index: str, body: dict[str, object]) -> object:
        self.created.add(index)
        return {"acknowledged": True, "body": body}


class FakeElasticsearchClient:
    def __init__(self) -> None:
        self.indices = FakeElasticsearchIndices()
        self.documents: dict[tuple[str, str], dict[str, object]] = {}

    def index(self, *, index: str, id: str, body: dict[str, object], refresh: bool = False) -> object:
        self.documents[(index, id)] = dict(body)
        return {"result": "updated", "refresh": refresh}

    def update(self, *, index: str, id: str, body: dict[str, object], **options: object) -> object:
        refresh = bool(options.get("refresh", False))
        current = self.documents.get((index, id))
        upsert = body.get("upsert")
        script = body.get("script")
        if not isinstance(upsert, dict) or not isinstance(script, dict):
            self.documents[(index, id)] = dict(body)
            return {"result": "updated", "refresh": refresh}
        params = script.get("params")
        if not isinstance(params, dict):
            self.documents[(index, id)] = dict(upsert)
            return {"result": "updated", "refresh": refresh}
        document = params.get("document")
        incoming_version = params.get("version")
        if not isinstance(document, dict) or not isinstance(incoming_version, int):
            self.documents[(index, id)] = dict(upsert)
            return {"result": "updated", "refresh": refresh}
        if current is None:
            self.documents[(index, id)] = dict(upsert)
            return {"result": "created", "refresh": refresh}
        current_version = current.get("version")
        if isinstance(current_version, int) and incoming_version <= current_version:
            return {"result": "noop", "refresh": refresh}
        self.documents[(index, id)] = dict(document)
        return {"result": "updated", "refresh": refresh}

    def delete(self, *, index: str, id: str, ignore_status: tuple[int, ...] = (), refresh: bool = False) -> object:
        self.documents.pop((index, id), None)
        return {"result": "deleted", "ignore_status": ignore_status, "refresh": refresh}

    def search(self, *, index: str, body: dict[str, object]) -> dict[str, object]:
        hits = [
            {"_id": document_id, "_score": 1.0, "_source": document}
            for (doc_index, document_id), document in sorted(self.documents.items())
            if doc_index == index and _fake_query_matches(document, body)
        ]
        size = body.get("size", 10)
        limit = size if isinstance(size, int) and not isinstance(size, bool) else 10
        return {"hits": {"hits": hits[:limit]}}


@pytest.fixture(params=["local", "fake", "elasticsearch"])
def adapter(request: pytest.FixtureRequest) -> SearchAdapter:
    profile = str(request.param)
    if profile == "local":
        return LocalSearchAdapter()
    if profile == "fake":
        return FakeSearchAdapter()
    return ElasticsearchAdapter(
        ElasticsearchAdapterConfig(endpoint="http://search:9200"),
        client=FakeElasticsearchClient(),
    )


def test_search_adapter_contract_upsert_search_and_delete(adapter: SearchAdapter) -> None:
    document = SearchDocument(
        tenant_id="tenant-demo",
        object_type="Order",
        document_id="O-1001",
        version=1,
        properties={"status": "PENDING", "customer_id": "C-100", "operatorNote": "Expedite this order"},
    )

    adapter.configure_index(
        SearchIndexMapping(
            "tenant-demo",
            "Order",
            indexed_properties=("status",),
            searchable_properties=("operatorNote",),
        )
    )
    adapter.upsert_document(document)
    hits = adapter.search(SearchQuery(tenant_id="tenant-demo", object_type="Order", terms={"status": "PENDING"}))

    assert [hit.document_id for hit in hits] == ["O-1001"]
    assert hits[0].score == 1.0
    assert hits[0].document.version == 1
    assert adapter.document_ids(tenant_id="tenant-demo", object_type="Order") == ["O-1001"]
    text_hits = adapter.search(
        SearchQuery(
            tenant_id="tenant-demo",
            object_type="Order",
            terms={},
            text="expedite",
            searchable_properties=("operatorNote",),
        )
    )
    assert [hit.document_id for hit in text_hits] == ["O-1001"]

    adapter.delete_document(tenant_id="tenant-demo", object_type="Order", document_id="O-1001")
    assert adapter.search(SearchQuery(tenant_id="tenant-demo", object_type="Order", terms={"status": "PENDING"})) == []


def test_search_adapter_contract_ignores_stale_upsert(adapter: SearchAdapter) -> None:
    adapter.upsert_document(
        SearchDocument(
            tenant_id="tenant-demo",
            object_type="Order",
            document_id="O-1001",
            version=12,
            properties={"status": "APPROVED"},
        )
    )
    adapter.upsert_document(
        SearchDocument(
            tenant_id="tenant-demo",
            object_type="Order",
            document_id="O-1001",
            version=11,
            properties={"status": "PENDING"},
        )
    )

    hits = adapter.search(SearchQuery(tenant_id="tenant-demo", object_type="Order", terms={}))

    assert [hit.document_id for hit in hits] == ["O-1001"]
    assert hits[0].document.version == 12
    assert hits[0].document.properties["status"] == "APPROVED"


def test_search_adapter_contract_filters_tenant_object_type_and_limit(adapter: SearchAdapter) -> None:
    for document in [
        SearchDocument("tenant-demo", "Order", "O-1001", 1, {"status": "PENDING"}),
        SearchDocument("tenant-demo", "Order", "O-1002", 1, {"status": "PENDING"}),
        SearchDocument("tenant-other", "Order", "O-2001", 1, {"status": "PENDING"}),
        SearchDocument("tenant-demo", "Customer", "C-100", 1, {"status": "PENDING"}),
    ]:
        adapter.upsert_document(document)

    hits = adapter.search(
        SearchQuery(tenant_id="tenant-demo", object_type="Order", terms={"status": "PENDING"}, limit=1)
    )

    assert [hit.document_id for hit in hits] == ["O-1001"]


def _fake_query_matches(document: dict[str, object], body: dict[str, object]) -> bool:
    query = body.get("query")
    if not isinstance(query, dict):
        return True
    bool_query = query.get("bool")
    if not isinstance(bool_query, dict):
        return True
    return _matches_fake_filters(document, bool_query) and _matches_fake_must(document, bool_query)


def _matches_fake_filters(document: dict[str, object], bool_query: dict[str, object]) -> bool:
    filters = bool_query.get("filter")
    if not isinstance(filters, list):
        return True
    return all(_matches_fake_term(document, item) for item in filters)


def _matches_fake_term(document: dict[str, object], item: object) -> bool:
    if not isinstance(item, dict):
        return True
    term = item.get("term")
    if not isinstance(term, dict):
        return True
    return all(_fake_value(document, str(name)) == value for name, value in term.items())


def _matches_fake_must(document: dict[str, object], bool_query: dict[str, object]) -> bool:
    must = bool_query.get("must")
    if not isinstance(must, list):
        return True
    return all(_matches_fake_text(document, item) for item in must)


def _matches_fake_text(document: dict[str, object], item: object) -> bool:
    if not isinstance(item, dict) or "match_all" in item:
        return True
    multi_match = item.get("multi_match")
    if not isinstance(multi_match, dict):
        return True
    query = str(multi_match.get("query", "")).casefold()
    fields = multi_match.get("fields")
    return isinstance(fields, list) and any(
        query in str(_fake_value(document, str(field))).casefold() for field in fields
    )


def _fake_value(document: dict[str, object], dotted: str) -> object:
    current: object = document
    for part in dotted.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current
