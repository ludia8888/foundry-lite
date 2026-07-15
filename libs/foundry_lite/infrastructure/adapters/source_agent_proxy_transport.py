"""Socket transport for policy-bound Source Agent CONNECT tunnels."""

from __future__ import annotations

import socket
from urllib.parse import SplitResult, urlsplit

from foundry_lite.application.ports.connector_adapter import ConnectorNetworkRoute
from foundry_lite.domain.errors import ValidationFailed

_MAX_PROXY_RESPONSE_BYTES = 8 * 1024


def connect_source_target(
    *,
    target_host: str,
    resolved_host: str,
    target_port: int,
    timeout: float | None,
    source_address: tuple[str, int] | None,
    route: ConnectorNetworkRoute | None,
) -> socket.socket:
    """Open a direct socket or an Agent CONNECT tunnel without silent fallback."""
    if route is None:
        return socket.create_connection((resolved_host, target_port), timeout, source_address)
    if route.mode == "direct":
        _require_direct_route(route, target_host, target_port)
        return socket.create_connection((resolved_host, target_port), timeout, source_address)
    _require_agent_route(route, target_host, target_port)
    return _connect_agent_proxy(route, target_host, target_port, timeout, source_address)


def _require_agent_route(route: ConnectorNetworkRoute, host: str, port: int) -> None:
    if route.mode != "agent_proxy" or not route.proxy_url or not route.agent_id:
        raise _route_error(route, host, port, "AGENT_ROUTE_INVALID", "Agent proxy route is incomplete.")
    if any(_destination_matches(item, host, port) for item in route.allowed_destinations):
        return
    raise _route_error(
        route,
        host,
        port,
        "POLICY_DENIED",
        "Source destination does not match the network policy host and port allowlist.",
    )


def _require_direct_route(route: ConnectorNetworkRoute, host: str, port: int) -> None:
    if not route.allowed_destinations:
        return
    if any(_destination_matches(item, host, port) for item in route.allowed_destinations):
        return
    raise _route_error(
        route,
        host,
        port,
        "POLICY_DENIED",
        "Source destination does not match the direct network policy host and port allowlist.",
    )


def _connect_agent_proxy(
    route: ConnectorNetworkRoute,
    target_host: str,
    target_port: int,
    timeout: float | None,
    source_address: tuple[str, int] | None,
) -> socket.socket:
    proxy_host, proxy_port = _proxy_endpoint(route, target_host, target_port)
    sock: socket.socket | None = None
    try:
        sock = socket.create_connection((proxy_host, proxy_port), timeout, source_address)
        sock.sendall(_connect_request(route, target_host, target_port))
        status = _proxy_response_status(sock)
        if status == 200:
            return sock
        raise _route_error(
            route,
            target_host,
            target_port,
            _proxy_response_flag(status),
            f"Source Agent refused the CONNECT tunnel with HTTP {status}.",
        )
    except ValidationFailed:
        if sock is not None:
            sock.close()
        raise
    except (OSError, TimeoutError) as exc:
        if sock is not None:
            sock.close()
        raise _route_error(
            route,
            target_host,
            target_port,
            "AGENT_UNREACHABLE",
            "Foundry worker could not reach the selected Source Agent proxy.",
        ) from exc


def _proxy_endpoint(route: ConnectorNetworkRoute, host: str, port: int) -> tuple[str, int]:
    split = urlsplit(route.proxy_url or "")
    _validate_proxy_endpoint(split, route, host, port)
    return split.hostname or "", _proxy_port(split, route, host, port)


def _validate_proxy_endpoint(
    split: SplitResult,
    route: ConnectorNetworkRoute,
    host: str,
    port: int,
) -> None:
    if split.scheme != "http" or not split.hostname or split.username or split.password:
        raise _route_error(
            route,
            host,
            port,
            "AGENT_ROUTE_INVALID",
            "Source Agent proxyUrl must be an unauthenticated http URL.",
        )
    if split.path not in {"", "/"} or split.query or split.fragment:
        raise _route_error(
            route,
            host,
            port,
            "AGENT_ROUTE_INVALID",
            "Source Agent proxyUrl cannot contain a path, query, or fragment.",
        )


def _proxy_port(split: SplitResult, route: ConnectorNetworkRoute, host: str, port: int) -> int:
    try:
        return split.port or 80
    except ValueError as exc:
        raise _route_error(route, host, port, "AGENT_ROUTE_INVALID", "Source Agent proxyUrl port is invalid.") from exc


def _connect_request(route: ConnectorNetworkRoute, host: str, port: int) -> bytes:
    authority = f"{host}:{port}"
    return (
        f"CONNECT {authority} HTTP/1.1\r\n"
        f"Host: {authority}\r\n"
        f"X-Foundry-Lite-Agent-Id: {route.agent_id}\r\n"
        "Proxy-Connection: keep-alive\r\n\r\n"
    ).encode("ascii")


def _proxy_response_status(sock: socket.socket) -> int:
    response = bytearray()
    while b"\r\n\r\n" not in response:
        chunk = sock.recv(1024)
        if not chunk:
            raise OSError("Source Agent closed the CONNECT handshake")
        response.extend(chunk)
        if len(response) > _MAX_PROXY_RESPONSE_BYTES:
            raise OSError("Source Agent CONNECT response exceeded limit")
    first_line = bytes(response).split(b"\r\n", 1)[0]
    parts = first_line.split(b" ", 2)
    if len(parts) < 2 or not parts[1].isdigit():
        raise OSError("Source Agent CONNECT response was malformed")
    return int(parts[1])


def _destination_matches(entry: str, host: str, port: int) -> bool:
    value = entry.strip()
    if not value:
        return False
    split = urlsplit(value if "://" in value else f"//{value}")
    expected_host = (split.hostname or "").rstrip(".").lower()
    actual_host = host.rstrip(".").lower()
    try:
        expected_port = split.port
    except ValueError:
        return False
    return expected_host == actual_host and (expected_port is None or expected_port == port)


def _proxy_response_flag(status: int) -> str:
    if status == 403:
        return "AGENT_POLICY_DENIED"
    if status == 503:
        return "AGENT_DRAINING"
    return "UPSTREAM_CONNECT_FAILURE"


def _route_error(
    route: ConnectorNetworkRoute,
    host: str,
    port: int,
    response_flag: str,
    message: str,
) -> ValidationFailed:
    is_agent = route.mode == "agent_proxy"
    evidence = {
        "egressSucceeded": False,
        "origin": "agent-proxy" if is_agent else "connectivity-sidecar",
        "networkType": route.mode,
        "destinationPort": port,
        "responseFlags": response_flag,
        "networkResources": {
            "networkPolicy": route.policy_name,
            "agentId": route.agent_id,
        },
    }
    return ValidationFailed(
        message,
        details={
            "networkStage": "agent_connect",
            "destinationHost": host,
            "networkEvidence": evidence,
        },
    )
