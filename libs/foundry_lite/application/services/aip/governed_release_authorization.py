"""Application-client and exact OAuth scope checks for Governed Release."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied
from foundry_lite.domain.platform.scopes import resource_scope

JsonObject = Mapping[str, object]
GOVERNED_RELEASE_SCOPE = resource_scope("connector", "governed_release", "execute")


def has_active_client(value: object, client_id: str) -> bool:
    """Return whether a client registry contains the requested active client."""

    return isinstance(value, list) and any(
        isinstance(item, Mapping) and item.get("client_id") == client_id and item.get("status") == "active"
        for item in value
    )


def require_release_scope(ctx: RequestContext, bundle: JsonObject) -> None:
    """Require the release execution scope in both the token and app bundle."""

    resources = bundle.get("resources")
    granted = (
        tuple(str(scope) for row in resources if isinstance(row, Mapping) for scope in row.get("scopes", ()))
        if isinstance(resources, list)
        else ()
    )
    if GOVERNED_RELEASE_SCOPE not in ctx.token_scopes or GOVERNED_RELEASE_SCOPE not in granted:
        raise PermissionDenied(
            "Governed Release MCP OAuth scope is not granted",
            details={"requiredScope": GOVERNED_RELEASE_SCOPE},
        )


__all__ = ["GOVERNED_RELEASE_SCOPE", "has_active_client", "require_release_scope"]
