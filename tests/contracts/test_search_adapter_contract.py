from __future__ import annotations

import pytest
from foundry_lite.application.ports.search_adapter import SearchAdapter, SearchDocument, SearchQuery
from foundry_lite.infrastructure.adapters import FakeSearchAdapter, LocalSearchAdapter


@pytest.fixture(params=[LocalSearchAdapter, FakeSearchAdapter])
def adapter(request: pytest.FixtureRequest) -> SearchAdapter:
    adapter_type = request.param
    return adapter_type()


def test_search_adapter_contract_upsert_search_and_delete(adapter: SearchAdapter) -> None:
    document = SearchDocument(
        tenant_id="tenant-demo",
        object_type="Order",
        document_id="O-1001",
        version=1,
        properties={"status": "PENDING", "customer_id": "C-100"},
    )

    adapter.upsert_document(document)
    hits = adapter.search(SearchQuery(tenant_id="tenant-demo", object_type="Order", terms={"status": "PENDING"}))

    assert [hit.document_id for hit in hits] == ["O-1001"]
    assert hits[0].score == 1.0
    assert hits[0].document.version == 1

    adapter.delete_document(tenant_id="tenant-demo", object_type="Order", document_id="O-1001")
    assert adapter.search(SearchQuery(tenant_id="tenant-demo", object_type="Order", terms={"status": "PENDING"})) == []


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
