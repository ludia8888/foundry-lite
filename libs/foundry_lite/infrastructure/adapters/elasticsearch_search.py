"""Elasticsearch-compatible adapter for object search projections."""

from __future__ import annotations

import hashlib
import importlib
import re
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol, cast

from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureKind,
    AdapterFailureMode,
)
from foundry_lite.application.ports.search_adapter import (
    SearchDocument,
    SearchHit,
    SearchIndexMapping,
    SearchQuery,
)

INDEX_TOKEN_PATTERN = re.compile(r"[^a-z0-9_-]+")
_RESOURCE_ALREADY_EXISTS = "resource_already_exists_exception"


class ElasticsearchIndicesLike(Protocol):
    def exists(self, *, index: str) -> bool: ...

    def create(self, *, index: str, body: Mapping[str, object]) -> object: ...

    def put_mapping(self, *, index: str, body: Mapping[str, object]) -> object: ...


class ElasticsearchClientLike(Protocol):
    indices: ElasticsearchIndicesLike

    def index(self, *, index: str, id: str, body: Mapping[str, object], **_options: object) -> object: ...

    def update(self, *, index: str, id: str, body: Mapping[str, object], **_options: object) -> object: ...

    def delete(self, *, index: str, id: str, **_options: object) -> object: ...

    def search(self, *, index: str, body: Mapping[str, object]) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class ElasticsearchAdapterConfig:
    endpoint: str
    index_prefix: str = "foundry-lite"
    username: str | None = None
    password: str | None = None
    request_timeout_seconds: int = 30
    # Idempotent operations (indexed-by-id upserts, scripted updates, reads) are
    # safe to retry; retrying on a dropped response heals transient transport
    # blips without duplicating side effects.
    max_retries: int = 3


class ElasticsearchAdapter:
    """Search adapter that keeps Elasticsearch as a rebuildable projection."""

    profile_name = "elasticsearch"

    def __init__(self, config: ElasticsearchAdapterConfig, *, client: ElasticsearchClientLike | None = None) -> None:
        self.config = config
        self._client = client

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode("configure_index", "unavailable", True, "Elasticsearch mapping update failed."),
                AdapterFailureMode("upsert_document", "timeout", True, "Elasticsearch document upsert timed out.", 30),
                AdapterFailureMode("delete_document", "unavailable", True, "Elasticsearch document delete failed."),
                AdapterFailureMode("document_ids", "timeout", True, "Elasticsearch consistency scan timed out.", 30),
                AdapterFailureMode("search", "timeout", True, "Elasticsearch query timed out.", 30),
            ),
        )

    def configure_index(self, mapping: SearchIndexMapping) -> None:
        index_name = self._index_name(mapping.tenant_id, mapping.object_type)
        body = {"mappings": _index_mappings(mapping)}
        with self._guard("configure_index"):
            if self.client.indices.exists(index=index_name):
                self.client.indices.put_mapping(index=index_name, body=body["mappings"])
                return
            self._create_index(index_name, body)

    def _create_index(self, index_name: str, body: Mapping[str, object]) -> None:
        try:
            self.client.indices.create(index=index_name, body=body)
        except Exception as exc:  # noqa: BLE001 - classified below
            # Idempotent: a concurrent create or a retried request whose response
            # was lost both surface as already-exists; the index is present.
            if _is_already_exists(exc):
                return
            raise

    def upsert_document(self, document: SearchDocument) -> None:
        with self._guard("upsert_document"):
            self.client.update(
                index=self._index_name(document.tenant_id, document.object_type),
                id=_physical_document_id(document.tenant_id, document.object_type, document.document_id),
                body=_version_guarded_update_body(document),
                retry_on_conflict=5,
                refresh=True,
            )

    def delete_document(self, *, tenant_id: str, object_type: str, document_id: str) -> None:
        with self._guard("delete_document"):
            self.client.delete(
                index=self._index_name(tenant_id, object_type),
                id=_physical_document_id(tenant_id, object_type, document_id),
                ignore_status=(404,),
                refresh=True,
            )

    def document_ids(self, *, tenant_id: str, object_type: str) -> list[str]:
        with self._guard("document_ids"):
            response = self.client.search(
                index=self._index_name(tenant_id, object_type),
                body={
                    "query": {
                        "bool": {
                            "filter": [
                                {"term": {"tenant_id": tenant_id}},
                                {"term": {"object_type": object_type}},
                            ]
                        }
                    },
                    "_source": ["object_id"],
                    "size": 10_000,
                },
            )
        return sorted(_hit_document_id(hit) for hit in _raw_hits(response) if _hit_document_id(hit))

    def search(self, query: SearchQuery) -> list[SearchHit]:
        with self._guard("search"):
            response = self.client.search(
                index=self._index_name(query.tenant_id, query.object_type),
                body=_search_body(query),
            )
        return [_search_hit(hit, query) for hit in _raw_hits(response)]

    @contextmanager
    def _guard(self, operation: str) -> Generator[None]:
        """Convert raw Elasticsearch transport/API errors into typed AdapterErrors.

        The failure_contract promises a stable taxonomy; without this, a live
        cluster outage would leak elastic_transport/elasticsearch exceptions and
        break the operator-evidence and retry contracts. Cluster-unavailability
        keeps search a rebuildable projection — it never corrupts serving truth.
        """
        try:
            yield
        except AdapterError:
            raise
        except Exception as exc:  # noqa: BLE001 - re-raised as a classified AdapterError
            raise _classify_es_error(self.profile_name, operation, exc) from exc

    @property
    def client(self) -> ElasticsearchClientLike:
        if self._client is None:
            self._client = _build_client(self.config)
        return self._client

    def _index_name(self, tenant_id: str, object_type: str) -> str:
        prefix = _index_token(self.config.index_prefix)
        tenant = _namespace_token(tenant_id)
        object_name = _namespace_token(object_type)
        return f"{prefix}-{tenant}-{object_name}"


def _build_client(config: ElasticsearchAdapterConfig) -> ElasticsearchClientLike:
    module = importlib.import_module("elasticsearch")
    client_type = cast(Callable[..., object], module.Elasticsearch)
    auth = (config.username, config.password) if config.username and config.password else None
    client = client_type(
        hosts=[config.endpoint],
        basic_auth=auth,
        request_timeout=config.request_timeout_seconds,
        retry_on_timeout=True,
        max_retries=config.max_retries,
    )
    return cast(ElasticsearchClientLike, client)


def _is_already_exists(exc: Exception) -> bool:
    """True when an index-create failed only because the index already exists."""
    return _RESOURCE_ALREADY_EXISTS in str(getattr(exc, "body", "")) or _RESOURCE_ALREADY_EXISTS in str(exc)


def _classify_es_error(profile: str, operation: str, exc: Exception) -> AdapterError:
    """Map an Elasticsearch transport/API exception to a typed, classified AdapterError."""
    transport = importlib.import_module("elastic_transport")
    kind, retryable, timeout = _es_failure_kind(transport, exc)
    return AdapterError(
        AdapterFailure(
            adapter_profile=profile,
            operation=operation,
            kind=kind,
            is_retryable=retryable,
            operator_message=f"Elasticsearch {operation} failed ({kind}): {exc}",
            timeout_seconds=timeout,
            details={"exceptionType": type(exc).__name__},
        )
    )


def _es_failure_kind(transport: object, exc: Exception) -> tuple[AdapterFailureKind, bool, int | None]:
    connection_timeout = getattr(transport, "ConnectionTimeout", ())
    connection_error = getattr(transport, "ConnectionError", ())
    api_error = getattr(transport, "ApiError", ())
    if isinstance(exc, connection_timeout):
        return "timeout", True, 30
    if isinstance(exc, api_error):
        return _api_error_kind(exc)
    if isinstance(exc, connection_error):
        # Connection refused / DNS / TLS — the cluster is unreachable, not wrong.
        return "unavailable", True, None
    return "unknown", False, None


def _api_error_kind(exc: Exception) -> tuple[AdapterFailureKind, bool, int | None]:
    status = getattr(getattr(exc, "meta", None), "status", None)
    if status == 429:
        return "rate_limited", True, None
    if isinstance(status, int) and status >= 500:
        return "unavailable", True, None
    if status == 409:
        return "conflict", False, None
    return "validation", False, None


def _index_token(value: str) -> str:
    normalized = INDEX_TOKEN_PATTERN.sub("-", value.casefold()).strip("-")
    return normalized or "default"


def _namespace_token(value: str) -> str:
    return f"{_index_token(value)}-{_stable_hash(value)}"


def _physical_document_id(tenant_id: str, object_type: str, document_id: str) -> str:
    tenant = _stable_hash(tenant_id)
    object_name = _stable_hash(object_type)
    document = _stable_hash(document_id)
    return f"{tenant}:{object_name}:{document}"


def _stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


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


def _version_guarded_update_body(document: SearchDocument) -> Mapping[str, object]:
    body = _document_body(document)
    return {
        "script": {
            "source": (
                "if (ctx._source.version == null || params.version > ctx._source.version) "
                "{ ctx._source.clear(); ctx._source.putAll(params.document); } "
                "else { ctx.op = 'noop'; }"
            ),
            "params": {"version": document.version, "document": body},
        },
        "upsert": body,
    }


def _search_body(query: SearchQuery) -> Mapping[str, object]:
    filters = [
        {"term": {"tenant_id": query.tenant_id}},
        {"term": {"object_type": query.object_type}},
        *({"term": {f"properties.{name}": value}} for name, value in query.terms.items()),
    ]
    must = [_full_text_query(query)] if query.text else [{"match_all": {}}]
    return {"query": {"bool": {"filter": filters, "must": must}}, "size": query.limit}


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
