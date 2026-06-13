from __future__ import annotations

import json
import socket
from collections.abc import Mapping
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import cast
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract, AdapterFailureMode
from foundry_lite.application.ports.connector_adapter import (
    ConnectorRateLimitedError,
    ConnectorSnapshot,
    ConnectorSnapshotRequest,
    RestAuthConfig,
    RestPaginationConfig,
    RestSourceConfig,
)
from foundry_lite.domain.errors import ValidationFailed

REST_CONNECTOR_PROFILE = "rest-pull-connector"


class RestPullConnectorAdapter:
    """HTTP JSON REST connector that returns one cursor page as a snapshot."""

    profile_name = REST_CONNECTOR_PROFILE

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode("snapshot", "validation", False, "REST connector configuration is invalid."),
                AdapterFailureMode(
                    "snapshot",
                    "rate_limited",
                    True,
                    "REST connector was rate limited; retry after the provider window.",
                ),
                AdapterFailureMode(
                    "snapshot",
                    "timeout",
                    True,
                    "REST connector request timed out; retry with the same cursor.",
                    timeout_seconds=5,
                ),
            ),
        )

    def snapshot(self, request: ConnectorSnapshotRequest) -> ConnectorSnapshot:
        config = _required_rest_config(request.rest)
        payload = _load_json(
            _http_get(
                _request_url(config, request.cursor),
                _auth_headers(config.auth),
                allow_private_network=config.allow_private_network,
            )
        )
        rows = _rows_from_payload(payload, config.pagination)
        cursor = _next_cursor(payload, config.pagination)
        return ConnectorSnapshot(
            connector_name=request.connector_name,
            resource_name=request.resource_name,
            rows=tuple(rows),
            schema={"columns": _schema_columns(config, rows)},
            cursor=cursor,
            source_watermark=request.request_id,
        )


def _required_rest_config(config: RestSourceConfig | None) -> RestSourceConfig:
    if config is None:
        raise ValidationFailed("REST connector requires source config")
    if not config.base_url or not config.resource_path:
        raise ValidationFailed("REST connector requires baseUrl and resourcePath")
    return config


def _request_url(config: RestSourceConfig, cursor: Mapping[str, object] | None) -> str:
    base = config.base_url.rstrip("/")
    path = config.resource_path.lstrip("/")
    url = f"{base}/{path}"
    cursor_value = _cursor_value(cursor, config.pagination)
    if cursor_value is None:
        return url
    split = urlsplit(url)
    query = dict(parse_qsl(split.query, keep_blank_values=True))
    query[config.pagination.cursor_query_param] = cursor_value
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


def _cursor_value(cursor: Mapping[str, object] | None, pagination: RestPaginationConfig) -> str | None:
    if cursor is None:
        return None
    value = cursor.get(pagination.cursor_key)
    if value is None:
        return None
    return str(value)


def _auth_headers(auth: RestAuthConfig) -> dict[str, str]:
    if auth.mode == "none":
        return {}
    if auth.mode == "bearer" and auth.token:
        return {"Authorization": f"Bearer {auth.token}"}
    if auth.mode == "header" and auth.header_name and auth.header_value:
        return {auth.header_name: auth.header_value}
    raise ValidationFailed("REST connector auth config is incomplete")


def _http_get(url: str, headers: Mapping[str, str], *, allow_private_network: bool = False) -> bytes:
    safe_url = _validated_http_url(url, allow_private_network=allow_private_network)
    request = Request(safe_url, headers=dict(headers), method="GET")
    try:
        with urlopen(request, timeout=5) as response:  # noqa: S310  # nosec B310
            return cast(bytes, response.read())
    except HTTPError as exc:
        if exc.code == 429:
            retry_after = exc.headers.get("Retry-After")
            raise ConnectorRateLimitedError(
                retry_after,
                adapter_profile=REST_CONNECTOR_PROFILE,
                operation="snapshot",
            ) from exc
        raise ValidationFailed("REST connector request failed", details={"status": exc.code, "url": url}) from exc


def _validated_http_url(url: str, *, allow_private_network: bool = False) -> str:
    split = urlsplit(url)
    if split.scheme not in {"http", "https"} or not split.netloc:
        raise ValidationFailed("REST connector URL must use http or https", details={"url": url})
    host = split.hostname
    if host is None:
        raise ValidationFailed("REST connector URL must include a host", details={"url": url})
    if allow_private_network:
        return url
    reason = _private_network_reason(host, _validated_port(url))
    if reason is not None:
        raise ValidationFailed(
            "REST connector URL cannot target private or local network addresses",
            details={"url": url, "host": host, "reason": reason},
        )
    return url


def _validated_port(url: str) -> int | None:
    try:
        return urlsplit(url).port
    except ValueError as exc:
        raise ValidationFailed("REST connector URL has an invalid port", details={"url": url}) from exc


def _private_network_reason(host: str, port: int | None) -> str | None:
    normalized = host.strip("[]").rstrip(".").lower()
    hostname_reason = _private_hostname_reason(normalized)
    if hostname_reason is not None:
        return hostname_reason
    literal = _ip_literal(normalized)
    if literal is not None:
        return _non_global_ip_reason(literal)
    for resolved in _resolved_addresses(normalized, port):
        reason = _non_global_ip_reason(resolved)
        if reason is not None:
            return f"resolved_{reason}"
    return None


def _private_hostname_reason(host: str) -> str | None:
    if host in {"localhost", "ip6-localhost"} or host.endswith(".localhost"):
        return "local_hostname"
    if host in {"metadata.google.internal", "169.254.169.254.nip.io"}:
        return "metadata_hostname"
    if host.endswith((".local", ".internal")):
        return "internal_hostname"
    return None


def _ip_literal(host: str) -> IPv4Address | IPv6Address | None:
    try:
        return ip_address(host)
    except ValueError:
        return None


def _resolved_addresses(host: str, port: int | None) -> tuple[IPv4Address | IPv6Address, ...]:
    try:
        infos = socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValidationFailed("REST connector URL host could not be resolved", details={"host": host}) from exc
    addresses: set[IPv4Address | IPv6Address] = set()
    for info in infos:
        sockaddr = info[4]
        if sockaddr:
            addresses.add(ip_address(str(sockaddr[0])))
    return tuple(addresses)


def _non_global_ip_reason(address: IPv4Address | IPv6Address) -> str | None:
    if address.is_loopback:
        return "loopback_ip"
    if address.is_link_local:
        return "link_local_ip"
    if address.is_multicast:
        return "multicast_ip"
    if address.is_unspecified:
        return "unspecified_ip"
    if address.is_reserved:
        return "reserved_ip"
    if address.is_private:
        return "private_ip"
    if address.is_global:
        return None
    return "non_global_ip"


def _load_json(body: bytes) -> Mapping[str, object]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationFailed("REST connector returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValidationFailed("REST connector response must be a JSON object")
    return payload


def _rows_from_payload(payload: Mapping[str, object], pagination: RestPaginationConfig) -> list[Mapping[str, object]]:
    value = _json_path(payload, pagination.items_path)
    if not isinstance(value, list):
        raise ValidationFailed("REST connector items path must be a list")
    rows: list[Mapping[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValidationFailed("REST connector item must be an object")
        rows.append({str(key): value for key, value in item.items()})
    return rows


def _next_cursor(payload: Mapping[str, object], pagination: RestPaginationConfig) -> Mapping[str, object] | None:
    value = _json_path(payload, pagination.next_cursor_path)
    if value is None or value == "":
        return None
    return {pagination.cursor_key: value}


def _json_path(payload: Mapping[str, object], path: str) -> object:
    current: object = payload
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _schema_columns(config: RestSourceConfig, rows: list[Mapping[str, object]]) -> list[str]:
    if config.schema_columns:
        return list(config.schema_columns)
    if not rows:
        return []
    return [str(key) for key in rows[0]]
