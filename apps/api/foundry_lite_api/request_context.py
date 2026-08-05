"""RequestContext construction from HTTP and WebSocket credentials."""

from __future__ import annotations

from fastapi import Header, Request, WebSocket
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import RateLimited
from foundry_lite.infrastructure.auth import AUTHORIZATION_HEADER

from foundry_lite_api import runtime


def _ctx(
    request: Request | None = None,
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_roles: str | None = Header(default=None),
    x_user_attributes: str | None = Header(default=None),
    x_foundry_lite_app_id: str | None = Header(default=None),
    x_foundry_lite_client_id: str | None = Header(default=None),
    x_foundry_lite_scopes: str | None = Header(default=None),
) -> RequestContext:
    defaults = RequestContext()
    credentials = _collect_credentials(
        request,
        authorization=authorization,
        x_tenant_id=x_tenant_id,
        x_user_id=x_user_id,
        x_roles=x_roles,
        x_user_attributes=x_user_attributes,
        x_foundry_lite_app_id=x_foundry_lite_app_id,
        x_foundry_lite_client_id=x_foundry_lite_client_id,
        x_foundry_lite_scopes=x_foundry_lite_scopes,
    )
    auth_provider = runtime.get_auth_provider()
    principal = auth_provider.authenticate(credentials) if credentials else auth_provider.anonymous()
    return RequestContext(
        tenant_id=principal.tenant_id,
        actor_user_id=principal.actor_user_id,
        request_id=_request_id(request, defaults.request_id),
        roles=principal.roles,
        application_id=principal.application_id,
        client_id=principal.client_id,
        token_scopes=principal.token_scopes,
        oauth_session_id=principal.oauth_session_id,
        user_attributes=principal.user_attributes,
    )


def _websocket_ctx(websocket: WebSocket) -> RequestContext:
    defaults = RequestContext()
    credentials = _collect_websocket_credentials(websocket)
    auth_provider = runtime.get_auth_provider()
    principal = auth_provider.authenticate(credentials) if credentials else auth_provider.anonymous()
    return RequestContext(
        tenant_id=principal.tenant_id,
        actor_user_id=principal.actor_user_id,
        request_id=websocket.headers.get("X-Request-ID", defaults.request_id),
        roles=principal.roles,
        application_id=principal.application_id,
        client_id=principal.client_id,
        token_scopes=principal.token_scopes,
        oauth_session_id=principal.oauth_session_id,
        user_attributes=principal.user_attributes,
    )


def _collect_websocket_credentials(websocket: WebSocket) -> dict[str, str]:
    authorization = websocket.headers.get(AUTHORIZATION_HEADER) or _websocket_subprotocol_bearer(websocket)
    pairs = (
        ("Authorization", authorization),
        ("X-Tenant-ID", websocket.headers.get("X-Tenant-ID")),
        ("X-User-ID", websocket.headers.get("X-User-ID")),
        ("X-Roles", websocket.headers.get("X-Roles")),
        ("X-User-Attributes", websocket.headers.get("X-User-Attributes")),
        ("X-Foundry-Lite-App-ID", websocket.headers.get("X-Foundry-Lite-App-ID")),
        ("X-Foundry-Lite-Client-ID", websocket.headers.get("X-Foundry-Lite-Client-ID")),
        ("X-Foundry-Lite-Scopes", websocket.headers.get("X-Foundry-Lite-Scopes")),
    )
    return {key: value for key, value in pairs if value}


def _websocket_subprotocol_bearer(websocket: WebSocket) -> str | None:
    header = websocket.headers.get("sec-websocket-protocol")
    if not header:
        return None
    for item in (part.strip() for part in header.split(",")):
        if item.startswith("bearer."):
            return f"Bearer {item.removeprefix('bearer.')}"
    return None


def _websocket_origin_allowed(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    return origin is None or origin in runtime.ALLOWED_BROWSER_ORIGINS


def _check_websocket_subscription_rate(ctx: RequestContext, object_type: str) -> None:
    key = (
        ctx.tenant_id,
        ctx.actor_user_id,
        ctx.application_id or "",
        ctx.client_id or "",
        object_type,
    )
    retry_after = runtime.websocket_subscription_rate_limiter.retry_after_seconds(
        key,
        limit=runtime.WEBSOCKET_SUBSCRIPTION_CONNECT_LIMIT,
        window_seconds=runtime.WEBSOCKET_SUBSCRIPTION_CONNECT_WINDOW_SECONDS,
    )
    if retry_after is not None:
        raise RateLimited(
            "WebSocket object subscription rate limit exceeded",
            details={"retryAfterSeconds": retry_after},
        )


def _collect_credentials(
    request: Request | None,
    *,
    authorization: str | None,
    x_tenant_id: str | None,
    x_user_id: str | None,
    x_roles: str | None,
    x_user_attributes: str | None,
    x_foundry_lite_app_id: str | None,
    x_foundry_lite_client_id: str | None,
    x_foundry_lite_scopes: str | None,
) -> dict[str, str]:
    pairs = (
        ("Authorization", _header_or_request(authorization, request, AUTHORIZATION_HEADER)),
        ("X-Tenant-ID", _header_or_request(x_tenant_id, request, "X-Tenant-ID")),
        ("X-User-ID", _header_or_request(x_user_id, request, "X-User-ID")),
        ("X-Roles", _header_or_request(x_roles, request, "X-Roles")),
        ("X-User-Attributes", _header_or_request(x_user_attributes, request, "X-User-Attributes")),
        ("X-Foundry-Lite-App-ID", _header_or_request(x_foundry_lite_app_id, request, "X-Foundry-Lite-App-ID")),
        ("X-Foundry-Lite-Client-ID", _header_or_request(x_foundry_lite_client_id, request, "X-Foundry-Lite-Client-ID")),
        ("X-Foundry-Lite-Scopes", _header_or_request(x_foundry_lite_scopes, request, "X-Foundry-Lite-Scopes")),
    )
    return {key: value for key, value in pairs if value}


def _header_or_request(value: str | None, request: Request | None, header_name: str) -> str | None:
    normalized = value if isinstance(value, str) else None
    if normalized or request is None:
        return normalized
    return request.headers.get(header_name)


def _request_id(request: Request | None, default_request_id: str) -> str:
    state = getattr(request, "state", None)
    return getattr(state, "request_id", default_request_id)
