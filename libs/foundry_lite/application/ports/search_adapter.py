from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract


@dataclass(frozen=True)
class SearchDocument:
    """Search-indexable object document."""

    tenant_id: str
    object_type: str
    document_id: str
    version: int
    properties: Mapping[str, object]


@dataclass(frozen=True)
class SearchIndexMapping:
    """Ontology-derived index mapping for one searchable object type."""

    tenant_id: str
    object_type: str
    indexed_properties: tuple[str, ...] = ()
    searchable_properties: tuple[str, ...] = ()


@dataclass(frozen=True)
class SearchQuery:
    """Vendor-neutral object search query used by local and OpenSearch adapters."""

    tenant_id: str
    object_type: str
    terms: Mapping[str, object]
    text: str | None = None
    searchable_properties: tuple[str, ...] = ()
    limit: int = 20


@dataclass(frozen=True)
class SearchHit:
    """Search result with adapter-specific score normalized to a float."""

    document_id: str
    score: float
    document: SearchDocument


class SearchIndexAdapter(Protocol):
    """Scale Foundation boundary for future OpenSearch-style object search."""

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
