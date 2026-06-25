from __future__ import annotations

from dataclasses import replace

from foundry_lite.application.ports.content_index import (
    ContentSearchHit,
    HybridContentQuery,
    is_classification_cleared,
)
from foundry_lite.application.ports.media_derivative_repository import ContentUnitRecord
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.media.query_enrichment import enrich_query
from foundry_lite.domain.context import RequestContext


class DefaultContentRetrievalService(CoreService):
    """Resolve content search hits against authoritative truth (doc §10.1).

    The index is a projection; before returning a hit this service re-reads the DB
    content_unit and (1) drops hits whose unit no longer exists (stale source version),
    (2) drops hits the caller's tenant may not see (ACL — no cross-tenant leakage), and
    (3) drops hits whose index text_hash disagrees with the DB row (citation integrity).
    The query is forced to the caller's tenant so the index can never widen scope.
    """

    required_dependencies = (
        "engine",
        "media_derivative_repository",
        "content_index_adapter",
        "embedding_model_adapter",
        "completion_model_adapter",
    )
    required_collaborators = ()

    def search_content(self, ctx: RequestContext, *, query: HybridContentQuery) -> list[ContentSearchHit]:
        scoped = self._with_query_vector(replace(query, tenant_id=ctx.tenant_id))
        hits = self.content_index_adapter.search(scoped)
        if not hits:
            return []
        with self.engine.begin() as conn:
            units = self.media_derivative_repository.get_content_units_by_ids(
                transaction=conn, ids=[hit.content_unit_id for hit in hits]
            )
        unit_by_id = {unit.content_unit_id: unit for unit in units}
        return [
            hit
            for hit in hits
            if _is_authoritative(
                hit, unit_by_id.get(hit.content_unit_id), ctx.tenant_id, scoped.allowed_classifications
            )
        ]

    def _with_query_vector(self, query: HybridContentQuery) -> HybridContentQuery:
        if not self.embedding_model_adapter.is_available or not query.text:
            return query
        # Query-side L13: HyDE embeds an LLM hypothetical answer (search-bait) instead of the raw
        # question, and distillation cleans the keyword leg. Default-off — when no completion model
        # is wired ``enriched`` is the raw query, so this stays byte-for-byte as before.
        enriched = enrich_query(query.text, self.completion_model_adapter)
        vector = self.embedding_model_adapter.embed([enriched.dense_text])[0]
        return replace(
            query,
            text=enriched.keyword_text,
            query_vector=vector,
            embedding_model_version=self.embedding_model_adapter.model_version,
        )


def _is_authoritative(
    hit: ContentSearchHit,
    unit: ContentUnitRecord | None,
    tenant_id: str,
    allowed_classifications: tuple[str, ...] | None,
) -> bool:
    if unit is None:
        return False  # stale: the source content unit no longer exists
    if unit.tenant_id != tenant_id or str(unit.security_envelope.get("tenantId", unit.tenant_id)) != tenant_id:
        return False  # ACL: never leak another tenant's content
    # AIP P0a defense-in-depth: re-apply the classification gate against the authoritative DB row,
    # so even a stale/over-broad index that leaked an over-classified unit is dropped here.
    if not is_classification_cleared(str(unit.security_envelope.get("classification", "")), allowed_classifications):
        return False
    return hit.text_hash == unit.text_hash  # citation integrity: index must match the truth
