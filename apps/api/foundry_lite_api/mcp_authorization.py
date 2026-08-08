"""RFC 9728/8707 authorization helpers shared by both HTTP MCP planes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import SplitResult, quote, unquote, urlsplit

from fastapi import HTTPException, Request
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import FoundryLiteError, NotFound, PermissionDenied, ValidationFailed

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.request_context import _ctx_for_audience

McpPlane = Literal["builder", "ontology"]


@dataclass(frozen=True)
class McpResourceTarget:
    plane: McpPlane
    application_id: str
    resource_uri: str


def canonical_mcp_resource(request: Request, plane: McpPlane, application_id: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/mcp/{plane}/{quote(application_id, safe='')}"


def parse_mcp_resource(request: Request, resource: str | None) -> McpResourceTarget:
    value = _required_mcp_resource(resource)
    parsed = urlsplit(value)
    segments = parsed.path.split("/")
    _require_canonical_resource_shape(parsed, segments)
    application_id = _mcp_resource_application_id(segments[3])
    plane: McpPlane = "builder" if segments[2] == "builder" else "ontology"
    target = McpResourceTarget(
        plane=plane,
        application_id=application_id,
        resource_uri=canonical_mcp_resource(request, plane, application_id),
    )
    if not _same_resource_uri(value, target.resource_uri):
        raise ValidationFailed("OSDK OAuth resource does not belong to this authorization server")
    return target


def _required_mcp_resource(resource: str | None) -> str:
    if resource is None or not resource.strip():
        raise ValidationFailed("OSDK OAuth resource is required for standard MCP authorization")
    return resource.strip()


def _require_canonical_resource_shape(parsed: SplitResult, segments: list[str]) -> None:
    if not _has_canonical_resource_authority(parsed) or not _has_canonical_resource_path(segments):
        raise ValidationFailed("OSDK OAuth resource is not a canonical MCP URI")


def _has_canonical_resource_authority(parsed: SplitResult) -> bool:
    return parsed.scheme.lower() in {"http", "https"} and _has_clean_resource_authority(parsed)


def _has_clean_resource_authority(parsed: SplitResult) -> bool:
    return bool(
        parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


def _has_canonical_resource_path(segments: list[str]) -> bool:
    return len(segments) == 4 and segments[:2] == ["", "mcp"] and segments[2] in {"builder", "ontology"}


def _mcp_resource_application_id(encoded_application_id: str) -> str:
    application_id = unquote(encoded_application_id)
    if not application_id or "/" in application_id or "\\" in application_id:
        raise ValidationFailed("OSDK OAuth resource is not a canonical MCP URI")
    return application_id


def require_mcp_context(
    request: Request,
    plane: McpPlane,
    application_id: str,
) -> RequestContext:
    resource = canonical_mcp_resource(request, plane, application_id)
    authorization = request.headers.get("Authorization", "")
    if not authorization.lower().startswith("bearer "):
        raise mcp_unauthorized(request, plane, application_id)
    try:
        ctx = _ctx_for_audience(request, resource)
    except PermissionDenied as exc:
        raise mcp_unauthorized(request, plane, application_id) from exc
    if ctx.application_id is not None and ctx.application_id != application_id:
        raise mcp_unauthorized(request, plane, application_id)
    return ctx


def mcp_unauthorized(request: Request, plane: McpPlane, application_id: str) -> HTTPException:
    metadata = protected_resource_metadata_uri(request, plane, application_id)
    return HTTPException(
        status_code=401,
        detail={
            "code": "PERMISSION_DENIED",
            "message": "MCP bearer authentication failed",
            "details": {},
            "request_id": getattr(request.state, "request_id", None),
        },
        headers={"WWW-Authenticate": f'Bearer resource_metadata="{metadata}"'},
    )


def mcp_forbidden(
    exc: FoundryLiteError,
    request: Request,
    plane: McpPlane,
    application_id: str,
) -> HTTPException:
    handled = _handle_error(exc, request)
    scopes = " ".join(mcp_resource_scopes(application_id, plane))
    handled.status_code = 403
    handled.headers = {"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{scopes}"'}
    return handled


def mcp_permission_failure(
    exc: PermissionDenied,
    request: Request,
    plane: McpPlane,
    application_id: str,
) -> HTTPException:
    if exc.details.get("resource") == "oauth_access_session":
        return mcp_unauthorized(request, plane, application_id)
    return mcp_forbidden(exc, request, plane, application_id)


def protected_resource_metadata_uri(request: Request, plane: McpPlane, application_id: str) -> str:
    base = str(request.base_url).rstrip("/")
    encoded_id = quote(application_id, safe="")
    return f"{base}/.well-known/oauth-protected-resource/mcp/{plane}/{encoded_id}"


def protected_resource_metadata(
    request: Request,
    plane: McpPlane,
    application_id: str,
) -> dict[str, object]:
    scopes = mcp_resource_scopes(application_id, plane)
    if not scopes:
        raise NotFound("OSDK OAuth protected resource was not found")
    return {
        "resource": canonical_mcp_resource(request, plane, application_id),
        "authorization_servers": [runtime.foundry.auth.osdk_oauth_issuer()],
        "bearer_methods_supported": ["header"],
        "scopes_supported": list(scopes),
    }


def mcp_resource_scopes(application_id: str, plane: McpPlane) -> tuple[str, ...]:
    try:
        scopes = runtime.foundry.auth.osdk_oauth_application_scopes(application_id)
    except FoundryLiteError as exc:
        raise NotFound("OSDK OAuth protected resource was not found") from exc
    if plane == "builder":
        return tuple(sorted(scopes))
    prefixes = ("osdk:object:", "osdk:action:", "osdk:function:")
    return tuple(sorted(scope for scope in scopes if scope.startswith(prefixes)))


def _same_resource_uri(left: str, right: str) -> bool:
    first = urlsplit(left)
    second = urlsplit(right)
    return (
        first.scheme.lower() == second.scheme.lower()
        and (first.hostname or "").lower() == (second.hostname or "").lower()
        and _effective_port(first.scheme, first.port) == _effective_port(second.scheme, second.port)
        and unquote(first.path) == unquote(second.path)
    )


def _effective_port(scheme: str, port: int | None) -> int | None:
    if port is not None:
        return port
    return 443 if scheme.lower() == "https" else 80 if scheme.lower() == "http" else None
