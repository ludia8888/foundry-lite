"""RFC 7591 dynamic client registration for the local OSDK OAuth authorization server.

Remote MCP hosts such as ChatGPT do not accept a pre-shared client. They read the
protected-resource metadata, follow it to this authorization server and register
themselves before starting the Authorization Code + PKCE flow. Registration is therefore
unauthenticated by protocol, so this module keeps the blast radius closed: it only ever
writes into one operator-selected application, it never issues a client secret, and it
clamps the granted scopes to the scopes that application already publishes.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from foundry_lite.application.ports import RuntimeJsonObject
from foundry_lite.application.services.osdk_application_service import OsdkApplicationService
from foundry_lite.application.services.osdk_oauth_session_service import OsdkOAuthSessionService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed

_REGISTRATION_ACTOR = "mcp-dynamic-client-registration"
_REGISTRATION_ROLES = ("admin",)
_SUPPORTED_GRANT_TYPES = ("authorization_code", "refresh_token")
_SUPPORTED_RESPONSE_TYPES = ("code",)
_PUBLIC_CLIENT_AUTH_METHOD = "none"
_MAX_REDIRECT_URIS = 10
_CLIENT_NAME_MAX_LENGTH = 200


@dataclass(frozen=True, slots=True)
class DynamicClientRegistration:
    """A validated RFC 7591 registration request for one MCP application."""

    redirect_uris: tuple[str, ...]
    client_name: str | None
    requested_scopes: tuple[str, ...]


def parse_dynamic_client_registration(payload: Mapping[str, object]) -> DynamicClientRegistration:
    """Validate an RFC 7591 request body, rejecting anything this server cannot honour."""

    _require_supported_metadata(payload, "grant_types", _SUPPORTED_GRANT_TYPES)
    _require_supported_metadata(payload, "response_types", _SUPPORTED_RESPONSE_TYPES)
    _require_public_client(payload)
    return DynamicClientRegistration(
        redirect_uris=_registration_redirect_uris(payload.get("redirect_uris")),
        client_name=_registration_client_name(payload.get("client_name")),
        requested_scopes=_registration_scopes(payload.get("scope")),
    )


def register_dynamic_client(
    *,
    applications: OsdkApplicationService,
    sessions: OsdkOAuthSessionService,
    application_id: str,
    registration: DynamicClientRegistration,
) -> RuntimeJsonObject:
    """Create one public PKCE client that can reach every MCP plane of one application.

    Registration happens before the host has chosen a plane, so the client carries the whole
    application ceiling and the response deliberately omits `scope`. Hosts echo a returned
    scope back verbatim at authorize, where each plane accepts only its own subset -- so
    naming scopes here would pin the client to one plane and 403 on the others. Leaving it
    out lets authorize derive the right subset from the resource being requested.
    """

    tenant_id = _registration_tenant(sessions, application_id)
    granted_scopes = _granted_scopes(sessions.application_scopes(application_id), registration.requested_scopes)
    client_id = f"dcr-{secrets.token_urlsafe(18)}"
    applications.create_client(
        application_id,
        ctx=RequestContext(
            tenant_id=tenant_id,
            actor_user_id=_REGISTRATION_ACTOR,
            roles=_REGISTRATION_ROLES,
        ),
        client_id=client_id,
        redirect_uris=registration.redirect_uris,
        allowed_scopes=granted_scopes,
        idempotency_key=f"osdk-dcr-{client_id}",
    )
    return _registration_response(client_id, registration)


def _registration_tenant(sessions: OsdkOAuthSessionService, application_id: str) -> str:
    with sessions.engine.begin() as conn:
        tenant_id = sessions.osdk_application_repository.public_active_application_tenant(
            transaction=conn,
            app_id=application_id,
        )
    if tenant_id is None:
        raise NotFound("OSDK OAuth application was not found")
    return tenant_id


def _granted_scopes(application_scopes: Sequence[str], requested_scopes: Sequence[str]) -> tuple[str, ...]:
    """Clamp the request to the application ceiling; an empty request takes every scope."""

    available = tuple(application_scopes)
    if not available:
        raise NotFound("OSDK OAuth application publishes no scope")
    if not requested_scopes:
        return available
    granted = tuple(scope for scope in available if scope in set(requested_scopes))
    if not granted:
        raise ValidationFailed("dynamic client registration requested no scope this application publishes")
    return granted


def _registration_response(
    client_id: str,
    registration: DynamicClientRegistration,
) -> RuntimeJsonObject:
    response: dict[str, object] = {
        "client_id": client_id,
        "client_id_issued_at": int(datetime.now().astimezone().timestamp()),
        "redirect_uris": list(registration.redirect_uris),
        "grant_types": list(_SUPPORTED_GRANT_TYPES),
        "response_types": list(_SUPPORTED_RESPONSE_TYPES),
        "token_endpoint_auth_method": _PUBLIC_CLIENT_AUTH_METHOD,
    }
    if registration.client_name is not None:
        response["client_name"] = registration.client_name
    return response


def _require_supported_metadata(payload: Mapping[str, object], field: str, supported: Sequence[str]) -> None:
    values = payload.get(field)
    if values is None:
        return
    requested = _string_sequence(values, field)
    unsupported = tuple(value for value in requested if value not in set(supported))
    if unsupported:
        raise ValidationFailed(f"dynamic client registration {field} must be a subset of {list(supported)}")


def _require_public_client(payload: Mapping[str, object]) -> None:
    """This server only registers PKCE public clients, so a secret must never be promised."""

    method = payload.get("token_endpoint_auth_method")
    if method is not None and method != _PUBLIC_CLIENT_AUTH_METHOD:
        raise ValidationFailed(
            f"dynamic client registration token_endpoint_auth_method must be {_PUBLIC_CLIENT_AUTH_METHOD}"
        )


def _registration_redirect_uris(value: object) -> tuple[str, ...]:
    if value is None:
        raise ValidationFailed("dynamic client registration requires at least one redirect_uri")
    uris = _string_sequence(value, "redirect_uris")
    if not uris:
        raise ValidationFailed("dynamic client registration requires at least one redirect_uri")
    if len(uris) > _MAX_REDIRECT_URIS:
        raise ValidationFailed(f"dynamic client registration allows at most {_MAX_REDIRECT_URIS} redirect_uris")
    for uri in uris:
        _require_registrable_redirect_uri(uri)
    return uris


def _require_registrable_redirect_uri(uri: str) -> None:
    parsed = urlsplit(uri)
    if parsed.fragment:
        raise ValidationFailed("dynamic client registration redirect_uri must not carry a fragment")
    if parsed.scheme == "https" and parsed.hostname:
        return
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        return
    raise ValidationFailed("dynamic client registration redirect_uri must be https or a loopback http URI")


def _registration_client_name(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValidationFailed("dynamic client registration client_name must be a non-empty string")
    name = value.strip()
    if len(name) > _CLIENT_NAME_MAX_LENGTH:
        raise ValidationFailed(
            f"dynamic client registration client_name must be at most {_CLIENT_NAME_MAX_LENGTH} characters"
        )
    return name


def _registration_scopes(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, str):
        raise ValidationFailed("dynamic client registration scope must be a space-delimited string")
    return tuple(item for item in value.replace(",", " ").split(" ") if item)


def _string_sequence(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        raise ValidationFailed(f"dynamic client registration {field} must be an array of strings")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValidationFailed(f"dynamic client registration {field} must contain non-empty strings")
        items.append(item.strip())
    return tuple(items)
