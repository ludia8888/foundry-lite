"""Elasticsearch content-index adapter (Media/Content Plane M3, profile elasticsearch).

Keeps the content search index a rebuildable projection: one index per generation, an
``active`` alias swapped atomically on promotion (shadow-then-switch), and version-guarded
upserts so a stale re-index never overwrites a newer projection. Transport/API errors are
classified into the typed AdapterError taxonomy (shared with the object-search adapter).
The DB content_units rows remain the authoritative truth re-read at retrieval time.
"""

from __future__ import annotations

import re
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from typing import Any

from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureMode,
)
from foundry_lite.application.ports.content_index import (
    ContentIndexBatch,
    ContentIndexBatchResult,
    ContentIndexSchema,
    ContentSearchHit,
    HybridContentQuery,
    IndexedContentUnit,
)
from foundry_lite.infrastructure.adapters.elasticsearch_search import ElasticsearchAdapterConfig
from foundry_lite.infrastructure.adapters.es_client import build_es_client, classify_es_error

_TOKEN = re.compile(r"[^a-z0-9_-]+")
_NOT_FOUND = "index_not_found_exception"


class ElasticsearchContentIndexAdapter:
    """`ContentIndexAdapter` backed by Elasticsearch (lexical-first; dense/hybrid is M8)."""

    profile_name = "elasticsearch-content"

    def __init__(self, config: ElasticsearchAdapterConfig, *, client: Any | None = None) -> None:
        self.config = config
        self._client = client

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode("configure_generation", "unavailable", True, "Content index mapping update failed."),
                AdapterFailureMode("upsert_units", "timeout", True, "Content unit upsert timed out.", 30),
                AdapterFailureMode("delete_source_version", "unavailable", True, "Content unit delete failed."),
                AdapterFailureMode("search", "timeout", True, "Content search timed out.", 30),
                AdapterFailureMode("promote_generation", "conflict", False, "Content index alias swap conflict."),
            ),
        )

    def configure_generation(self, schema: ContentIndexSchema) -> None:
        index = self._index_name(schema.generation)
        with self._guard("configure_generation"):
            if not self.client.indices.exists(index=index):
                self.client.indices.create(index=index, body={"mappings": _MAPPINGS})

    def upsert_units(self, batch: ContentIndexBatch) -> ContentIndexBatchResult:
        index = self._index_name(batch.generation)
        indexed = 0
        failed = 0
        with self._guard("upsert_units"):
            for unit in batch.units:
                if not unit.content_unit_id or not unit.text_hash:
                    failed += 1
                    continue
                self.client.update(index=index, id=unit.content_unit_id, body=_version_guarded_body(unit), refresh=True)
                indexed += 1
        return ContentIndexBatchResult(indexed=indexed, failed=failed)

    def delete_source_version(self, source_version_id: str) -> None:
        with self._guard("delete_source_version"):
            self.client.delete_by_query(
                index=f"{self._prefix()}-content-*",
                body={"query": {"term": {"source_media_item_version_id": source_version_id}}},
                refresh=True,
                ignore_status=(404,),
            )

    def search(self, query: HybridContentQuery) -> list[ContentSearchHit]:
        try:
            response = self.client.search(index=self._alias(), body=_search_body(query))
        except Exception as exc:  # noqa: BLE001 - missing alias = empty projection, not an error
            if _is_not_found(exc):
                return []
            raise classify_es_error(self.profile_name, "search", exc) from exc
        return [_hit(raw) for raw in _raw_hits(response)]

    def promote_generation(self, expected_active: str, shadow: str) -> None:
        with self._guard("promote_generation"):
            if not self.client.indices.exists(index=self._index_name(shadow)):
                raise AdapterError(_conflict(self.profile_name, f"unknown shadow generation: {shadow}"))
            current = self._active_generation()
            if current != expected_active:
                raise AdapterError(
                    _conflict(self.profile_name, f"active moved (expected {expected_active!r}, found {current!r})")
                )
            actions: list[Mapping[str, object]] = []
            if current:
                actions.append({"remove": {"index": self._index_name(current), "alias": self._alias()}})
            actions.append({"add": {"index": self._index_name(shadow), "alias": self._alias()}})
            self.client.indices.update_aliases(body={"actions": actions})

    def _active_generation(self) -> str:
        try:
            aliased = self.client.indices.get_alias(name=self._alias())
        except Exception as exc:  # noqa: BLE001 - no alias yet = no active generation
            if _is_not_found(exc):
                return ""
            raise classify_es_error(self.profile_name, "promote_generation", exc) from exc
        for index in aliased:
            return _generation_from_index(str(index), self._prefix())
        return ""

    @contextmanager
    def _guard(self, operation: str) -> Generator[None]:
        try:
            yield
        except AdapterError:
            raise
        except Exception as exc:  # noqa: BLE001 - re-raised as a classified AdapterError
            raise classify_es_error(self.profile_name, operation, exc) from exc

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = build_es_client(
                endpoint=self.config.endpoint,
                username=self.config.username,
                password=self.config.password,
                request_timeout_seconds=self.config.request_timeout_seconds,
                max_retries=self.config.max_retries,
            )
        return self._client

    def _prefix(self) -> str:
        return _index_token(self.config.index_prefix)

    def _index_name(self, generation: str) -> str:
        return f"{self._prefix()}-content-{_index_token(generation)}"

    def _alias(self) -> str:
        return f"{self._prefix()}-content-active"


_MAPPINGS: Mapping[str, object] = {
    "properties": {
        "tenant_id": {"type": "keyword"},
        "source_media_item_version_id": {"type": "keyword"},
        "content_unit_id": {"type": "keyword"},
        "version": {"type": "long"},
        "text": {"type": "text"},
        "text_hash": {"type": "keyword"},
        "page_number": {"type": "long"},
        "start_ms": {"type": "long"},
        "end_ms": {"type": "long"},
        "chunk_spec_hash": {"type": "keyword"},
        "classification": {"type": "keyword"},
        "embedding_model_version": {"type": "keyword"},
        "embedding": {"type": "dense_vector"},
    }
}


def _conflict(profile: str, reason: str) -> AdapterFailure:
    return AdapterFailure(profile, "promote_generation", "conflict", False, f"content index promote: {reason}")


def _document_body(unit: IndexedContentUnit) -> Mapping[str, object]:
    return {
        "tenant_id": unit.tenant_id,
        "source_media_item_version_id": unit.source_media_item_version_id,
        "content_unit_id": unit.content_unit_id,
        "version": unit.version,
        "text": unit.text,
        "text_hash": unit.text_hash,
        "page_number": unit.page_number,
        "start_ms": unit.start_ms,
        "end_ms": unit.end_ms,
        "chunk_spec_hash": unit.chunk_spec_hash,
        "classification": unit.classification,
        "embedding_model_version": unit.embedding_model_version,
        "embedding": list(unit.embedding),
    }


def _version_guarded_body(unit: IndexedContentUnit) -> Mapping[str, object]:
    body = _document_body(unit)
    return {
        "script": {
            "source": (
                "if (ctx._source.version == null || params.version >= ctx._source.version) "
                "{ ctx._source.clear(); ctx._source.putAll(params.document); } "
                "else { ctx.op = 'noop'; }"
            ),
            "params": {"version": unit.version, "document": body},
        },
        "upsert": body,
    }


def _search_body(query: HybridContentQuery) -> Mapping[str, object]:
    filters: list[Mapping[str, object]] = [{"term": {"tenant_id": query.tenant_id}}]
    # AIP P0a: compile the caller's allowed-classification set into the query as a PRE-filter so
    # the engine "returns only" permitted units — over-classified candidates never enter the kNN
    # or the lexical/score pass. ``None`` = full clearance, so no term is added (back-compat).
    if query.allowed_classifications is not None:
        filters.append({"terms": {"classification": list(query.allowed_classifications)}})
    if query.query_vector is not None:
        return _dense_search_body(query, filters)
    must = [{"match": {"text": query.text}}] if query.text else [{"match_all": {}}]
    return {"query": {"bool": {"filter": filters, "must": must}}, "size": query.top_k}


def _dense_search_body(query: HybridContentQuery, filters: list[Mapping[str, object]]) -> Mapping[str, object]:
    scoped = [*filters, {"term": {"embedding_model_version": query.embedding_model_version or ""}}]
    return {
        "query": {
            "script_score": {
                "query": {"bool": {"filter": scoped}},
                "script": {
                    "source": "cosineSimilarity(params.query_vector, 'embedding') + 1.0",
                    "params": {"query_vector": list(query.query_vector or ())},
                },
            }
        },
        "size": query.top_k,
    }


def _raw_hits(response: Mapping[str, object]) -> list[Mapping[str, object]]:
    hits = response.get("hits")
    if not isinstance(hits, Mapping):
        return []
    raw = hits.get("hits")
    if not isinstance(raw, list):
        return []
    return [hit for hit in raw if isinstance(hit, Mapping)]


def _hit(raw: Mapping[str, object]) -> ContentSearchHit:
    source = raw.get("_source")
    source = source if isinstance(source, Mapping) else {}
    return ContentSearchHit(
        source_media_item_version_id=str(source.get("source_media_item_version_id", "")),
        content_unit_id=str(source.get("content_unit_id", "")),
        index_generation=str(raw.get("_index", "")),
        page_number=_opt_int(source.get("page_number")),
        start_ms=_opt_int(source.get("start_ms")),
        end_ms=_opt_int(source.get("end_ms")),
        text_hash=str(source.get("text_hash", "")) or None,
    )


def _opt_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _is_not_found(exc: Exception) -> bool:
    status = getattr(getattr(exc, "meta", None), "status", None)
    return status == 404 or _NOT_FOUND in str(getattr(exc, "body", "")) or _NOT_FOUND in str(exc)


def _index_token(value: str) -> str:
    normalized = _TOKEN.sub("-", value.casefold()).strip("-")
    return normalized or "default"


def _generation_from_index(index: str, prefix: str) -> str:
    marker = f"{prefix}-content-"
    return index[len(marker) :] if index.startswith(marker) else index
