from __future__ import annotations

from dataclasses import replace

from foundry_lite.application.ports.content_index import ContentSearchHit, HybridContentQuery
from foundry_lite.application.ports.media_derivative_repository import ContentUnitRecord
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext


class DefaultContentRetrievalService(CoreService):
    """Resolve content search hits against authoritative truth (doc §10.1).

    The index is a projection; before returning a hit this service re-reads the DB
    content_unit and (1) drops hits whose unit no longer exists (stale source version),
    (2) drops hits the caller's tenant may not see (ACL — no cross-tenant leakage), and
    (3) drops hits whose index text_hash disagrees with the DB row (citation integrity).
    The query is forced to the caller's tenant so the index can never widen scope.
    """

    required_dependencies = ("engine", "media_derivative_repository", "content_index_adapter")
    required_collaborators = ()

    def search_content(self, ctx: RequestContext, *, query: HybridContentQuery) -> list[ContentSearchHit]:
        scoped = replace(query, tenant_id=ctx.tenant_id)
        hits = self.content_index_adapter.search(scoped)
        if not hits:
            return []
        with self.engine.begin() as conn:
            units = self.media_derivative_repository.get_content_units_by_ids(
                transaction=conn, ids=[hit.content_unit_id for hit in hits]
            )
        unit_by_id = {unit.content_unit_id: unit for unit in units}
        return [hit for hit in hits if _is_authoritative(hit, unit_by_id.get(hit.content_unit_id), ctx.tenant_id)]


def _is_authoritative(hit: ContentSearchHit, unit: ContentUnitRecord | None, tenant_id: str) -> bool:
    if unit is None:
        return False  # stale: the source content unit no longer exists
    if unit.tenant_id != tenant_id or str(unit.security_envelope.get("tenantId", unit.tenant_id)) != tenant_id:
        return False  # ACL: never leak another tenant's content
    return hit.text_hash == unit.text_hash  # citation integrity: index must match the truth
