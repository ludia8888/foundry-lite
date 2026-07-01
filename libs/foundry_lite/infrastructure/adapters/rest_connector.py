"""Infrastructure adapter implementation for rest connector."""

from __future__ import annotations

import json
import math
import socket
import ssl
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPMessage, HTTPResponse, HTTPSConnection
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import IO, Protocol, cast
from urllib.error import HTTPError
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPHandler, HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureMode,
)
from foundry_lite.application.ports.connector_adapter import (
    ConnectorRateLimitedError,
    ConnectorSnapshot,
    ConnectorSnapshotRequest,
    RestAuthConfig,
    RestPaginationConfig,
    RestSourceConfig,
)
from foundry_lite.application.ports.secret_provider import REDACTED_VALUE, SecretProvider
from foundry_lite.domain.errors import ValidationFailed

REST_CONNECTOR_PROFILE = "rest-pull-connector"
_TARGET_ATTR = "_foundry_lite_rest_target"


@dataclass(frozen=True)
class _ValidatedHttpTarget:
    url: str
    host: str
    port: int
    resolved_host: str


class RestPullConnectorAdapter:
    """HTTP JSON REST connector that returns one cursor page as a snapshot."""

    profile_name = REST_CONNECTOR_PROFILE

    def __init__(
        self,
        *,
        secret_provider: SecretProvider | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._secret_provider = secret_provider
        self._clock = clock
        self._rate_limit_lock = threading.Lock()
        self._last_request_at: dict[tuple[str, str, str], float] = {}

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
        self._enforce_rate_limit(request, config)
        payload = _load_json(
            _http_get(
                _request_url(config, request.cursor),
                _auth_headers(config.auth, self._secret_provider),
                allow_private_network=config.allow_private_network,
                max_response_bytes=config.max_response_bytes,
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

    def _enforce_rate_limit(self, request: ConnectorSnapshotRequest, config: RestSourceConfig) -> None:
        if config.rate_limit_per_minute is None:
            return
        if config.rate_limit_per_minute <= 0:
            raise ValidationFailed(
                "REST connector rateLimitPerMinute must be positive",
                details={"rateLimitPerMinute": config.rate_limit_per_minute},
            )
        interval_seconds = 60.0 / config.rate_limit_per_minute
        now = self._clock()
        key = (request.tenant_id, request.connector_name, request.resource_name)
        with self._rate_limit_lock:
            last_request_at = self._last_request_at.get(key)
            if last_request_at is not None:
                wait_seconds = interval_seconds - (now - last_request_at)
                if wait_seconds > 0:
                    raise _client_rate_limited(wait_seconds, request)
            self._last_request_at[key] = now


def _required_rest_config(config: RestSourceConfig | None) -> RestSourceConfig:
    if config is None:
        raise ValidationFailed("REST connector requires source config")
    if not config.base_url or not config.resource_path:
        raise ValidationFailed("REST connector requires baseUrl and resourcePath")
    if config.max_response_bytes <= 0:
        raise ValidationFailed(
            "REST connector maxResponseBytes must be positive",
            details={"maxResponseBytes": config.max_response_bytes},
        )
    _require_replayable_pagination(config.pagination)
    return config


def _require_replayable_pagination(pagination: RestPaginationConfig) -> None:
    """Fail closed when a REST source cannot offer replay-safe cursor semantics."""
    if pagination.strategy == "cursor":
        return
    raise ValidationFailed(
        "REST connector page-number pagination is non-replayable; use a stable cursor source",
        details={"pagination_strategy": pagination.strategy, "non_replayable": True},
    )


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


def _auth_headers(auth: RestAuthConfig, secret_provider: SecretProvider | None) -> dict[str, str]:
    if auth.mode == "none":
        return {}
    if auth.mode == "bearer":
        token = _secret_ref_or_value(auth.token, auth.token_secret_ref, secret_provider)
        if token:
            return {"Authorization": f"Bearer {token}"}
    if auth.mode == "header" and auth.header_name:
        header_value = _secret_ref_or_value(auth.header_value, auth.header_value_secret_ref, secret_provider)
        if header_value:
            return {auth.header_name: header_value}
    raise ValidationFailed("REST connector auth config is incomplete")


def _secret_ref_or_value(
    value: str | None,
    secret_ref: str | None,
    secret_provider: SecretProvider | None,
) -> str | None:
    configured_value = _configured_string(value)
    configured_secret_ref = _configured_string(secret_ref)
    if configured_value and configured_secret_ref:
        raise ValidationFailed(
            "REST connector auth config cannot mix direct secret value and secretRef",
            details={"secret_ref": configured_secret_ref, "secret_value": REDACTED_VALUE},
        )
    if configured_secret_ref is None:
        return configured_value
    if secret_provider is None:
        raise ValidationFailed(
            "REST connector auth secretRef requires SecretProvider",
            details={"secret_ref": configured_secret_ref, "secret_value": REDACTED_VALUE},
        )
    return secret_provider.get_secret(configured_secret_ref).value


def _configured_string(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _http_get(
    url: str,
    headers: Mapping[str, str],
    *,
    allow_private_network: bool = False,
    max_response_bytes: int,
) -> bytes:
    target = _validated_http_target(url, allow_private_network=allow_private_network)
    request = Request(target.url, headers=dict(headers), method="GET")
    setattr(request, _TARGET_ATTR, target)
    try:
        with _open_http_request(request, allow_private_network=allow_private_network) as response:
            return _read_bounded_response(response, max_bytes=max_response_bytes)
    except HTTPError as exc:
        if exc.code == 429:
            retry_after = exc.headers.get("Retry-After")
            raise ConnectorRateLimitedError(
                retry_after,
                adapter_profile=REST_CONNECTOR_PROFILE,
                operation="snapshot",
            ) from exc
        raise ValidationFailed("REST connector request failed", details={"status": exc.code, "url": url}) from exc


class _ReadableHTTPResponse(AbstractContextManager["_ReadableHTTPResponse"], Protocol):
    """Small protocol for urllib responses used by the REST connector."""

    def read(self, _: int | None = None) -> bytes: ...


class _TunnelableHTTPConnection(Protocol):
    source_address: tuple[str, int] | None
    _tunnel_host: str | None

    def _tunnel(self) -> None: ...


class _TunnelableHTTPSConnection(_TunnelableHTTPConnection, Protocol):
    _context: ssl.SSLContext


class _ContextHTTPSHandler(Protocol):
    _context: ssl.SSLContext | None


def _read_bounded_response(response: _ReadableHTTPResponse, *, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(64 * 1024, max_bytes - total + 1))
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > max_bytes:
            raise _response_too_large(max_bytes=max_bytes, bytes_read=total)
        chunks.append(chunk)


def _response_too_large(*, max_bytes: int, bytes_read: int) -> AdapterError:
    return AdapterError(
        AdapterFailure(
            adapter_profile=REST_CONNECTOR_PROFILE,
            operation="snapshot",
            kind="validation",
            is_retryable=False,
            operator_message="REST connector response exceeded maxResponseBytes; reduce source page size.",
            details={"maxResponseBytes": max_bytes, "bytesRead": bytes_read},
        )
    )


def _client_rate_limited(wait_seconds: float, request: ConnectorSnapshotRequest) -> AdapterError:
    retry_after_seconds = max(1, math.ceil(wait_seconds))
    return AdapterError(
        AdapterFailure(
            adapter_profile=REST_CONNECTOR_PROFILE,
            operation="snapshot",
            kind="rate_limited",
            is_retryable=True,
            timeout_seconds=retry_after_seconds,
            operator_message="REST connector local rate limit reached; retry after the configured window.",
            details={
                "tenantId": request.tenant_id,
                "connectorName": request.connector_name,
                "resourceName": request.resource_name,
                "retryAfterSeconds": retry_after_seconds,
            },
        )
    )


class _ValidatingRedirectHandler(HTTPRedirectHandler):
    """Re-run private-network validation for every HTTP redirect target."""

    def __init__(self, allow_private_network: bool) -> None:
        super().__init__()
        self._allow_private_network = allow_private_network

    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> Request | None:
        target = _validated_http_target(newurl, allow_private_network=self._allow_private_network)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            setattr(redirected, _TARGET_ATTR, target)
        return redirected


class _PinnedHTTPHandler(HTTPHandler):
    def __init__(self, allow_private_network: bool) -> None:
        super().__init__()
        self._allow_private_network = allow_private_network

    def http_open(self, req: Request) -> HTTPResponse:
        target = _target_for_request(req, allow_private_network=self._allow_private_network)
        return self.do_open(_pinned_http_connection(target), req)


class _PinnedHTTPSHandler(HTTPSHandler):
    def __init__(self, allow_private_network: bool) -> None:
        super().__init__()
        self._allow_private_network = allow_private_network

    def https_open(self, req: Request) -> HTTPResponse:
        target = _target_for_request(req, allow_private_network=self._allow_private_network)
        handler = cast(_ContextHTTPSHandler, self)
        return self.do_open(_pinned_https_connection(target), req, context=handler._context)


def _open_http_request(request: Request, *, allow_private_network: bool) -> _ReadableHTTPResponse:
    """Open a request with redirect validation and pinned-IP transport."""
    opener = build_opener(
        ProxyHandler({}),
        _PinnedHTTPHandler(allow_private_network),
        _PinnedHTTPSHandler(allow_private_network),
        _ValidatingRedirectHandler(allow_private_network),
    )
    return cast(_ReadableHTTPResponse, opener.open(request, timeout=5))


def _target_for_request(req: Request, *, allow_private_network: bool) -> _ValidatedHttpTarget:
    target = getattr(req, _TARGET_ATTR, None)
    if isinstance(target, _ValidatedHttpTarget):
        return target
    return _validated_http_target(req.full_url, allow_private_network=allow_private_network)


def _pinned_http_connection(target: _ValidatedHttpTarget) -> type[HTTPConnection]:
    class _PinnedHTTPConnection(HTTPConnection):
        def connect(self) -> None:
            connection = cast(_TunnelableHTTPConnection, self)
            self.sock = socket.create_connection(
                (target.resolved_host, target.port),
                self.timeout,
                connection.source_address,
            )
            if connection._tunnel_host:
                connection._tunnel()

    return _PinnedHTTPConnection


def _pinned_https_connection(target: _ValidatedHttpTarget) -> type[HTTPSConnection]:
    class _PinnedHTTPSConnection(HTTPSConnection):
        def connect(self) -> None:
            connection = cast(_TunnelableHTTPSConnection, self)
            sock = socket.create_connection(
                (target.resolved_host, target.port),
                self.timeout,
                connection.source_address,
            )
            tunnel_host = connection._tunnel_host
            if tunnel_host:
                self.sock = sock
                connection._tunnel()
                server_hostname = tunnel_host
            else:
                server_hostname = target.host
            self.sock = connection._context.wrap_socket(sock, server_hostname=server_hostname)

    return _PinnedHTTPSConnection


def _validated_http_url(url: str, *, allow_private_network: bool = False) -> str:
    return _validated_http_target(url, allow_private_network=allow_private_network).url


def _validated_http_target(url: str, *, allow_private_network: bool = False) -> _ValidatedHttpTarget:
    split = urlsplit(url)
    if split.scheme not in {"http", "https"} or not split.netloc:
        raise ValidationFailed("REST connector URL must use http or https", details={"url": url})
    host = split.hostname
    if host is None:
        raise ValidationFailed("REST connector URL must include a host", details={"url": url})
    port = _validated_port(url) or (443 if split.scheme == "https" else 80)
    normalized_host = unquote(host.strip("[]").rstrip(".")).lower()
    if not allow_private_network:
        _reject_private_literal_host(url, host, normalized_host)
    target_addresses = _resolved_target_addresses(normalized_host, port)
    if not allow_private_network:
        _reject_private_resolved_host(url, host, normalized_host, target_addresses)
    return _ValidatedHttpTarget(url=url, host=host, port=port, resolved_host=str(target_addresses[0]))


def _reject_private_literal_host(url: str, host: str, normalized_host: str) -> None:
    """Reject private/local hostnames and IP literals before DNS resolution (SSRF guard)."""
    hostname_reason = _private_hostname_reason(normalized_host)
    if hostname_reason is not None:
        raise ValidationFailed(
            "REST connector URL cannot target private or local network addresses",
            details={"url": url, "host": host, "reason": hostname_reason},
        )
    literal = _ip_literal(normalized_host)
    if literal is None:
        return
    literal_reason = _non_global_ip_reason(literal)
    if literal_reason is not None:
        raise ValidationFailed(
            "REST connector URL cannot target private or local network addresses",
            details={"url": url, "host": host, "reason": literal_reason},
        )


def _reject_private_resolved_host(
    url: str,
    host: str,
    normalized_host: str,
    target_addresses: tuple[IPv4Address | IPv6Address, ...],
) -> None:
    """Reject hosts whose resolved addresses fall in private/local network ranges (SSRF guard)."""
    reason = _private_network_reason_for_addresses(normalized_host, target_addresses)
    if reason is not None:
        raise ValidationFailed(
            "REST connector URL cannot target private or local network addresses",
            details={"url": url, "host": host, "reason": reason},
        )


def _private_network_reason_for_addresses(
    normalized_host: str,
    target_addresses: tuple[IPv4Address | IPv6Address, ...],
) -> str | None:
    hostname_reason = _private_hostname_reason(normalized_host)
    if hostname_reason is not None:
        return hostname_reason
    literal = _ip_literal(normalized_host)
    if literal is not None:
        return _non_global_ip_reason(literal)
    for resolved in target_addresses:
        reason = _non_global_ip_reason(resolved)
        if reason is not None:
            return f"resolved_{reason}"
    return None


def _resolved_target_addresses(host: str, port: int) -> tuple[IPv4Address | IPv6Address, ...]:
    resolved = _resolved_addresses(host, port)
    if not resolved:
        raise ValidationFailed(
            "REST connector URL host could not be resolved",
            details={"host": host},
        )
    return resolved


def _validated_port(url: str) -> int | None:
    try:
        return urlsplit(url).port
    except ValueError as exc:
        raise ValidationFailed("REST connector URL has an invalid port", details={"url": url}) from exc


def _private_network_reason(host: str, port: int | None) -> str | None:
    normalized = unquote(host.strip("[]").rstrip(".")).lower()
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
        return _legacy_ipv4_literal(host)


def _legacy_ipv4_literal(host: str) -> IPv4Address | None:
    """Parse browser-accepted IPv4 forms that urllib leaves as hostnames."""
    if ":" in host:
        return None
    if host.isdecimal():
        return _ipv4_from_int(host, 10)
    parts = host.split(".")
    if len(parts) != 4:
        return None
    parsed: list[int] = []
    for part in parts:
        value = _legacy_ipv4_part(part)
        if value is None or value > 255:
            return None
        parsed.append(value)
    return IPv4Address(".".join(str(part) for part in parsed))


def _ipv4_from_int(value: str, base: int) -> IPv4Address | None:
    try:
        return IPv4Address(int(value, base))
    except ValueError:
        return None


def _legacy_ipv4_part(part: str) -> int | None:
    if part.startswith("0x"):
        return _int_part(part, 16)
    if len(part) > 1 and part.startswith("0"):
        return _int_part(part, 8)
    if part.isdecimal():
        return _int_part(part, 10)
    return None


def _int_part(value: str, base: int) -> int | None:
    try:
        return int(value, base)
    except ValueError:
        return None


def _resolved_addresses(host: str, port: int | None) -> tuple[IPv4Address | IPv6Address, ...]:
    try:
        infos = socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValidationFailed("REST connector URL host could not be resolved", details={"host": host}) from exc
    addresses: list[IPv4Address | IPv6Address] = []
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        address = ip_address(str(sockaddr[0]))
        if address not in addresses:
            addresses.append(address)
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
