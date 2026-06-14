"""OpenSearch-compatible adapter for object search projections."""

from __future__ import annotations

import importlib
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract, AdapterFailureMode
from foundry_lite.application.ports.search_adapter import (
    SearchDocument,
    SearchHit,
    SearchIndexMapping,
    SearchQuery,
)

INDEX_TOKEN_PATTERN = re.compile(r"[^a-z0-9_-]+")


class OpenSearchIndicesLike(Protocol):
    def exists(self, *, index: str) -> bool: ...

    def create(self, *, index: str, body: Mapping[str, object]) -> object: ...

    def put_mapping(self, *, index: str, body: Mapping[str, object]) -> object: ...


class OpenSearchClientLike(Protocol):
    indices: OpenSearchIndicesLike

    def index(self, *, index: str, id: str, body: Mapping[str, object], **_options: object) -> object: ...

    def delete(self, *, index: str, id: str, **_options: object) -> object: ...

    def search(self, *, index: str, body: Mapping[str, object], size: int) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class OpenSearchAdapterConfig:
    endpoint: str
    index_prefix: str = "foundry-lite"
    username: str | None = None
    password: str | None = None
    request_timeout_seconds: int = 30


class OpenSearchAdapter:
    """Search adapter that keeps OpenSearch as a rebuildable projection."""

    profile_name = "opensearch"

    def __init__(self, config: OpenSearchAdapterConfig, *, client: OpenSearchClientLike | None = None) -> None:
        self.config = config
        self._client = client

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode("configure_index", "unavailable", True, "OpenSearch mapping update failed."),
                AdapterFailureMode("upsert_document", "timeout", True, "OpenSearch document upsert timed out.", 30),
                AdapterFailureMode("delete_document", "unavailable", True, "OpenSearch document delete failed."),
                AdapterFailureMode("document_ids", "timeout", True, "OpenSearch consistency scan timed out.", 30),
                AdapterFailureMode("search", "timeout", True, "OpenSearch query timed out.", 30),
            ),
        )

    def configure_index(self, mapping: SearchIndexMapping) -> None:
        index_name = self._index_name(mapping.tenant_id, mapping.object_type)
        body = {"mappings": _index_mappings(mapping)}
        if self.client.indices.exists(index=index_name):
            self.client.indices.put_mapping(index=index_name, body=body["mappings"])
            return
        self.client.indices.create(index=index_name, body=body)

    def upsert_document(self, document: SearchDocument) -> None:
        self.client.index(
            index=self._index_name(document.tenant_id, document.object_type),
            id=document.document_id,
            body=_document_body(document),
            refresh=True,
        )

    def delete_document(self, *, tenant_id: str, object_type: str, document_id: str) -> None:
        self.client.delete(index=self._index_name(tenant_id, object_type), id=document_id, ignore=(404,), refresh=True)

    def document_ids(self, *, tenant_id: str, object_type: str) -> list[str]:
        response = self.client.search(
            index=self._index_name(tenant_id, object_type),
            body={"query": {"match_all": {}}, "_source": ["object_id"]},
            size=10_000,
        )
        return sorted(_hit_document_id(hit) for hit in _raw_hits(response) if _hit_document_id(hit))

    def search(self, query: SearchQuery) -> list[SearchHit]:
        response = self.client.search(
            index=self._index_name(query.tenant_id, query.object_type),
            body=_search_body(query),
            size=query.limit,
        )
        return [_search_hit(hit, query) for hit in _raw_hits(response)]

    @property
    def client(self) -> OpenSearchClientLike:
        if self._client is None:
            self._client = _build_client(self.config)
        return self._client

    def _index_name(self, tenant_id: str, object_type: str) -> str:
        prefix = _index_token(self.config.index_prefix)
        tenant = _index_token(tenant_id)
        object_name = _index_token(object_type)
        return f"{prefix}-{tenant}-{object_name}"


def _build_client(config: OpenSearchAdapterConfig) -> OpenSearchClientLike:
    module = importlib.import_module("opensearchpy")
    client_type = cast(Callable[..., object], module.OpenSearch)
    auth = (config.username, config.password) if config.username and config.password else None
    client = client_type(hosts=[config.endpoint], http_auth=auth, request_timeout=config.request_timeout_seconds)
    return cast(OpenSearchClientLike, client)


def _index_token(value: str) -> str:
    normalized = INDEX_TOKEN_PATTERN.sub("-", value.casefold()).strip("-")
    return normalized or "default"


def _index_mappings(mapping: SearchIndexMapping) -> Mapping[str, object]:
    field_mappings: dict[str, object] = {}
    for name in mapping.indexed_properties:
        field_mappings[name] = {"type": "keyword"}
    for name in mapping.searchable_properties:
        field_mappings[name] = {"type": "text"}
    return {
        "properties": {
            "tenant_id": {"type": "keyword"},
            "object_type": {"type": "keyword"},
            "object_id": {"type": "keyword"},
            "version": {"type": "long"},
            "properties": {"properties": field_mappings},
        }
    }


def _document_body(document: SearchDocument) -> Mapping[str, object]:
    return {
        "tenant_id": document.tenant_id,
        "object_type": document.object_type,
        "object_id": document.document_id,
        "version": document.version,
        "properties": dict(document.properties),
    }


def _search_body(query: SearchQuery) -> Mapping[str, object]:
    filters = [
        {"term": {"tenant_id": query.tenant_id}},
        {"term": {"object_type": query.object_type}},
        *({"term": {f"properties.{name}": value}} for name, value in query.terms.items()),
    ]
    must = [_full_text_query(query)] if query.text else [{"match_all": {}}]
    return {"query": {"bool": {"filter": filters, "must": must}}}


def _full_text_query(query: SearchQuery) -> Mapping[str, object]:
    fields = [f"properties.{name}" for name in query.searchable_properties]
    return {"multi_match": {"query": query.text or "", "fields": fields}}


def _raw_hits(response: Mapping[str, object]) -> list[Mapping[str, object]]:
    hits = response.get("hits")
    if not isinstance(hits, Mapping):
        return []
    raw = hits.get("hits")
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        return []
    return [hit for hit in raw if isinstance(hit, Mapping)]


def _hit_source(hit: Mapping[str, object]) -> Mapping[str, object]:
    source = hit.get("_source")
    return source if isinstance(source, Mapping) else {}


def _hit_document_id(hit: Mapping[str, object]) -> str:
    value = _hit_source(hit).get("object_id")
    return value if isinstance(value, str) else ""


def _search_hit(hit: Mapping[str, object], query: SearchQuery) -> SearchHit:
    source = _hit_source(hit)
    properties = source.get("properties")
    document = SearchDocument(
        tenant_id=query.tenant_id,
        object_type=query.object_type,
        document_id=_hit_document_id(hit),
        version=_hit_version(source),
        properties=properties if isinstance(properties, Mapping) else {},
    )
    return SearchHit(document_id=document.document_id, score=_score(hit), document=document)


def _hit_version(source: Mapping[str, object]) -> int:
    version = source.get("version")
    return version if isinstance(version, int) and not isinstance(version, bool) else 0


def _score(hit: Mapping[str, object]) -> float:
    score = hit.get("_score")
    if isinstance(score, int | float) and not isinstance(score, bool):
        return float(score)
    return 0.0
