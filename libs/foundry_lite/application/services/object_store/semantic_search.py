"""Object semantic search helpers (L12a — Palantir ``Vector`` object property).

An object embedding is a DERIVED projection of the object's own ``searchable_properties``
text — never a separate source truth. The committed object record stays the serving truth
and the search index (with its vectors) rebuilds from it. These helpers keep the embedding
concerns out of the keyword/structured ``ObjectSearchService`` body: when no embedding model
is wired (``is_available`` False) every helper degrades to keyword-only behaviour, so the
existing object search is unchanged and default-off.
"""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import ObjectRecordRow
from foundry_lite.application.ports.embedding_model import EmbeddingModelAdapter, EmbeddingVector
from foundry_lite.application.ports.search_adapter import SearchDocument, SearchIndexMapping, SearchQuery
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

SEARCH_INDEX_MAX_LIMIT = 500


def search_limit(limit: int) -> int:
    """Validate the public search limit before calling the adapter."""
    if limit < 1:
        raise ValidationFailed("search query limit must be positive", details={"limit": limit})
    if limit > SEARCH_INDEX_MAX_LIMIT:
        raise ValidationFailed(
            "search query limit exceeds maximum",
            details={"limit": limit, "max_limit": SEARCH_INDEX_MAX_LIMIT},
        )
    return limit


def searchable_text(properties: Mapping[str, object], mapping: SearchIndexMapping) -> str:
    """Concatenate the object's own searchable-property values into one embeddable string.

    The object vector is computed from the object's OWN text (its ``searchable_properties``),
    not from any media linkage — media-derived object vectors are the later L12b step.
    """
    parts = [str(properties[name]) for name in mapping.searchable_properties if name in properties]
    return "\n".join(part for part in parts if part)


def object_embedding(
    embedding_model_adapter: EmbeddingModelAdapter,
    properties: Mapping[str, object],
    mapping: SearchIndexMapping,
) -> tuple[EmbeddingVector, str]:
    """Return the model-pinned vector for one object, or ``((), "")`` when no model is wired.

    When unavailable the empty vector keeps the document keyword-only, exactly as today.
    """
    if not embedding_model_adapter.is_available:
        return (), ""
    text = searchable_text(properties, mapping)
    if not text:
        return (), ""
    vector = embedding_model_adapter.embed([text])[0]
    return tuple(vector), embedding_model_adapter.model_version


def search_document(
    ctx: RequestContext,
    object_type_api_name: str,
    row: ObjectRecordRow,
    mapping: SearchIndexMapping,
    embedding_model_adapter: EmbeddingModelAdapter,
) -> SearchDocument:
    """Build the projection document, attaching the model-pinned object vector when wired."""
    properties = _indexed_document_properties(row, mapping)
    embedding, embedding_model_version = object_embedding(embedding_model_adapter, properties, mapping)
    return SearchDocument(
        tenant_id=ctx.tenant_id,
        object_type=object_type_api_name,
        document_id=row["object_id"],
        version=row["object_version"],
        properties=properties,
        embedding=embedding,
        embedding_model_version=embedding_model_version,
    )


def _indexed_document_properties(row: ObjectRecordRow, mapping: SearchIndexMapping) -> Mapping[str, object]:
    names = set(mapping.indexed_properties) | set(mapping.searchable_properties)
    return {name: row["properties"][name] for name in sorted(names) if name in row["properties"]}


def keyword_search_query(
    ctx: RequestContext,
    object_type_api_name: str,
    search_text: str,
    mapping: SearchIndexMapping,
    limit: int,
) -> SearchQuery:
    """Create the vendor-neutral keyword/full-text query sent through the search port."""
    return SearchQuery(
        tenant_id=ctx.tenant_id,
        object_type=object_type_api_name,
        terms={},
        text=search_text,
        searchable_properties=mapping.searchable_properties,
        limit=limit,
    )


def semantic_search_query(
    ctx: RequestContext,
    object_type_api_name: str,
    semantic_text: str,
    mapping: SearchIndexMapping,
    limit: int,
    embedding_model_adapter: EmbeddingModelAdapter,
) -> SearchQuery:
    """Embed the query text with the SAME model and build a nearestNeighbors search query."""
    vector = embedding_model_adapter.embed([semantic_text])[0]
    return SearchQuery(
        tenant_id=ctx.tenant_id,
        object_type=object_type_api_name,
        terms={},
        searchable_properties=mapping.searchable_properties,
        limit=limit,
        query_vector=tuple(vector),
        embedding_model_version=embedding_model_adapter.model_version,
    )


def require_semantic_model(embedding_model_adapter: EmbeddingModelAdapter) -> None:
    """Reject a semantic query when no embedding model is wired (keyword-only deployment)."""
    if not embedding_model_adapter.is_available:
        raise ValidationFailed("object semantic search requires a wired embedding model")
