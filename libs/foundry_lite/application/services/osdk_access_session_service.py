"""Online OSDK OAuth access-session validation for revocable bearer tokens."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied


class OsdkAccessSessionService(CoreService):
    """Intersect a verified local JWT with its durable OAuth session record."""

    required_dependencies = ("engine", "oauth_session_repository")
    required_collaborators = ()

    def require_active(self, ctx: RequestContext, application_id: str) -> None:
        session_id = ctx.oauth_session_id
        if session_id is None:
            return
        with self.engine.begin() as conn:
            row = self.oauth_session_repository.session_by_id(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                session_id=session_id,
            )
        if row is None or not _is_active_session(row, ctx, application_id):
            raise PermissionDenied(
                "OSDK OAuth access session is inactive or no longer authorized",
                details={"resource": "oauth_access_session"},
            )


def _is_active_session(row: Mapping[str, object], ctx: RequestContext, application_id: str) -> bool:
    return (
        row.get("status") == "active"
        and row.get("app_id") == application_id
        and row.get("client_id") == ctx.client_id
        and row.get("actor_user_id") == ctx.actor_user_id
        and _string_set(row.get("scopes")) == set(ctx.token_scopes)
        and not _is_expired(row.get("expires_at"))
    )


def _string_set(value: object) -> set[str]:
    return {str(item) for item in value} if isinstance(value, list | tuple) else set()


def _is_expired(value: object) -> bool:
    if not isinstance(value, str):
        return True
    try:
        return datetime.fromisoformat(value) <= datetime.now().astimezone()
    except ValueError:
        return True
