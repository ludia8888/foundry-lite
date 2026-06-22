from __future__ import annotations

from foundry_lite.domain.context import RequestContext

SEARCH_PROJECTION_ACTOR = "system:search-projection"
SEARCH_PROJECTION_ROLES = ("admin", "finance")


def search_projection_context(ctx: RequestContext) -> RequestContext:
    return RequestContext(
        tenant_id=ctx.tenant_id,
        actor_user_id=SEARCH_PROJECTION_ACTOR,
        request_id=ctx.request_id,
        roles=SEARCH_PROJECTION_ROLES,
    )
