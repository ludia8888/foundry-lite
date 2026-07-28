"""Application port contract for content index."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from foundry_lite.application.ports.embedding_model import EmbeddingVector


@dataclass(frozen=True)
class ContentIndexSchema:
    """Generation-scoped index schema. ``embedding_model_version`` pins the dense space of
    the generation (empty for a lexical-only generation)."""

    generation: str
    fields: tuple[str, ...] = ()
    embedding_model_version: str = ""


@dataclass(frozen=True)
class IndexedContentUnit:
    """One content unit projected into the index, with the citation it must return.

    ``version`` is monotonic per source version so a stale re-index never overwrites a
    newer projection (version-guarded upsert). The index stores only the projection; the
    DB content_units row remains the authoritative truth re-read at retrieval time.
    """

    tenant_id: str
    content_unit_id: str
    source_media_item_version_id: str
    text: str
    text_hash: str
    version: int = 0
    page_number: int | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    chunk_spec_hash: str = ""
    embedding: EmbeddingVector = ()
    embedding_model_version: str = ""
    # AIP P0a: the unit's classification, copied from its source content_unit's security_envelope
    # (a derived, model-pinned projection — never an independent authority). Stored AS a mandatory
    # control property ON the indexed record so the security predicate can be PRE-applied in the
    # query, before lexical/dense ranking. Empty for back-compat (unclassified projections).
    classification: str = ""


@dataclass(frozen=True)
class ContentIndexBatch:
    """A batch of content units to upsert into one index generation."""

    generation: str
    units: tuple[IndexedContentUnit, ...] = ()


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
    query_vector: EmbeddingVector | None = None
    embedding_model_version: str | None = None
    # AIP P0a: the caller's allowed-classification set, compiled into the query as a PRE-filter
    # (granular-policy → query template). ``None`` means full clearance — every classification is
    # permitted and behaviour is unchanged (back-compat). Otherwise the index returns ONLY units
    # whose classification is in this set, so over-classified candidates never enter the kNN/ranking
    # and cannot reach an LLM. Mirrors MediaReferenceBindingService.resolve's allowed_classifications.
    allowed_classifications: tuple[str, ...] | None = None


def is_classification_cleared(classification: str, allowed_classifications: tuple[str, ...] | None) -> bool:
    """The single content-plane classification-access rule (mirrors binding.resolve).

    ``None`` allowed-set = full clearance: every classification passes (back-compat — unchanged).
    Otherwise the unit's classification must be a member of the caller's allowed set. Reused by
    both index adapters (PRE-filter) and the authoritative re-read (defense-in-depth) so the
    predicate is defined in exactly one place.
    """
    if allowed_classifications is None:
        return True
    return classification in allowed_classifications


@dataclass(frozen=True)
class ContentSearchHit:
    """A search hit pinning the exact source version, content unit, and citation."""

    source_media_item_version_id: str
    content_unit_id: str
    index_generation: str
    media_derivative_id: str | None = None
    page_number: int | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    bbox: Mapping[str, object] | None = None
    timecode: Mapping[str, object] | None = None
    source_locator: Mapping[str, object] | None = None
    text_hash: str | None = None
    text: str = ""
    chunk_spec_hash: str = ""
    classification: str = ""


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
