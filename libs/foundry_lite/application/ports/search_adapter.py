"""Application port contract for search adapter."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.application.ports.embedding_model import EmbeddingVector


@dataclass(frozen=True)
class SearchDocument:
    """Search-indexable object document.

    ``embedding`` mirrors Palantir's ``Vector`` object base type (L12a): an optional,
    model-pinned dense projection of the object's own ``searchable_properties`` text. It
    defaults empty so an object type with no embedding model wired indexes keyword-only,
    exactly as before. ``embedding_model_version`` pins the model that produced the vector
    so a query in a different vector space fails closed (never silently comparable).
    """

    tenant_id: str
    object_type: str
    document_id: str
    version: int
    properties: Mapping[str, object]
    embedding: EmbeddingVector = ()
    embedding_model_version: str = ""


@dataclass(frozen=True)
class SearchIndexMapping:
    """Ontology-derived index mapping for one searchable object type."""

    tenant_id: str
    object_type: str
    indexed_properties: tuple[str, ...] = ()
    searchable_properties: tuple[str, ...] = ()


@dataclass(frozen=True)
class SearchQuery:
    """Vendor-neutral object search query used by local and Elasticsearch adapters.

    When ``query_vector`` is set the adapter runs a ``nearestNeighbors`` semantic pass
    (Palantir ``Objects.search().nearestNeighbors(...).orderByRelevance()``) instead of the
    keyword/structured path. ``embedding_model_version`` must match the indexed generation's
    model or the adapter fails closed — index-time and query-time model pinning.
    """

    tenant_id: str
    object_type: str
    terms: Mapping[str, object]
    text: str | None = None
    searchable_properties: tuple[str, ...] = ()
    limit: int = 20
    query_vector: EmbeddingVector | None = None
    embedding_model_version: str = ""


@dataclass(frozen=True)
class SearchHit:
    """Search result with adapter-specific score normalized to a float."""

    document_id: str
    score: float
    document: SearchDocument


class SearchIndexAdapter(Protocol):
    """Scale Foundation boundary for future Elasticsearch-style object search."""

    @property
    def profile_name(self) -> str: ...

    def failure_contract(self) -> AdapterFailureContract:
        """Return the adapter failure taxonomy promised by this profile."""
        ...

    def upsert_document(self, document: SearchDocument) -> None:
        """Insert or replace one search document."""
        ...

    def configure_index(self, mapping: SearchIndexMapping) -> None:
        """Create or update the searchable/indexed field mapping."""
        ...

    def delete_document(self, *, tenant_id: str, object_type: str, document_id: str) -> None:
        """Delete one search document when present."""
        ...

    def document_ids(self, *, tenant_id: str, object_type: str) -> list[str]:
        """Return indexed document ids for drift/orphan detection."""
        ...

    def search(self, query: SearchQuery) -> list[SearchHit]:
        """Return matching documents in deterministic adapter order."""
        ...


SearchAdapter = SearchIndexAdapter
