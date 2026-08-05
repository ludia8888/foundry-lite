"""In-memory content-index adapter (Media/Content Plane M3 + M8, local/test profile).

A deterministic, dependency-free `ContentIndexAdapter`: generations are isolated maps,
upserts are version-guarded (a stale re-index never overwrites a newer projection),
search is tenant-scoped over the ACTIVE generation, and promotion is shadow-then-switch
with a compare-and-set on the expected active generation. The index is a pure projection —
it can be dropped and rebuilt from committed content_units. M8 adds dense retrieval: when a
query carries a vector, lexical and cosine-kNN ranks are fused with Reciprocal Rank Fusion;
a query whose embedding model version does not match the generation's fails closed (no
silent mixing of vector spaces).
"""

from __future__ import annotations

import math

from foundry_lite.application.ports.adapter_failure import AdapterError, AdapterFailure, AdapterFailureContract
from foundry_lite.application.ports.content_index import (
    ContentIndexBatch,
    ContentIndexBatchResult,
    ContentIndexSchema,
    ContentSearchHit,
    HybridContentQuery,
    IndexedContentUnit,
    is_classification_cleared,
)
from foundry_lite.application.ports.embedding_model import EmbeddingVector

_RRF_K = 60


class LocalContentIndexAdapter:
    """Filesystem-free `ContentIndexAdapter` for the local/test profile."""

    profile_name = "local-content-index"

    def __init__(self) -> None:
        self._generations: dict[str, dict[str, IndexedContentUnit]] = {}
        self._model_version: dict[str, str] = {}
        self._active: str = ""

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=())

    def configure_generation(self, schema: ContentIndexSchema) -> None:
        self._generations.setdefault(schema.generation, {})
        self._model_version[schema.generation] = schema.embedding_model_version

    def upsert_units(self, batch: ContentIndexBatch) -> ContentIndexBatchResult:
        generation = self._generations.setdefault(batch.generation, {})
        indexed = 0
        failed = 0
        for unit in batch.units:
            if not unit.content_unit_id or not unit.text_hash:
                # A malformed unit is reported, never silently swallowed (partial failure).
                failed += 1
                continue
            existing = generation.get(unit.content_unit_id)
            if existing is not None and existing.version > unit.version:
                continue  # version guard: keep the newer projection
            generation[unit.content_unit_id] = unit
            indexed += 1
        return ContentIndexBatchResult(indexed=indexed, failed=failed)

    def delete_source_version(self, source_version_id: str) -> None:
        for generation in self._generations.values():
            stale = [uid for uid, unit in generation.items() if unit.source_media_item_version_id == source_version_id]
            for uid in stale:
                del generation[uid]

    def search(self, query: HybridContentQuery) -> list[ContentSearchHit]:
        generation = self._generations.get(self._active)
        if not generation:
            return []
        candidates = [
            unit
            for unit in generation.values()
            if unit.tenant_id == query.tenant_id
            and is_classification_cleared(unit.classification, query.allowed_classifications)
        ]
        if query.query_vector is None:
            ranked = _lexical_rank(candidates, query.text)
        elif query.text:
            self._guard_model_version(query)
            ranked = _fuse(_lexical_rank(candidates, query.text), _dense_rank(candidates, query.query_vector))
        else:
            # Pure kNN: a vector with no query text (e.g. a visual frame query) ranks by cosine
            # alone — fusing a tokenless lexical pass that matches every unit equally would let an
            # arbitrary tiebreak override the dense order.
            self._guard_model_version(query)
            ranked = _dense_rank(candidates, query.query_vector)
        return [_hit(unit, self._active) for unit in ranked[: query.top_k]]

    def _guard_model_version(self, query: HybridContentQuery) -> None:
        active_model = self._model_version.get(self._active, "")
        if active_model != (query.embedding_model_version or ""):
            raise self._error(
                "search",
                f"embedding model mismatch (index {active_model!r}, query {query.embedding_model_version!r})",
            )

    def active_generation(self) -> str:
        return self._active

    def promote_generation(self, expected_active: str, shadow: str) -> None:
        if shadow not in self._generations:
            raise self._error("promote_generation", f"unknown shadow generation: {shadow}")
        if self._active != expected_active:
            raise self._error(
                "promote_generation", f"active generation moved (expected {expected_active!r}, found {self._active!r})"
            )
        self._active = shadow

    def _error(self, operation: str, reason: str) -> AdapterError:
        return AdapterError(
            AdapterFailure(self.profile_name, operation, "conflict", False, f"content index {operation}: {reason}")
        )


def _matches(unit: IndexedContentUnit, text: str | None) -> bool:
    if not text:
        return True
    haystack = unit.text.casefold()
    return all(token in haystack for token in text.casefold().split())


def _score(unit: IndexedContentUnit, text: str | None) -> int:
    if not text:
        return 0
    haystack = unit.text.casefold()
    return sum(haystack.count(token) for token in text.casefold().split())


def _lexical_rank(candidates: list[IndexedContentUnit], text: str | None) -> list[IndexedContentUnit]:
    scored = [(_score(unit, text), unit) for unit in candidates if _matches(unit, text)]
    scored.sort(key=lambda pair: (-pair[0], pair[1].content_unit_id))
    return [unit for _, unit in scored]


def _dense_rank(candidates: list[IndexedContentUnit], query_vector: EmbeddingVector) -> list[IndexedContentUnit]:
    scored = [(_cosine(unit.embedding, query_vector), unit) for unit in candidates if unit.embedding]
    matches = [(score, unit) for score, unit in scored if score > 0.0]
    matches.sort(key=lambda pair: (-pair[0], pair[1].content_unit_id))
    return [unit for _, unit in matches]


def _fuse(lexical: list[IndexedContentUnit], dense: list[IndexedContentUnit]) -> list[IndexedContentUnit]:
    scores: dict[str, float] = {}
    units: dict[str, IndexedContentUnit] = {}
    for ranked in (lexical, dense):
        for rank, unit in enumerate(ranked):
            scores[unit.content_unit_id] = scores.get(unit.content_unit_id, 0.0) + 1.0 / (_RRF_K + rank)
            units[unit.content_unit_id] = unit
    return sorted(units.values(), key=lambda unit: (-scores[unit.content_unit_id], unit.content_unit_id))


def _cosine(a: EmbeddingVector, b: EmbeddingVector) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _hit(unit: IndexedContentUnit, generation: str) -> ContentSearchHit:
    return ContentSearchHit(
        source_media_item_version_id=unit.source_media_item_version_id,
        content_unit_id=unit.content_unit_id,
        index_generation=generation,
        page_number=unit.page_number,
        start_ms=unit.start_ms,
        end_ms=unit.end_ms,
        text_hash=unit.text_hash,
    )
