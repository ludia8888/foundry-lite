"""Application service helpers for osdk application records workflows."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.application.ports import (
    OsdkApplicationClientRecord,
    OsdkApplicationRecord,
    OsdkApplicationResourceRecord,
    OsdkApplicationResourceRow,
    OsdkLanguage,
    OsdkResourceOperation,
    OsdkResourceType,
    RuntimeJsonObject,
)
from foundry_lite.application.primitives import _new_id
from foundry_lite.application.services.osdk_application_types import (
    _CHANNELS,
    _LANGUAGES,
    _OPERATIONS,
    _RESOURCE_TYPES,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied, ValidationFailed


def scope_for(resource_type: OsdkResourceType, resource_api_name: str, operation: OsdkResourceOperation) -> str:
    if resource_type not in _RESOURCE_TYPES or operation not in _OPERATIONS:
        raise ValidationFailed("unsupported OSDK resource scope request")
    return f"osdk:{resource_type}:{resource_api_name}:{operation}"


def _application_record(
    ctx: RequestContext, app_api_name: str, display_name: str, idempotency_key: str, now: str
) -> OsdkApplicationRecord:
    _require_api_name(app_api_name, "appApiName")
    return OsdkApplicationRecord(
        application_id=_new_id("osdk_app"),
        tenant_id=ctx.tenant_id,
        app_api_name=app_api_name,
        display_name=display_name or app_api_name,
        status="active",
        owner_user_id=ctx.actor_user_id,
        created_idempotency_key=idempotency_key,
        created_at=now,
        updated_at=now,
    )


def _resource_record(
    tenant_id: str, app_id: str, payload: Mapping[str, object], now: str
) -> OsdkApplicationResourceRecord:
    resource_type = _resource_type(payload.get("resourceType", payload.get("resource_type")))
    resource_api_name = _required_string(payload, "resourceApiName", "resource_api_name")
    scopes = _scopes(payload.get("scopes"), resource_type, resource_api_name)
    return OsdkApplicationResourceRecord(
        resource_id=_new_id("osdk_resource"),
        tenant_id=tenant_id,
        app_id=app_id,
        resource_type=resource_type,
        resource_api_name=resource_api_name,
        scopes=scopes,
        created_at=now,
    )


def _client_record(ctx: RequestContext, app_id: str, client_id: str, now: str) -> OsdkApplicationClientRecord:
    if not client_id.strip():
        raise ValidationFailed("OSDK client id is required")
    return OsdkApplicationClientRecord(
        client_row_id=_new_id("osdk_client"),
        tenant_id=ctx.tenant_id,
        app_id=app_id,
        client_id=client_id.strip(),
        status="active",
        created_at=now,
    )


def _scopes(raw: object, resource_type: OsdkResourceType, resource_api_name: str) -> tuple[str, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise ValidationFailed("OSDK application resource scopes must be a list")
    scopes = tuple(str(item).strip() for item in raw if str(item).strip())
    for scope in scopes:
        _validate_scope(scope, resource_type, resource_api_name)
    return scopes


def _validate_scope(scope: str, resource_type: OsdkResourceType, resource_api_name: str) -> None:
    prefix = f"osdk:{resource_type}:{resource_api_name}:"
    if not scope.startswith(prefix) or scope.rsplit(":", 1)[-1] not in _OPERATIONS:
        raise ValidationFailed("OSDK application resource scope does not match its resource")


def _scope_allowed(
    expected_scope: str, token_scopes: Sequence[str], resources: Sequence[OsdkApplicationResourceRow]
) -> bool:
    if expected_scope not in set(token_scopes) and "osdk:*" not in set(token_scopes):
        return False
    return any(expected_scope in set(row["scopes"]) for row in resources)


def _raise_scope_denied(expected_scope: str, resource_type: str, resource_api_name: str) -> None:
    raise PermissionDenied(
        "OSDK application scope denied",
        details={"requiredScope": expected_scope, "resourceType": resource_type, "resourceApiName": resource_api_name},
    )


def _channel(channel: str) -> str:
    if channel not in _CHANNELS:
        raise ValidationFailed("OSDK release channel must be stable, beta, or latest")
    return channel


def _language(language: str) -> OsdkLanguage:
    if language not in _LANGUAGES:
        raise ValidationFailed("OSDK SDK language must be typescript or python")
    return cast(OsdkLanguage, language)


def _resource_type(value: object) -> OsdkResourceType:
    if not isinstance(value, str) or value not in _RESOURCE_TYPES:
        raise ValidationFailed("OSDK application resource type is unsupported")
    return cast(OsdkResourceType, value)


def _required_string(payload: Mapping[str, object], camel: str, snake: str) -> str:
    value = payload.get(camel, payload.get(snake))
    if not isinstance(value, str) or value.strip() == "":
        raise ValidationFailed("required OSDK application field is missing", details={"field": camel})
    return value.strip()


def _require_api_name(value: str, field: str) -> None:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", value):
        raise ValidationFailed("OSDK application API name is invalid", details={"field": field})


def _require_idempotency_key(value: str) -> None:
    if not value:
        raise ValidationFailed("Idempotency-Key is required for OSDK Developer Console mutations")


def _clean_strings(values: Sequence[str]) -> list[str]:
    return [value.strip() for value in values if value.strip()]


def _string_sequence(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return ()
    return tuple(str(item) for item in value if str(item))


def _positive_ttl(value: int) -> int:
    if value <= 0:
        raise ValidationFailed("OSDK token TTL seconds must be positive")
    return value


def _ttl(value: object, fallback: int) -> int:
    return value if isinstance(value, int) and value > 0 else fallback


def _default_client_id(app_api_name: str) -> str:
    return f"client-{app_api_name}"


def _client_request(
    app_id: str,
    client_id: str,
    redirect_uris: Sequence[str],
    allowed_scopes: Sequence[str],
    access_token_ttl_seconds: int,
    refresh_token_ttl_seconds: int,
) -> RuntimeJsonObject:
    return {
        "appId": app_id,
        "clientId": client_id,
        "redirectUris": _clean_strings(redirect_uris),
        "allowedScopes": _clean_strings(allowed_scopes),
        "accessTokenTtlSeconds": access_token_ttl_seconds,
        "refreshTokenTtlSeconds": refresh_token_ttl_seconds,
    }


def _client_update_request(
    app_id: str,
    client_row_id: str,
    status: str,
    redirect_uris: Sequence[str],
    allowed_scopes: Sequence[str],
    access_token_ttl_seconds: int,
    refresh_token_ttl_seconds: int,
) -> RuntimeJsonObject:
    return {
        "appId": app_id,
        "clientRowId": client_row_id,
        "status": status,
        "redirectUris": _clean_strings(redirect_uris),
        "allowedScopes": _clean_strings(allowed_scopes),
        "accessTokenTtlSeconds": access_token_ttl_seconds,
        "refreshTokenTtlSeconds": refresh_token_ttl_seconds,
    }
