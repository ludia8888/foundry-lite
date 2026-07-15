"""Infrastructure adapter implementation for rest connector."""

from __future__ import annotations

import base64
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
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPHandler, HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureMode,
)
from foundry_lite.application.ports.connector_adapter import (
    ConnectorNetworkRoute,
    ConnectorRateLimitedError,
    ConnectorSnapshot,
    ConnectorSnapshotRequest,
    RestAuthConfig,
    RestPaginationConfig,
    RestSourceConfig,
)
from foundry_lite.application.ports.secret_provider import REDACTED_VALUE, SecretProvider
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure.adapters.source_agent_proxy_transport import connect_source_target

REST_CONNECTOR_PROFILE = "rest-pull-connector"
_TARGET_ATTR = "_foundry_lite_rest_target"


@dataclass(frozen=True)
class _ValidatedHttpTarget:
    url: str
    host: str
    port: int
    resolved_host: str


@dataclass(frozen=True)
class _HttpGetResult:
    payload: bytes
    network_evidence: Mapping[str, object]


@dataclass(frozen=True)
class _SnapshotPages:
    rows: tuple[Mapping[str, object], ...]
    cursor: Mapping[str, object] | None
    network_evidence: Mapping[str, object]


class RestPullConnectorAdapter:
    """HTTP JSON connector with bounded, replay-safe cursor page collection."""

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
        pages = _collect_snapshot_pages(
            request,
            config,
            _auth_headers(config.auth, self._secret_provider),
        )
        return ConnectorSnapshot(
            connector_name=request.connector_name,
            resource_name=request.resource_name,
            rows=pages.rows,
            schema={"columns": _schema_columns(config, list(pages.rows))},
            cursor=pages.cursor,
            source_watermark=request.request_id,
            network_evidence=pages.network_evidence,
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


def _collect_snapshot_pages(
    request: ConnectorSnapshotRequest,
    config: RestSourceConfig,
    headers: Mapping[str, str],
) -> _SnapshotPages:
    rows: list[Mapping[str, object]] = []
    evidence: list[Mapping[str, object]] = []
    cursor = request.cursor
    seen = {_cursor_marker(cursor)} if cursor is not None else set()
    for page_index in range(config.pagination.max_pages_per_snapshot):
        response = _fetch_snapshot_page(request, config, headers, cursor, page_index)
        payload = _load_json(response.payload)
        rows.extend(_rows_from_payload(payload, config.pagination))
        evidence.append(response.network_evidence)
        cursor = _next_cursor(payload, config.pagination)
        if cursor is None:
            break
        marker = _cursor_marker(cursor)
        if marker in seen:
            raise ValidationFailed("REST connector pagination cursor repeated")
        seen.add(marker)
    return _SnapshotPages(tuple(rows), cursor, _aggregate_network_evidence(evidence))


def _fetch_snapshot_page(
    request: ConnectorSnapshotRequest,
    config: RestSourceConfig,
    headers: Mapping[str, str],
    cursor: Mapping[str, object] | None,
    page_index: int,
) -> _HttpGetResult:
    return _http_get(
        _request_url(config, cursor),
        headers,
        allow_private_network=config.allow_private_network,
        max_response_bytes=config.max_response_bytes,
        route=request.network_route,
        connection_id=_connection_id(request, page_index),
    )


def _cursor_marker(cursor: Mapping[str, object]) -> str:
    return json.dumps(dict(cursor), sort_keys=True, separators=(",", ":"), default=str)


def _aggregate_network_evidence(items: list[Mapping[str, object]]) -> Mapping[str, object]:
    if len(items) == 1:
        return dict(items[0])
    first = dict(items[0])
    first["pageCount"] = len(items)
    first["connectionCount"] = len(items)
    first["bytesSent"] = sum(_evidence_int(item, "bytesSent") for item in items)
    first["bytesReceived"] = sum(_evidence_int(item, "bytesReceived") for item in items)
    first["durationMs"] = sum(_evidence_int(item, "durationMs") for item in items)
    first["pageConnections"] = [item.get("connectionId") for item in items]
    return first


def _evidence_int(item: Mapping[str, object], key: str) -> int:
    value = item.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


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
    if not 1 <= config.pagination.max_pages_per_snapshot <= 100:
        raise ValidationFailed(
            "REST connector maxPagesPerSnapshot must be between 1 and 100",
            details={"maxPagesPerSnapshot": config.pagination.max_pages_per_snapshot},
        )
    _require_replayable_pagination(config.pagination)
    return config


def _require_replayable_pagination(pagination: RestPaginationConfig) -> None:
    """Fail closed when a REST source cannot offer replay-safe cursor semantics."""
    if pagination.strategy in {"cursor", "next_link"}:
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
    if config.pagination.strategy == "next_link":
        return _same_origin_next_link(url, cursor_value)
    split = urlsplit(url)
    query = dict(parse_qsl(split.query, keep_blank_values=True))
    query[config.pagination.cursor_query_param] = cursor_value
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


def _same_origin_next_link(initial_url: str, next_link: str) -> str:
    resolved = urljoin(initial_url, next_link)
    if _url_origin(initial_url) != _url_origin(resolved):
        raise ValidationFailed(
            "REST connector next link must keep the configured origin",
            details={"nextLinkOrigin": _display_origin(resolved)},
        )
    return resolved


def _url_origin(url: str) -> tuple[str, str, int]:
    split = urlsplit(url)
    host = (split.hostname or "").rstrip(".").lower()
    port = split.port or (443 if split.scheme.lower() == "https" else 80)
    return split.scheme.lower(), host, port


def _display_origin(url: str) -> str:
    scheme, host, port = _url_origin(url)
    return f"{scheme}://{host}:{port}"


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
    if auth.mode == "basic":
        credentials = _secret_ref_or_value(
            auth.basic_credentials,
            auth.basic_credentials_secret_ref,
            secret_provider,
        )
        if credentials and _is_basic_credentials(credentials):
            encoded = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
            return {"Authorization": f"Basic {encoded}"}
    if auth.mode == "header" and auth.header_name:
        header_value = _secret_ref_or_value(auth.header_value, auth.header_value_secret_ref, secret_provider)
        if header_value:
            return {auth.header_name: header_value}
    raise ValidationFailed("REST connector auth config is incomplete")


def _is_basic_credentials(value: str) -> bool:
    if ":" in value and not value.startswith(":"):
        return True
    raise ValidationFailed(
        "REST connector basic auth secret must use username:password format",
        details={"secret_value": REDACTED_VALUE},
    )


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
    route: ConnectorNetworkRoute | None,
    connection_id: str,
) -> _HttpGetResult:
    target = _validated_http_target(url, allow_private_network=allow_private_network)
    request = Request(target.url, headers=dict(headers), method="GET")
    setattr(request, _TARGET_ATTR, target)
    started_at = time.monotonic()
    try:
        with _open_http_request(request, allow_private_network=allow_private_network, route=route) as response:
            payload = _read_bounded_response(response, max_bytes=max_response_bytes)
        evidence = _network_evidence(
            target,
            route,
            connection_id=connection_id,
            duration_ms=_duration_ms(started_at),
            response_flags="NONE",
            bytes_sent=_estimated_request_bytes(request),
            bytes_received=len(payload),
            is_egress_succeeded=True,
        )
        return _HttpGetResult(payload, evidence)
    except HTTPError as exc:
        evidence = _network_evidence(
            target,
            route,
            connection_id=connection_id,
            duration_ms=_duration_ms(started_at),
            response_flags="HTTP_ERROR",
            bytes_sent=_estimated_request_bytes(request),
            bytes_received=0,
            is_egress_succeeded=True,
        )
        if exc.code == 429:
            retry_after = exc.headers.get("Retry-After")
            raise ConnectorRateLimitedError(
                retry_after,
                adapter_profile=REST_CONNECTOR_PROFILE,
                operation="snapshot",
            ) from exc
        raise ValidationFailed(
            "REST connector request failed",
            details={"status": exc.code, "networkStage": "http", "networkEvidence": evidence},
        ) from exc
    except ValidationFailed as exc:
        raise _enriched_transport_error(exc, target, route, connection_id, request, started_at) from exc
    except URLError as exc:
        raise _transport_error(target, route, connection_id, request, started_at, exc) from exc


def _connection_id(request: ConnectorSnapshotRequest, page_index: int = 0) -> str:
    base = f"{request.request_id}:{request.connector_name}:{request.resource_name}"
    return base if page_index == 0 else f"{base}:page-{page_index + 1}"


def _duration_ms(started_at: float) -> int:
    return max(0, round((time.monotonic() - started_at) * 1000))


def _estimated_request_bytes(request: Request) -> int:
    request_line = f"GET {request.selector} HTTP/1.1\r\n"
    headers = sum(len(key) + len(value) + 4 for key, value in request.header_items())
    return len(request_line.encode("utf-8")) + headers + 2


def _network_evidence(
    target: _ValidatedHttpTarget,
    route: ConnectorNetworkRoute | None,
    *,
    connection_id: str,
    duration_ms: int,
    response_flags: str,
    bytes_sent: int,
    bytes_received: int,
    is_egress_succeeded: bool,
) -> dict[str, object]:
    is_agent = route is not None and route.mode == "agent_proxy"
    resources = {
        "networkPolicy": route.policy_name if route is not None else None,
        "agentId": route.agent_id if route is not None else None,
    }
    return {
        "connectionId": connection_id,
        "egressSucceeded": is_egress_succeeded,
        "responseFlags": response_flags,
        "bytesSent": bytes_sent,
        "bytesReceived": bytes_received,
        "durationMs": duration_ms,
        "destinationPort": target.port,
        "origin": "agent-proxy" if is_agent else "connectivity-sidecar",
        "networkType": "agent_proxy" if is_agent else "direct",
        "networkResources": resources,
    }


def _enriched_transport_error(
    error: ValidationFailed,
    target: _ValidatedHttpTarget,
    route: ConnectorNetworkRoute | None,
    connection_id: str,
    request: Request,
    started_at: float,
) -> ValidationFailed:
    details = dict(error.details)
    existing = details.get("networkEvidence")
    if isinstance(existing, Mapping):
        evidence = dict(existing)
        evidence.setdefault("connectionId", connection_id)
        evidence.setdefault("bytesSent", _estimated_request_bytes(request))
        evidence.setdefault("bytesReceived", 0)
        evidence.setdefault("durationMs", _duration_ms(started_at))
    else:
        evidence = _network_evidence(
            target,
            route,
            connection_id=connection_id,
            duration_ms=_duration_ms(started_at),
            response_flags="UPSTREAM_CONNECT_FAILURE",
            bytes_sent=_estimated_request_bytes(request),
            bytes_received=0,
            is_egress_succeeded=False,
        )
    details["networkEvidence"] = evidence
    return ValidationFailed(error.message, details=details)


def _transport_error(
    target: _ValidatedHttpTarget,
    route: ConnectorNetworkRoute | None,
    connection_id: str,
    request: Request,
    started_at: float,
    error: URLError,
) -> ValidationFailed:
    reason = error.reason
    is_tls_error = isinstance(reason, ssl.SSLError)
    evidence = _network_evidence(
        target,
        route,
        connection_id=connection_id,
        duration_ms=_duration_ms(started_at),
        response_flags="TLS_ERROR" if is_tls_error else "UPSTREAM_CONNECT_FAILURE",
        bytes_sent=_estimated_request_bytes(request),
        bytes_received=0,
        is_egress_succeeded=is_tls_error,
    )
    message = "REST connector TLS handshake failed" if is_tls_error else "REST connector endpoint was unreachable"
    return ValidationFailed(
        message,
        details={"networkStage": "tls" if is_tls_error else "tcp", "networkEvidence": evidence},
    )


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
    def __init__(
        self,
        allow_private_network: bool,
        route: ConnectorNetworkRoute | None = None,
    ) -> None:
        super().__init__()
        self._allow_private_network = allow_private_network
        self._route = route

    def http_open(self, req: Request) -> HTTPResponse:
        target = _target_for_request(req, allow_private_network=self._allow_private_network)
        return self.do_open(_pinned_http_connection(target, self._route), req)


class _PinnedHTTPSHandler(HTTPSHandler):
    def __init__(
        self,
        allow_private_network: bool,
        route: ConnectorNetworkRoute | None = None,
    ) -> None:
        super().__init__()
        self._allow_private_network = allow_private_network
        self._route = route

    def https_open(self, req: Request) -> HTTPResponse:
        target = _target_for_request(req, allow_private_network=self._allow_private_network)
        handler = cast(_ContextHTTPSHandler, self)
        return self.do_open(_pinned_https_connection(target, self._route), req, context=handler._context)


def _open_http_request(
    request: Request,
    *,
    allow_private_network: bool,
    route: ConnectorNetworkRoute | None,
) -> _ReadableHTTPResponse:
    """Open a request with redirect validation and pinned-IP transport."""
    opener = build_opener(
        ProxyHandler({}),
        _PinnedHTTPHandler(allow_private_network, route),
        _PinnedHTTPSHandler(allow_private_network, route),
        _ValidatingRedirectHandler(allow_private_network),
    )
    return cast(_ReadableHTTPResponse, opener.open(request, timeout=5))


def _target_for_request(req: Request, *, allow_private_network: bool) -> _ValidatedHttpTarget:
    target = getattr(req, _TARGET_ATTR, None)
    if isinstance(target, _ValidatedHttpTarget):
        return target
    return _validated_http_target(req.full_url, allow_private_network=allow_private_network)


def _pinned_http_connection(
    target: _ValidatedHttpTarget,
    route: ConnectorNetworkRoute | None = None,
) -> type[HTTPConnection]:
    class _PinnedHTTPConnection(HTTPConnection):
        def connect(self) -> None:
            connection = cast(_TunnelableHTTPConnection, self)
            self.sock = connect_source_target(
                target_host=target.host,
                resolved_host=target.resolved_host,
                target_port=target.port,
                timeout=self.timeout,
                source_address=connection.source_address,
                route=route,
            )
            if connection._tunnel_host:
                connection._tunnel()

    return _PinnedHTTPConnection


def _pinned_https_connection(
    target: _ValidatedHttpTarget,
    route: ConnectorNetworkRoute | None = None,
) -> type[HTTPSConnection]:
    class _PinnedHTTPSConnection(HTTPSConnection):
        def connect(self) -> None:
            connection = cast(_TunnelableHTTPSConnection, self)
            sock = connect_source_target(
                target_host=target.host,
                resolved_host=target.resolved_host,
                target_port=target.port,
                timeout=self.timeout,
                source_address=connection.source_address,
                route=route,
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
    if path in payload:
        return payload[path]
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
