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
class SearchQuery:
    """Vendor-neutral exact-match search query used by local contract tests."""

    tenant_id: str
    object_type: str
    terms: Mapping[str, object]
    limit: int = 20


@dataclass(frozen=True)
class SearchHit:
    """Search result with adapter-specific score normalized to a float."""

    document_id: str
    score: float
    document: SearchDocument


class SearchAdapter(Protocol):
    """Scale Foundation boundary for future OpenSearch-style object search."""

    @property
    def profile_name(self) -> str: ...

    def failure_contract(self) -> AdapterFailureContract:
        """Return the adapter failure taxonomy promised by this profile."""
        ...

    def upsert_document(self, document: SearchDocument) -> None:
        """Insert or replace one search document."""
        ...

    def delete_document(self, *, tenant_id: str, object_type: str, document_id: str) -> None:
        """Delete one search document when present."""
        ...

    def search(self, query: SearchQuery) -> list[SearchHit]:
        """Return matching documents in deterministic adapter order."""
        ...
