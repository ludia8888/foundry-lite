"""OSDK OAuth authorize/token/refresh/revoke routes."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping
from dataclasses import replace
from typing import cast
from urllib.parse import parse_qs, unquote

from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse
from foundry_lite.application.services.osdk_dynamic_client_registration import parse_dynamic_client_registration
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import FoundryLiteError, NotFound, PermissionDenied, ValidationFailed
from pydantic import ValidationError

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.mcp_authorization import McpResourceTarget, mcp_resource_scopes, parse_mcp_resource
from foundry_lite_api.request_context import _ctx
from foundry_lite_api.schemas import JsonObject, OsdkOAuthRefreshRequest, OsdkOAuthTokenRequest

router = APIRouter()
_MAX_TOKEN_REQUEST_BYTES = 16_384


def _scope_query(scope: str | None) -> tuple[str, ...]:
    if not scope:
        return ()
    return tuple(item.strip() for item in scope.replace(",", " ").split(" ") if item.strip())


@router.get("/.well-known/oauth-authorization-server")
def osdk_oauth_authorization_server(request: Request) -> dict[str, object]:
    base = runtime.get_mcp_authorization_config().canonical_base_url(str(request.base_url))
    metadata: dict[str, object] = {
        "issuer": runtime.foundry.auth.osdk_oauth_issuer(),
        "authorization_endpoint": f"{base}/api/auth/osdk/oauth/authorize",
        "token_endpoint": f"{base}/api/auth/osdk/oauth/token",
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": ["authorization_code", "client_credentials", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post", "none"],
    }
    if runtime.get_mcp_authorization_config().dynamic_registration_application_id() is not None:
        metadata["registration_endpoint"] = f"{base}/api/auth/osdk/oauth/register"
    return metadata


@router.post("/api/auth/osdk/oauth/register", status_code=201)
async def register_osdk_oauth_dynamic_client(request: Request) -> JsonObject:
    """RFC 7591 registration so a remote MCP host can mint its own PKCE client."""

    config = runtime.get_mcp_authorization_config()
    application_id = config.dynamic_registration_application_id()
    if application_id is None:
        raise _handle_error(NotFound("dynamic client registration is not enabled"), request)
    try:
        registration = parse_dynamic_client_registration(await _registration_body(request))
        return cast(
            JsonObject,
            runtime.foundry.auth.osdk_oauth_register_dynamic_client(
                application_id=application_id,
                registration=registration,
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


def _consent_ctx(request: Request) -> RequestContext:
    """Apply the opt-in local consent roles to an interactive browser authorization."""

    ctx = _ctx(request)
    roles = runtime.get_mcp_authorization_config().consent_roles()
    return replace(ctx, roles=roles) if roles else ctx


async def _registration_body(request: Request) -> Mapping[str, object]:
    raw = await request.body()
    if len(raw) > _MAX_TOKEN_REQUEST_BYTES:
        raise ValidationFailed("dynamic client registration body is too large")
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError as exc:
        raise ValidationFailed("dynamic client registration body must be JSON") from exc
    if not isinstance(payload, dict):
        raise ValidationFailed("dynamic client registration body must be a JSON object")
    return cast(Mapping[str, object], payload)


@router.get("/api/auth/osdk/oauth/authorize", response_model=None)
def authorize_osdk_oauth(
    request: Request,
    client_id: str | None = Query(default=None, alias="clientId"),
    oauth_client_id: str | None = Query(default=None, alias="client_id"),
    redirect_uri: str | None = Query(default=None, alias="redirectUri"),
    oauth_redirect_uri: str | None = Query(default=None, alias="redirect_uri"),
    code_challenge: str | None = Query(default=None, alias="codeChallenge"),
    oauth_code_challenge: str | None = Query(default=None, alias="code_challenge"),
    code_challenge_method: str | None = Query(default=None, alias="codeChallengeMethod"),
    oauth_code_challenge_method: str | None = Query(default=None, alias="code_challenge_method"),
    scope: str | None = Query(default=None),
    state: str | None = Query(default=None),
    response_type: str | None = Query(default=None),
    resource: str | None = Query(default=None),
) -> JsonObject | RedirectResponse:
    try:
        resolved_client_id = _oauth_query(client_id, oauth_client_id, "client_id")
        target = parse_mcp_resource(request, resource) if resource is not None else None
        if response_type is not None and response_type != "code":
            raise ValidationFailed("OSDK OAuth response_type must be code")
        if response_type is not None and target is None:
            raise ValidationFailed("OSDK OAuth standard authorization requires resource")
        result = cast(
            JsonObject,
            runtime.foundry.auth.osdk_oauth_authorize(
                client_id=resolved_client_id,
                redirect_uri=_oauth_query(redirect_uri, oauth_redirect_uri, "redirect_uri"),
                code_challenge=_oauth_query(code_challenge, oauth_code_challenge, "code_challenge"),
                code_challenge_method=_optional_oauth_query(
                    code_challenge_method, oauth_code_challenge_method, "code_challenge_method", "S256"
                ),
                scopes=_resource_scopes(target, _scope_query(scope)),
                state=state,
                resource=target.resource_uri if target is not None else None,
                resource_application_id=target.application_id if target is not None else None,
                ctx=_consent_ctx(request),
            ),
        )
        if response_type is not None:
            return RedirectResponse(str(result["redirectTo"]), status_code=302)
        return result
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/auth/osdk/oauth/token")
async def exchange_osdk_oauth_token(request: Request) -> JsonObject:
    try:
        payload = await _token_request(request)
        target = parse_mcp_resource(request, payload.resource) if payload.resource is not None else None
        token_ctx = _oauth_token_context(request, payload, target)
        return _oauth_token_response(_exchange_oauth_grant(payload, target, token_ctx))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


def _exchange_oauth_grant(
    payload: OsdkOAuthTokenRequest,
    target: McpResourceTarget | None,
    token_ctx: RequestContext,
) -> JsonObject:
    if payload.grant_type == "client_credentials":
        return _client_credentials_token(payload, target, token_ctx)
    if payload.grant_type == "refresh_token":
        return _refresh_grant_token(payload, target, token_ctx)
    return _authorization_code_token(payload, target, token_ctx)


def _client_credentials_token(
    payload: OsdkOAuthTokenRequest, target: McpResourceTarget | None, token_ctx: RequestContext
) -> JsonObject:
    return cast(
        JsonObject,
        runtime.foundry.auth.osdk_oauth_client_credentials(
            client_id=payload.client_id,
            client_secret=_required(payload.client_secret, "clientSecret"),
            scopes=_resource_scopes(target, _scope_query(payload.scope)),
            resource=target.resource_uri if target is not None else None,
            ctx=token_ctx,
        ),
    )


def _refresh_grant_token(
    payload: OsdkOAuthTokenRequest, target: McpResourceTarget | None, token_ctx: RequestContext
) -> JsonObject:
    if payload.client_secret is not None:
        runtime.foundry.auth.verify_osdk_oauth_client_credentials(
            client_id=payload.client_id,
            client_secret=payload.client_secret,
            ctx=token_ctx,
        )
    return cast(
        JsonObject,
        runtime.foundry.auth.osdk_oauth_refresh(
            refresh_token=_required(payload.refresh_token, "refreshToken"),
            client_id=payload.client_id,
            resource=target.resource_uri if target is not None else None,
            resource_application_id=target.application_id if target is not None else None,
            should_reevaluate_roles=False,
            ctx=token_ctx,
        ),
    )


def _authorization_code_token(
    payload: OsdkOAuthTokenRequest, target: McpResourceTarget | None, token_ctx: RequestContext
) -> JsonObject:
    return cast(
        JsonObject,
        runtime.foundry.auth.osdk_oauth_token(
            client_id=payload.client_id,
            code=_required(payload.code, "code"),
            redirect_uri=_required(payload.redirect_uri, "redirectUri"),
            code_verifier=_required(payload.code_verifier, "codeVerifier"),
            resource=target.resource_uri if target is not None else None,
            resource_application_id=target.application_id if target is not None else None,
            ctx=token_ctx,
        ),
    )


def _oauth_token_context(
    request: Request,
    payload: OsdkOAuthTokenRequest,
    target: McpResourceTarget | None,
) -> RequestContext:
    """Bind a token backchannel request without requiring a resource-owner bearer."""

    tenant_id = _oauth_token_tenant(request, payload, target)
    request_id = getattr(request.state, "request_id", "oauth-client-credentials")
    return RequestContext(
        tenant_id=tenant_id,
        actor_user_id=f"oauth-client:{payload.client_id}",
        request_id=request_id,
        roles=(),
    )


def _oauth_token_tenant(
    request: Request,
    payload: OsdkOAuthTokenRequest,
    target: McpResourceTarget | None,
) -> str:
    payload_tenant = _tenant_hint(payload.tenant_id)
    header_tenant = _tenant_hint(request.headers.get("X-Tenant-ID"))
    if payload_tenant and header_tenant and payload_tenant != header_tenant:
        raise ValidationFailed("OSDK OAuth client credentials tenant values conflict")
    hinted_tenant = payload_tenant or header_tenant
    if target is not None:
        resolved_tenant = runtime.foundry.auth.osdk_oauth_resource_tenant(
            target.application_id,
            payload.client_id,
        )
        if hinted_tenant is not None and hinted_tenant != resolved_tenant:
            raise ValidationFailed("OSDK OAuth client credentials are invalid")
        return resolved_tenant
    tenant_id = hinted_tenant
    if tenant_id is None:
        if payload.grant_type != "client_credentials":
            return _ctx(request).tenant_id
        raise ValidationFailed("OSDK OAuth client credentials require tenant_id or resource")
    return tenant_id


def _tenant_hint(value: str | None) -> str | None:
    if value is None:
        return None
    if not value.strip():
        raise ValidationFailed("OSDK OAuth client credentials tenant_id must be non-empty")
    return value.strip()


def _resource_scopes(
    target: McpResourceTarget | None,
    requested_scopes: tuple[str, ...],
) -> tuple[str, ...]:
    if target is None:
        return requested_scopes
    allowed = mcp_resource_scopes(target.application_id, target.plane)
    if not allowed:
        raise PermissionDenied("OSDK OAuth application has no scopes for the requested MCP resource")
    selected = requested_scopes or allowed
    if not set(selected).issubset(allowed):
        raise PermissionDenied("OSDK OAuth requested scope is not granted for the MCP resource")
    return selected


async def _token_request(request: Request) -> OsdkOAuthTokenRequest:
    body = await request.body()
    if len(body) > _MAX_TOKEN_REQUEST_BYTES:
        raise ValidationFailed("OSDK OAuth token request exceeds 16 KiB")
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    try:
        if content_type == "application/json":
            raw = json.loads(body)
        elif content_type == "application/x-www-form-urlencoded":
            raw = _single_form_values(parse_qs(body.decode("utf-8"), keep_blank_values=True))
        else:
            raise ValidationFailed("OSDK OAuth token request content type is not supported")
        if not isinstance(raw, Mapping):
            raise ValidationFailed("OSDK OAuth token request must be an object")
        values: dict[str, object] = {str(key): value for key, value in raw.items()}
        return OsdkOAuthTokenRequest.model_validate(_with_basic_client_auth(values, request))
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as exc:
        raise ValidationFailed("OSDK OAuth token request is invalid") from exc


def _single_form_values(values: dict[str, list[str]]) -> dict[str, str]:
    if any(len(items) != 1 for items in values.values()):
        raise ValidationFailed("OSDK OAuth token request contains duplicate parameters")
    return {key: items[0] for key, items in values.items()}


def _with_basic_client_auth(values: dict[str, object], request: Request) -> dict[str, object]:
    authorization = request.headers.get("Authorization", "")
    scheme, _, encoded = authorization.partition(" ")
    if scheme.lower() != "basic":
        return values
    try:
        decoded = base64.b64decode(encoded.strip(), validate=True).decode("utf-8")
        encoded_client_id, encoded_secret = decoded.split(":", 1)
        client_id = unquote(encoded_client_id)
        client_secret = unquote(encoded_secret)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ValidationFailed("OSDK OAuth client authentication is invalid") from exc
    body_client_id = values.get("client_id", values.get("clientId"))
    body_secret = values.get("client_secret", values.get("clientSecret"))
    if not client_id or not client_secret or (body_client_id is not None and body_client_id != client_id):
        raise ValidationFailed("OSDK OAuth client authentication is invalid")
    if body_secret is not None:
        raise ValidationFailed("OSDK OAuth client authentication method is ambiguous")
    return {**values, "client_id": client_id, "client_secret": client_secret}


def _oauth_token_response(payload: JsonObject) -> JsonObject:
    response = dict(payload)
    for camel, standard in (
        ("accessToken", "access_token"),
        ("tokenType", "token_type"),
        ("expiresIn", "expires_in"),
        ("refreshToken", "refresh_token"),
        ("refreshExpiresAt", "refresh_expires_at"),
    ):
        if camel in payload:
            response[standard] = payload[camel]
    return response


def _oauth_query(camel: str | None, standard: str | None, field_name: str) -> str:
    if camel and standard and camel != standard:
        raise ValidationFailed(f"OSDK OAuth {field_name} query values conflict")
    return _required(camel or standard, field_name)


def _optional_oauth_query(camel: str | None, standard: str | None, field_name: str, default: str) -> str:
    if camel is None and standard is None:
        return default
    return _oauth_query(camel, standard, field_name)


def _required(value: str | None, field_name: str) -> str:
    if value is None or value == "":
        raise ValidationFailed(f"OSDK OAuth {field_name} is required")
    return value


@router.post("/api/auth/osdk/oauth/refresh")
def refresh_osdk_oauth_token(request: Request, payload: OsdkOAuthRefreshRequest) -> JsonObject:
    try:
        return cast(
            JsonObject, runtime.foundry.auth.osdk_oauth_refresh(refresh_token=payload.refresh_token, ctx=_ctx(request))
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/auth/osdk/oauth/revoke")
def revoke_osdk_oauth_token(request: Request, payload: OsdkOAuthRefreshRequest) -> JsonObject:
    try:
        return cast(
            JsonObject, runtime.foundry.auth.osdk_oauth_revoke(refresh_token=payload.refresh_token, ctx=_ctx(request))
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc
