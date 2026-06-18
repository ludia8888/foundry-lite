"""Elasticsearch live-cluster ratchet — real testcontainers Elasticsearch.

Runs the adapter against a real single-node Elasticsearch (testcontainers), so the
version-guarded update script, the configure→upsert→search round-trip, and a real
cluster outage are proven against the engine itself, not a fake.

vz/virtiofs note: Elasticsearch's refresh fsync on Colima's virtiofs disk is slow
enough to exceed client timeouts, which made earlier live round-trips hang. The
data directory is mounted on tmpfs (memory) so index/refresh I/O is fast and the
round-trip is reliable both locally and on CI. The deterministic classification
proofs live in test_elasticsearch_deployment_ratchet.py.
"""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any, cast
from uuid import uuid4

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.search_adapter import SearchDocument, SearchIndexMapping, SearchQuery
from foundry_lite.infrastructure.adapters import ElasticsearchAdapter, ElasticsearchAdapterConfig
from testcontainers.elasticsearch import ElasticSearchContainer

_IMAGE = "docker.elastic.co/elasticsearch/elasticsearch:9.0.0"


def _container() -> ElasticSearchContainer:
    return (
        ElasticSearchContainer(_IMAGE)
        .with_env("xpack.security.enabled", "false")
        .with_env("discovery.type", "single-node")
        .with_env("ES_JAVA_OPTS", "-Xms1g -Xmx1g")
        # tmpfs data dir: ES refresh fsync on the vz virtiofs disk is slow enough
        # to exceed client timeouts; a memory-backed data dir keeps writes fast.
        .with_kwargs(tmpfs={"/usr/share/elasticsearch/data": "rw,size=1g"})
    )


def _adapter(url: str) -> ElasticsearchAdapter:
    adapter = ElasticsearchAdapter(
        ElasticsearchAdapterConfig(endpoint=url, index_prefix=f"live{uuid4().hex[:6]}", request_timeout_seconds=30)
    )
    # readiness barrier (not a sleep): block until the cluster can allocate shards
    cast(Any, adapter.client).cluster.health(wait_for_status="yellow", timeout="60s")
    return adapter


def _mapping() -> SearchIndexMapping:
    return SearchIndexMapping(
        tenant_id="t1", object_type="Order", indexed_properties=("status",), searchable_properties=("note",)
    )


def _doc(version: int, status: str, note: str = "n") -> SearchDocument:
    return SearchDocument(
        tenant_id="t1",
        object_type="Order",
        document_id="o1",
        version=version,
        properties={"status": status, "note": note},
    )


def _query(**terms: str) -> SearchQuery:
    return SearchQuery(
        tenant_id="t1", object_type="Order", terms=terms, text=None, searchable_properties=("note",), limit=10
    )


@pytest.fixture(scope="module")
def live_url() -> Iterator[str]:
    container = _container()
    container.start()
    try:
        yield f"http://{container.get_container_host_ip()}:{container.get_exposed_port(9200)}"
    finally:
        container.stop()


def test_elasticsearch_live_round_trip_indexes_and_searches(live_url: str) -> None:
    adapter = _adapter(live_url)
    adapter.configure_index(_mapping())
    adapter.upsert_document(_doc(1, "OPEN", note="urgent shipment"))
    hits = adapter.search(_query(status="OPEN"))
    assert [hit.document_id for hit in hits] == ["o1"]
    assert hits[0].document.version == 1
    assert adapter.document_ids(tenant_id="t1", object_type="Order") == ["o1"]
    # full-text search through the real analyzer
    text_hits = adapter.search(
        SearchQuery(
            tenant_id="t1", object_type="Order", terms={}, text="shipment", searchable_properties=("note",), limit=10
        )
    )
    assert [hit.document_id for hit in text_hits] == ["o1"]


def test_elasticsearch_live_version_guard_rejects_stale_writer(live_url: str) -> None:
    adapter = _adapter(live_url)
    adapter.configure_index(_mapping())
    adapter.upsert_document(_doc(2, "NEW"))
    # the real painless version-guard script must drop a lower-version write
    adapter.upsert_document(_doc(1, "STALE"))
    assert adapter.search(_query())[0].document.properties["status"] == "NEW"
    adapter.upsert_document(_doc(3, "FRESH"))
    winner = adapter.search(_query())[0]
    assert winner.document.properties["status"] == "FRESH"
    assert winner.document.version == 3


def test_elasticsearch_live_version_guard_keeps_highest_version_under_concurrent_writers(live_url: str) -> None:
    adapter = _adapter(live_url)
    adapter.configure_index(_mapping())
    documents = [_doc(1, "STALE"), _doc(4, "V4"), _doc(2, "V2"), _doc(5, "WINNER"), _doc(3, "V3")]
    barrier = Barrier(len(documents))

    def write(document: SearchDocument) -> None:
        barrier.wait()
        adapter.upsert_document(document)

    with ThreadPoolExecutor(max_workers=len(documents)) as pool:
        for future in [pool.submit(write, document) for document in documents]:
            future.result()

    winner = adapter.search(_query())[0]
    assert winner.document.version == 5
    assert winner.document.properties["status"] == "WINNER"


def test_elasticsearch_live_cluster_outage_is_typed_adapter_error() -> None:
    # a dedicated cluster we stop mid-flight, so the outage does not affect the
    # shared module cluster; the adapter must surface a typed, retryable failure.
    container = _container()
    container.start()
    url = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(9200)}"
    adapter = _adapter(url)
    adapter.configure_index(_mapping())
    adapter.upsert_document(_doc(1, "OPEN"))
    container.stop()  # the cluster goes away
    with pytest.raises(AdapterError) as raised:
        adapter.search(_query())
    assert raised.value.failure.kind in {"timeout", "unavailable"}
    assert raised.value.failure.is_retryable is True
    assert raised.value.failure.adapter_profile == "elasticsearch"
