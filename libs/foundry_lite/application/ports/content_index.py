from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ContentIndexSchema:
    """Generation-scoped index schema (lexical first; dense-vector fields added later)."""

    generation: str
    fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContentIndexBatch:
    """A batch of content units to upsert into one index generation."""

    generation: str
    units: tuple[object, ...] = ()


@dataclass(frozen=True)
class ContentIndexBatchResult:
    """Outcome of one upsert batch; partial failures must be reported, not swallowed."""

    indexed: int
    failed: int = 0


@dataclass(frozen=True)
class HybridContentQuery:
    """Lexical/vector/hybrid query over content units, always tenant-scoped."""

    tenant_id: str
    text: str | None = None
    top_k: int = 10


@dataclass(frozen=True)
class ContentSearchHit:
    """A search hit pinning the exact source version, content unit, and citation."""

    source_media_item_version_id: str
    content_unit_id: str
    index_generation: str
    page_number: int | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    text_hash: str | None = None


class ContentIndexAdapter(Protocol):
    """Projection boundary for content-unit search, distinct from object-level SearchIndexAdapter.

    Contract only in this sprint. The index is a rebuildable projection of committed content
    artifacts (ADR-0001 invariant 5); generation promotion is shadow-then-switch.
    """

    def configure_generation(self, schema: ContentIndexSchema) -> None:
        """Create/configure one index generation."""
        ...

    def upsert_units(self, batch: ContentIndexBatch) -> ContentIndexBatchResult:
        """Upsert a batch of content units, reporting partial failures."""
        ...

    def delete_source_version(self, source_version_id: str) -> None:
        """Remove all projected units for one source media version."""
        ...

    def search(self, query: HybridContentQuery) -> list[ContentSearchHit]:
        """Return ranked content hits with exact citations."""
        ...

    def promote_generation(self, expected_active: str, shadow: str) -> None:
        """Atomically promote a validated shadow generation to active."""
        ...
