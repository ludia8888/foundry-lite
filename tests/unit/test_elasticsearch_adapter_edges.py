from __future__ import annotations

from types import SimpleNamespace

import foundry_lite.infrastructure.adapters.elasticsearch_search as elasticsearch_module
import pytest
from foundry_lite.application.ports.search_adapter import SearchIndexMapping, SearchQuery
from foundry_lite.infrastructure.adapters import ElasticsearchAdapter, ElasticsearchAdapterConfig

from tests.contracts.test_search_adapter_contract import FakeElasticsearchClient


def test_elasticsearch_configure_index_updates_existing_mapping() -> None:
    client = FakeElasticsearchClient()
    adapter = ElasticsearchAdapter(ElasticsearchAdapterConfig(endpoint="http://search:9200"), client=client)
    mapping = SearchIndexMapping(
        "tenant-demo",
        "Order",
        indexed_properties=("status",),
        searchable_properties=("operatorNote",),
    )

    adapter.configure_index(mapping)
    adapter.configure_index(mapping)

    assert "foundry-lite-tenant-demo-order" in client.indices.created


def test_elasticsearch_lazy_client_uses_configured_auth_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeElasticsearch:
        def __init__(self, **kwargs: object) -> None:
            calls.append(kwargs)

    monkeypatch.setattr(
        elasticsearch_module.importlib,
        "import_module",
        lambda name: SimpleNamespace(Elasticsearch=FakeElasticsearch),
    )

    adapter = ElasticsearchAdapter(
        ElasticsearchAdapterConfig(
            endpoint="http://search:9200",
            username="search-user",
            password="search-pass",
            request_timeout_seconds=7,
        )
    )

    assert adapter.client is adapter.client
    assert calls == [
        {
            "hosts": ["http://search:9200"],
            "basic_auth": ("search-user", "search-pass"),
            "request_timeout": 7,
        }
    ]


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"hits": {"hits": "not-a-list"}},
    ],
)
def test_elasticsearch_raw_hits_rejects_malformed_responses(response: dict[str, object]) -> None:
    assert elasticsearch_module._raw_hits(response) == []


def test_elasticsearch_search_hit_defaults_malformed_score_to_zero() -> None:
    query = SearchQuery(tenant_id="tenant-demo", object_type="Order", terms={})

    hit = elasticsearch_module._search_hit({"_score": "not-a-number", "_source": {"object_id": "O-1"}}, query)

    assert hit.score == 0.0
    assert hit.document_id == "O-1"
