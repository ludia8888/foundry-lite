"""Validation and normalized payload builders for Generic REST connector onboarding."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from foundry_lite.application.ports import (
    ConnectorConnectionRecord,
    ConnectorConnectionRow,
    ConnectorConnectionUpdate,
    ConnectorNetworkRoute,
    ConnectorResourceRecord,
    ConnectorResourceRow,
    ConnectorSnapshotRequest,
    RestAuthConfig,
    RestPaginationConfig,
    RestSourceConfig,
)
from foundry_lite.application.ports.connector_adapter import RestPaginationStrategy
from foundry_lite.application.ports.connector_registry_repository import ConnectorConnectionStatus
from foundry_lite.application.primitives import (
    SQL_IDENTIFIER_PATTERN,
    _dataset_ref_parts,
    _json_hash,
    _new_id,
    _now,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.domain.http_headers import is_safe_custom_header_name
from foundry_lite.domain.http_targets import is_relative_http_resource_path, is_safe_http_base_url


@dataclass(frozen=True)
class ConnectorBundle:
    """Resolved connector connection, resource, fingerprint, and REST runtime config."""

    connection: ConnectorConnectionRow
    resource: ConnectorResourceRow
    config_fingerprint: str
    rest: RestSourceConfig


def _connection_record(
    ctx: RequestContext,
    connector_name: str,
    display_name: str,
    base_url: str,
    auth: Mapping[str, object],
    rate_limit_per_minute: int | None,
    allow_private_network: bool,
) -> ConnectorConnectionRecord:
    normalized = _connection_config(
        connector_name,
        display_name,
        base_url,
        auth,
        rate_limit_per_minute,
        allow_private_network,
        "active",
    )
    created_at = _now()
    return ConnectorConnectionRecord(
        connection_id=_new_id("connector"),
        tenant_id=ctx.tenant_id,
        connector_name=str(normalized["connectorName"]),
        display_name=str(normalized["displayName"]),
        kind="rest",
        base_url=str(normalized["baseUrl"]),
        auth=cast(Mapping[str, object], normalized["auth"]),
        rate_limit_per_minute=cast(int | None, normalized["rateLimitPerMinute"]),
        allow_private_network=bool(normalized["allowPrivateNetwork"]),
        status="active",
        config_fingerprint=_fingerprint(normalized),
        created_at=created_at,
        updated_at=created_at,
    )


def _connection_patch(
    current: ConnectorConnectionRow,
    display_name: str | None,
    base_url: str | None,
    auth: Mapping[str, object] | None,
    rate_limit_per_minute: int | None,
    allow_private_network: bool | None,
    status: str | None,
) -> ConnectorConnectionUpdate:
    normalized = _connection_config(
        current["connector_name"],
        display_name or current["display_name"],
        base_url or current["base_url"],
        auth or current["auth"],
        rate_limit_per_minute if rate_limit_per_minute is not None else current["rate_limit_per_minute"],
        allow_private_network if allow_private_network is not None else current["allow_private_network"],
        _connection_status(status or current["status"]),
    )
    return ConnectorConnectionUpdate(
        display_name=str(normalized["displayName"]),
        base_url=str(normalized["baseUrl"]),
        auth=cast(Mapping[str, object], normalized["auth"]),
        rate_limit_per_minute=cast(int | None, normalized["rateLimitPerMinute"]),
        allow_private_network=bool(normalized["allowPrivateNetwork"]),
        status=cast(ConnectorConnectionStatus, normalized["status"]),
        config_fingerprint=_fingerprint(normalized),
        updated_at=_now(),
    )


def _resource_record(
    ctx: RequestContext,
    connector_name: str,
    resource_name: str,
    dataset_ref: str,
    resource_path: str,
    pagination: Mapping[str, object] | None,
    schema_columns: Sequence[str],
    primary_key: Sequence[str],
) -> ConnectorResourceRecord:
    config = _resource_config(
        connector_name,
        resource_name,
        dataset_ref,
        resource_path,
        pagination,
        schema_columns,
        primary_key,
    )
    created_at = _now()
    return ConnectorResourceRecord(
        resource_id=_new_id("connector_resource"),
        tenant_id=ctx.tenant_id,
        connector_name=str(config["connectorName"]),
        resource_name=str(config["resourceName"]),
        dataset_ref=str(config["datasetRef"]),
        resource_path=str(config["resourcePath"]),
        pagination=cast(Mapping[str, object], config["pagination"]),
        schema_columns=cast(Sequence[str], config["schemaColumns"]),
        primary_key=cast(Sequence[str], config["primaryKey"]),
        config_fingerprint=_fingerprint(config),
        created_at=created_at,
        updated_at=created_at,
    )


def _connection_config(
    connector_name: str,
    display_name: str,
    base_url: str,
    auth: Mapping[str, object],
    rate_limit_per_minute: int | None,
    allow_private_network: bool,
    status: str,
) -> dict[str, object]:
    _require_identifier(connector_name, "connectorName")
    _require_text(display_name, "displayName")
    _require_safe_base_url(base_url)
    _connection_status(status)
    _require_positive_rate_limit(rate_limit_per_minute)
    return {
        "connectorName": connector_name,
        "displayName": display_name,
        "kind": "rest",
        "baseUrl": base_url,
        "auth": _auth_payload(auth),
        "rateLimitPerMinute": rate_limit_per_minute,
        "allowPrivateNetwork": allow_private_network,
        "status": status,
    }


def _resource_config(
    connector_name: str,
    resource_name: str,
    dataset_ref: str,
    resource_path: str,
    pagination: Mapping[str, object] | None,
    schema_columns: Sequence[str],
    primary_key: Sequence[str],
) -> dict[str, object]:
    _require_identifier(connector_name, "connectorName")
    _require_identifier(resource_name, "resourceName")
    _dataset_ref_parts(dataset_ref)
    _require_relative_resource_path(resource_path)
    return {
        "connectorName": connector_name,
        "resourceName": resource_name,
        "datasetRef": dataset_ref,
        "resourcePath": resource_path,
        "pagination": _pagination_payload(pagination or {}),
        "schemaColumns": _string_list(schema_columns, "schemaColumns"),
        "primaryKey": _string_list(primary_key, "primaryKey"),
    }


def _auth_payload(auth: Mapping[str, object]) -> dict[str, object]:
    mode = _auth_mode(auth.get("mode", "none"))
    _reject_raw_secret(auth)
    if mode == "none":
        return {"mode": "none"}
    if mode == "bearer":
        return {"mode": "bearer", "tokenSecretRef": _required_auth_text(auth, "tokenSecretRef")}
    if mode == "basic":
        return {
            "mode": "basic",
            "basicCredentialsSecretRef": _required_auth_text(auth, "basicCredentialsSecretRef"),
        }
    header_name = _required_auth_text(auth, "headerName")
    if not is_safe_custom_header_name(header_name):
        raise ValidationFailed("connector auth headerName is unsafe or reserved", details={"field": "headerName"})
    return {
        "mode": "header",
        "headerName": header_name,
        "headerValueSecretRef": _required_auth_text(auth, "headerValueSecretRef"),
    }


def _pagination_payload(pagination: Mapping[str, object]) -> dict[str, object]:
    strategy = str(pagination.get("strategy", "cursor"))
    if strategy not in {"cursor", "next_link"}:
        raise ValidationFailed(
            "connector pagination strategy must be cursor or next_link",
            details={"strategy": strategy},
        )
    return {
        "itemsPath": str(pagination.get("itemsPath", "items")),
        "nextCursorPath": str(pagination.get("nextCursorPath", "nextCursor")),
        "cursorQueryParam": str(pagination.get("cursorQueryParam", "cursor")),
        "cursorKey": str(pagination.get("cursorKey", "cursor")),
        "strategy": strategy,
        "maxPagesPerSnapshot": _max_pages_per_snapshot(pagination.get("maxPagesPerSnapshot", 1)),
    }


def _rest_source(connection: ConnectorConnectionRow, resource: ConnectorResourceRow) -> RestSourceConfig:
    pagination = resource["pagination"]
    return RestSourceConfig(
        base_url=connection["base_url"],
        resource_path=resource["resource_path"],
        auth=_rest_auth_config(connection["auth"]),
        pagination=RestPaginationConfig(
            items_path=str(pagination.get("itemsPath", "items")),
            next_cursor_path=str(pagination.get("nextCursorPath", "nextCursor")),
            cursor_query_param=str(pagination.get("cursorQueryParam", "cursor")),
            cursor_key=str(pagination.get("cursorKey", "cursor")),
            strategy=cast(RestPaginationStrategy, pagination.get("strategy", "cursor")),
            max_pages_per_snapshot=_max_pages_per_snapshot(pagination.get("maxPagesPerSnapshot", 1)),
        ),
        rate_limit_per_minute=connection["rate_limit_per_minute"],
        schema_columns=tuple(str(item) for item in resource["schema_columns"]),
        allow_private_network=connection["allow_private_network"],
    )


def _rest_auth_config(auth: Mapping[str, object]) -> RestAuthConfig:
    mode = _auth_mode(auth.get("mode", "none"))
    if mode == "bearer":
        return RestAuthConfig(mode="bearer", token_secret_ref=str(auth["tokenSecretRef"]))
    if mode == "basic":
        return RestAuthConfig(
            mode="basic",
            basic_credentials_secret_ref=str(auth["basicCredentialsSecretRef"]),
        )
    if mode == "header":
        return RestAuthConfig(
            mode="header",
            header_name=str(auth["headerName"]),
            header_value_secret_ref=str(auth["headerValueSecretRef"]),
        )
    return RestAuthConfig(mode="none")


def _resolved_config_fingerprint(connection: ConnectorConnectionRow, resource: ConnectorResourceRow) -> str:
    return _fingerprint(
        {
            "connection": _connection_view_without_resources(connection),
            "resource": dict(_resource_row_config(resource)),
        }
    )


def _connection_view_without_resources(connection: ConnectorConnectionRow) -> dict[str, object]:
    return {
        "connectorName": connection["connector_name"],
        "kind": connection["kind"],
        "baseUrl": connection["base_url"],
        "auth": dict(connection["auth"]),
        "rateLimitPerMinute": connection["rate_limit_per_minute"],
        "allowPrivateNetwork": connection["allow_private_network"],
        "status": connection["status"],
    }


def _resource_row_config(resource: ConnectorResourceRow) -> dict[str, object]:
    return {
        "connectorName": resource["connector_name"],
        "resourceName": resource["resource_name"],
        "datasetRef": resource["dataset_ref"],
        "resourcePath": resource["resource_path"],
        "pagination": dict(resource["pagination"]),
        "schemaColumns": list(resource["schema_columns"]),
        "primaryKey": list(resource["primary_key"]),
    }


def _snapshot_request(
    ctx: RequestContext,
    bundle: ConnectorBundle,
    network_route: ConnectorNetworkRoute | None = None,
) -> ConnectorSnapshotRequest:
    return ConnectorSnapshotRequest(
        bundle.connection["connector_name"],
        bundle.resource["resource_name"],
        ctx.tenant_id,
        ctx.request_id,
        None,
        bundle.rest,
        network_route,
    )


def _activity_connector_input(payload: Mapping[str, object]) -> tuple[str, str, str]:
    connector_name = _required_payload_text(payload, "connectorName")
    resource_name = _required_payload_text(payload, "resourceName")
    config_fingerprint = _required_payload_text(payload, "configFingerprint")
    return connector_name, resource_name, config_fingerprint


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _require_idempotency_key(value: str) -> None:
    if value.strip():
        return
    raise ValidationFailed("Idempotency-Key is required")


def _fingerprint(value: Mapping[str, object]) -> str:
    return f"sha256:{_json_hash(value)}"


def _require_identifier(value: str, field: str) -> None:
    if SQL_IDENTIFIER_PATTERN.fullmatch(value):
        return
    raise ValidationFailed(f"{field} must be a safe identifier", details={field: value})


def _require_text(value: str, field: str) -> None:
    if value.strip():
        return
    raise ValidationFailed(f"{field} is required", details={"field": field})


def _require_relative_resource_path(value: str) -> None:
    if is_relative_http_resource_path(value):
        return
    raise ValidationFailed(
        "resourcePath must be a relative HTTP path under the registered connector origin",
        details={"field": "resourcePath"},
    )


def _require_safe_base_url(value: str) -> None:
    if is_safe_http_base_url(value):
        return
    raise ValidationFailed(
        "baseUrl must be an absolute HTTP URL without inline credentials, query, or fragment",
        details={"field": "baseUrl"},
    )


def _required_auth_text(auth: Mapping[str, object], field: str) -> str:
    value = auth.get(field)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValidationFailed("connector auth secretRef is required", details={"field": field})


def _required_payload_text(payload: Mapping[str, object], field: str) -> str:
    value = payload.get(field)
    if isinstance(value, str) and value:
        return value
    raise ValidationFailed("connector workflow payload is missing a required field", details={"field": field})


def _auth_mode(value: object) -> str:
    if value in {"none", "bearer", "basic", "header"}:
        return str(value)
    raise ValidationFailed("unsupported connector auth mode", details={"mode": str(value)})


def _connection_status(value: str) -> str:
    if value in {"active", "disabled"}:
        return value
    raise ValidationFailed("unsupported connector status", details={"status": value})


def _reject_raw_secret(auth: Mapping[str, object]) -> None:
    if auth.get("token") is not None or auth.get("headerValue") is not None:
        raise ValidationFailed("connector onboarding accepts secretRef only", details={"secretValue": "***REDACTED***"})


def _require_positive_rate_limit(value: int | None) -> None:
    if value is None or value > 0:
        return
    raise ValidationFailed("rateLimitPerMinute must be positive", details={"rateLimitPerMinute": value})


def _max_pages_per_snapshot(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise ValidationFailed(
            "maxPagesPerSnapshot must be between 1 and 100",
            details={"maxPagesPerSnapshot": value},
        )
    return value


def _string_list(values: Sequence[str], field: str) -> list[str]:
    result = [str(value) for value in values if str(value)]
    if len(result) != len(values):
        raise ValidationFailed(f"{field} cannot contain blank values", details={"field": field})
    return result
