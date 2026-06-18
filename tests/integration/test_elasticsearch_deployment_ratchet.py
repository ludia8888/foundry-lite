"""Elasticsearch deployment ratchet — failure/projection evidence (5th infra).

The ``ElasticsearchAdapter`` already existed (profile + projection contract); this
ratchet closes the live-cluster gap: a real cluster outage must surface as the
*typed* failure the adapter promises, search must stay a rebuildable projection,
and the version guard must hold under concurrent writers.

These proofs run against an in-memory ``FakeElasticsearchClient`` that models the
version-guarded update contract and raises the *real* ``elastic_transport`` /
``elasticsearch`` exception types (constructed below), so the adapter's
classification is exercised against the exact taxonomy a live cluster raises —
deterministically and fast, with no Docker. The real round-trip, the real
painless version-guard script, and a real cluster outage are proven against a
testcontainers Elasticsearch in ``test_elasticsearch_live_cluster.py``.

Proof classes (docs/infra-ratchet.md):
- adapter-contract     : declared search failure taxonomy
- normal-path          : configure → upsert → search round-trip
- failure-injection    : cluster timeout/unavailable/5xx/4xx → typed AdapterError
- concurrency-race     : version guard keeps the highest version under writers
- retry-idempotency    : already-exists create is idempotent; stale upsert is noop
- partial-success      : one document's failure does not lose the others
- recovery-cleanup     : index is a rebuildable projection after loss
- operator-evidence    : failure carries a durable, classified payload
"""

from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from typing import Any, cast

import elastic_transport as et
import elasticsearch as es
import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.search_adapter import SearchDocument, SearchIndexMapping, SearchQuery
from foundry_lite.infrastructure.adapters import ElasticsearchAdapter, ElasticsearchAdapterConfig
from foundry_lite.infrastructure.adapters.elasticsearch_search import ElasticsearchClientLike

_ALREADY_EXISTS = "resource_already_exists_exception"
_NODE = et.NodeConfig(scheme="http", host="fake", port=9200)


def _api_error(status: int, *, already_exists: bool = False) -> es.ApiError:
    meta = et.ApiResponseMeta(status=status, http_version="1.1", headers=et.HttpHeaders({}), duration=0.0, node=_NODE)
    error_type = _ALREADY_EXISTS if already_exists else "x"
    return es.ApiError("boom", meta=meta, body={"error": {"type": error_type}})


class _FakeIndices:
    def __init__(self, parent: FakeElasticsearchClient) -> None:
        self._parent = parent

    def exists(self, *, index: str) -> bool:
        self._parent.maybe_fail("indices.exists")
        return not self._parent.force_create and index in self._parent.index_names

    def create(self, *, index: str, body: Mapping[str, object]) -> object:
        self._parent.maybe_fail("indices.create")
        if index in self._parent.index_names:
            raise _api_error(400, already_exists=True)
        self._parent.index_names.add(index)
        return {"acknowledged": True}

    def put_mapping(self, *, index: str, body: Mapping[str, object]) -> object:
        self._parent.maybe_fail("indices.put_mapping")
        return {"acknowledged": True}


class FakeElasticsearchClient:
    """In-memory client modelling the version-guarded projection contract."""

    def __init__(self) -> None:
        self.indices = _FakeIndices(self)
        self.index_names: set[str] = set()
        self.docs: dict[tuple[str, str], dict[str, Any]] = {}
        self.force_create = False
        self._faults: dict[str, BaseException] = {}
        self._lock = Lock()

    def fail(self, operation: str, exc: BaseException) -> None:
        self._faults[operation] = exc

    def maybe_fail(self, operation: str) -> None:
        exc = self._faults.pop(operation, None)
        if exc is not None:
            raise exc

    def index(self, *, index: str, id: str, body: Mapping[str, object], **_options: object) -> object:
        self.maybe_fail("index")
        with self._lock:
            self.docs[(index, id)] = dict(body)
        return {"result": "created"}

    def update(self, *, index: str, id: str, body: Mapping[str, object], **_options: object) -> object:
        self.maybe_fail("update")
        with self._lock:
            params = cast(Mapping[str, Any], body["script"])["params"]
            key = (index, id)
            current = self.docs.get(key)
            if current is None:
                self.docs[key] = dict(cast(Mapping[str, Any], body["upsert"]))
                return {"result": "created"}
            if params["version"] > current.get("version", -1):
                self.docs[key] = dict(params["document"])
                return {"result": "updated"}
        return {"result": "noop"}

    def delete(self, *, index: str, id: str, **_options: object) -> object:
        self.maybe_fail("delete")
        with self._lock:
            self.docs.pop((index, id), None)
        return {"result": "deleted"}

    def search(self, *, index: str, body: Mapping[str, object]) -> Mapping[str, object]:
        self.maybe_fail("search")
        matched = [doc for (idx, _id), doc in sorted(self.docs.items()) if idx == index and _matches(doc, body)]
        size = body.get("size", 10)
        limit = size if isinstance(size, int) and not isinstance(size, bool) else 10
        return {"hits": {"hits": [{"_source": doc, "_score": 1.0} for doc in matched[:limit]]}}


def _matches(doc: Mapping[str, Any], body: Mapping[str, object]) -> bool:
    bool_query = cast(Mapping[str, Any], body.get("query", {})).get("bool")
    if bool_query is None:
        return True
    for clause in bool_query.get("filter", []):
        for field, value in clause.get("term", {}).items():
            if _field_value(doc, field) != value:
                return False
    return True


def _field_value(doc: Mapping[str, Any], field: str) -> object:
    if field.startswith("properties."):
        return doc.get("properties", {}).get(field.split(".", 1)[1])
    return doc.get(field)


def _adapter() -> tuple[ElasticsearchAdapter, FakeElasticsearchClient]:
    client = FakeElasticsearchClient()
    adapter = ElasticsearchAdapter(
        ElasticsearchAdapterConfig(endpoint="http://fake:9200"), client=cast(ElasticsearchClientLike, client)
    )
    return adapter, client


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


# -- adapter-contract -------------------------------------------------------


def test_elasticsearch_adapter_contract_declares_search_failure_modes() -> None:
    adapter, _ = _adapter()
    assert adapter.profile_name == "elasticsearch"
    contract = adapter.failure_contract()
    assert contract.adapter_profile == "elasticsearch"
    operations = {mode.operation for mode in contract.modes}
    assert {"configure_index", "upsert_document", "delete_document", "document_ids", "search"} <= operations
    assert all(mode.is_retryable for mode in contract.modes)


# -- normal-path ------------------------------------------------------------


def test_elasticsearch_normal_path_indexes_and_searches() -> None:
    adapter, _ = _adapter()
    adapter.configure_index(_mapping())
    adapter.upsert_document(_doc(1, "OPEN"))
    hits = adapter.search(_query(status="OPEN"))
    assert [hit.document_id for hit in hits] == ["o1"]
    assert hits[0].document.version == 1
    assert adapter.document_ids(tenant_id="t1", object_type="Order") == ["o1"]


# -- failure-injection (classification) -------------------------------------


@pytest.mark.parametrize(
    ("exc", "kind", "retryable"),
    [
        (et.ConnectionTimeout("timed out"), "timeout", True),
        (et.ConnectionError("connection refused"), "unavailable", True),
        (_api_error(503), "unavailable", True),
        (_api_error(429), "rate_limited", True),
        (_api_error(400), "validation", False),
        (_api_error(409), "conflict", False),
        (RuntimeError("unexpected client bug"), "unknown", False),
    ],
)
def test_elasticsearch_cluster_failures_map_to_typed_adapter_error(
    exc: BaseException, kind: str, retryable: bool
) -> None:
    adapter, client = _adapter()
    client.fail("search", exc)
    with pytest.raises(AdapterError) as raised:
        adapter.search(_query())
    failure = raised.value.failure
    assert failure.kind == kind
    assert failure.is_retryable is retryable
    assert failure.operation == "search"
    assert failure.adapter_profile == "elasticsearch"


# -- concurrency-race -------------------------------------------------------


def test_elasticsearch_version_guard_keeps_highest_version_under_concurrent_upserts() -> None:
    adapter, _ = _adapter()
    adapter.configure_index(_mapping())
    documents = [_doc(2, "V2"), _doc(1, "STALE"), _doc(5, "WINNER"), _doc(4, "V4"), _doc(3, "V3")]
    barrier = Barrier(len(documents))

    def write(document: SearchDocument) -> None:
        barrier.wait()
        adapter.upsert_document(document)

    with ThreadPoolExecutor(max_workers=len(documents)) as pool:
        for future in [pool.submit(write, document) for document in documents]:
            future.result()

    winner = adapter.search(_query())[0]
    assert winner.document.properties["status"] == "WINNER"
    assert winner.document.version == 5


# -- retry-idempotency ------------------------------------------------------


def test_elasticsearch_configure_index_is_idempotent_on_already_exists() -> None:
    adapter, client = _adapter()
    adapter.configure_index(_mapping())
    # a retried create whose first response was dropped: exists() reports false
    # but the index is really present, so create raises already-exists — benign.
    client.force_create = True
    adapter.configure_index(_mapping())  # must not raise


def test_elasticsearch_create_failure_other_than_already_exists_is_classified() -> None:
    adapter, client = _adapter()
    # a create that fails for a real reason (not already-exists) must surface as
    # a typed AdapterError, not leak the raw transport exception.
    client.force_create = True
    client.fail("indices.create", et.ConnectionError("cluster down during create"))
    with pytest.raises(AdapterError) as raised:
        adapter.configure_index(_mapping())
    assert raised.value.failure.operation == "configure_index"
    assert raised.value.failure.kind == "unavailable"


def test_elasticsearch_repeated_same_version_upsert_is_noop() -> None:
    adapter, _ = _adapter()
    adapter.configure_index(_mapping())
    adapter.upsert_document(_doc(5, "OPEN", note="first"))
    adapter.upsert_document(_doc(5, "OPEN", note="second"))  # same version -> noop guard
    assert adapter.search(_query())[0].document.properties["note"] == "first"


def test_elasticsearch_client_config_enables_retry_on_dropped_response() -> None:
    # The transport retries idempotent operations so a dropped response heals
    # without duplicating side effects (the Colima keep-alive failure mode).
    assert ElasticsearchAdapterConfig(endpoint="http://x:9200").max_retries >= 1


# -- partial-success --------------------------------------------------------


def test_elasticsearch_one_document_failure_does_not_lose_others() -> None:
    adapter, client = _adapter()
    adapter.configure_index(_mapping())
    adapter.upsert_document(_doc(1, "OPEN"))
    client.fail("update", et.ConnectionTimeout("timed out"))
    with pytest.raises(AdapterError):
        adapter.upsert_document(
            SearchDocument(tenant_id="t1", object_type="Order", document_id="o2", version=1, properties={"status": "X"})
        )
    # the first document survived the second document's failure
    assert adapter.document_ids(tenant_id="t1", object_type="Order") == ["o1"]


# -- recovery-cleanup (projection rebuild) ----------------------------------


def test_elasticsearch_index_is_rebuildable_projection_after_loss() -> None:
    adapter, client = _adapter()
    adapter.configure_index(_mapping())
    source_of_truth = [_doc(1, "OPEN", note="a")]
    for document in source_of_truth:
        adapter.upsert_document(document)
    # simulate index loss (cluster wipe): the projection is gone, truth is not
    client.docs.clear()
    assert adapter.document_ids(tenant_id="t1", object_type="Order") == []
    # rebuild the projection from the object store
    for document in source_of_truth:
        adapter.upsert_document(document)
    assert adapter.document_ids(tenant_id="t1", object_type="Order") == ["o1"]


# -- operator-evidence ------------------------------------------------------


def test_elasticsearch_failure_carries_durable_operator_payload() -> None:
    adapter, client = _adapter()
    client.fail("search", et.ConnectionTimeout("timed out"))
    with pytest.raises(AdapterError) as raised:
        adapter.search(_query())
    payload = raised.value.failure.to_payload()
    assert payload["adapterProfile"] == "elasticsearch"
    assert payload["operation"] == "search"
    assert payload["kind"] == "timeout"
    assert payload["retryable"] is True
    assert "Elasticsearch search failed" in str(payload["operatorMessage"])
