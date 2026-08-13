"""RFC 9728/8707 authorization helpers shared by the HTTP MCP planes."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal
from urllib.parse import SplitResult, quote, unquote, urlsplit

from fastapi import HTTPException, Request
from foundry_lite.application.services.aip.governed_release_authorization import GOVERNED_RELEASE_SCOPE
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import FoundryLiteError, NotFound, PermissionDenied, ValidationFailed

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.request_context import _ctx_for_audience

McpPlane = Literal["builder", "ontology", "release"]


@dataclass(frozen=True)
class McpResourceTarget:
    plane: McpPlane
    application_id: str
    resource_uri: str


def canonical_mcp_resource(request: Request, plane: McpPlane, application_id: str) -> str:
    base = _canonical_base_url(request)
    return f"{base}/mcp/{plane}/{quote(application_id, safe='')}"


def parse_mcp_resource(request: Request, resource: str | None) -> McpResourceTarget:
    value = _required_mcp_resource(resource)
    parsed = urlsplit(value)
    segments = parsed.path.split("/")
    _require_canonical_resource_shape(parsed, segments)
    application_id = _mcp_resource_application_id(segments[3])
    plane = _mcp_resource_plane(segments[2])
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
    return (
        len(segments) == 4
        and segments[:2] == ["", "mcp"]
        and segments[2]
        in {
            "builder",
            "ontology",
            "release",
        }
    )


def _mcp_resource_plane(value: str) -> McpPlane:
    if value == "builder":
        return "builder"
    if value == "ontology":
        return "ontology"
    return "release"


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
    authorization_config = runtime.get_mcp_authorization_config()
    if plane == "release" and not authorization_config.allows_release_application(application_id):
        raise mcp_unauthorized(request, plane, application_id)
    authorization = request.headers.get("Authorization", "")
    if not authorization.lower().startswith("bearer "):
        raise mcp_unauthorized(request, plane, application_id)
    try:
        ctx = _ctx_for_audience(request, resource)
    except PermissionDenied as exc:
        raise mcp_unauthorized(request, plane, application_id) from exc
    if ctx.application_id is not None and ctx.application_id != application_id:
        raise mcp_unauthorized(request, plane, application_id)
    configured_issuer = authorization_config.external_authorization_server
    if configured_issuer and not _same_issuer(ctx.authorization_server_issuer, configured_issuer):
        raise mcp_unauthorized(request, plane, application_id)
    return replace(ctx, application_id=application_id) if ctx.application_id is None else ctx


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


def mcp_tool_authentication_result(
    exc: PermissionDenied,
    request: Request,
    plane: McpPlane,
    application_id: str,
) -> dict[str, object]:
    """Return the MCP tool challenge that lets the client restart OAuth."""

    metadata = protected_resource_metadata_uri(request, plane, application_id)
    is_invalid_session = exc.details.get("resource") == "oauth_access_session"
    error = "invalid_token" if is_invalid_session else "insufficient_scope"
    description = (
        "An active human OAuth session is required."
        if is_invalid_session
        else "The required governed release OAuth scope is missing."
    )
    challenge = f'Bearer resource_metadata="{metadata}", error="{error}", error_description="{description}"'
    return {
        "content": [{"type": "text", "text": "Authentication is required before this release tool can run."}],
        "isError": True,
        "_meta": {"mcp/www_authenticate": [challenge]},
    }


def protected_resource_metadata_uri(request: Request, plane: McpPlane, application_id: str) -> str:
    base = _canonical_base_url(request)
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
        "authorization_servers": list(
            runtime.get_mcp_authorization_config().authorization_servers(runtime.foundry.auth.osdk_oauth_issuer())
        ),
        "bearer_methods_supported": ["header"],
        "scopes_supported": list(scopes),
    }


def mcp_resource_scopes(application_id: str, plane: McpPlane) -> tuple[str, ...]:
    authorization_config = runtime.get_mcp_authorization_config()
    if plane == "release" and not authorization_config.allows_release_application(application_id):
        return ()
    try:
        scopes = runtime.foundry.auth.osdk_oauth_application_scopes(application_id)
    except FoundryLiteError as exc:
        raise NotFound("OSDK OAuth protected resource was not found") from exc
    if plane == "builder":
        return tuple(sorted(scopes))
    if plane == "release":
        return (GOVERNED_RELEASE_SCOPE,) if GOVERNED_RELEASE_SCOPE in scopes else ()
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


def _canonical_base_url(request: Request) -> str:
    return runtime.get_mcp_authorization_config().canonical_base_url(str(request.base_url))


def _same_issuer(actual: str | None, expected: str) -> bool:
    return actual is not None and actual.rstrip("/") == expected.rstrip("/")


def _effective_port(scheme: str, port: int | None) -> int | None:
    if port is not None:
        return port
    return 443 if scheme.lower() == "https" else 80 if scheme.lower() == "http" else None
