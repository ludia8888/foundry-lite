"""Local Source Agent that exposes a policy-bounded HTTP CONNECT tunnel."""

from __future__ import annotations

import argparse
import json
import select
import socket
import socketserver
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from ipaddress import ip_address
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

_MAX_HEADER_BYTES = 8 * 1024
_BUFFER_BYTES = 64 * 1024


@dataclass(frozen=True)
class SourceAgentProxyConfig:
    agent_id: str
    display_name: str
    listen_host: str
    listen_port: int
    control_plane_url: str
    allowed_destinations: tuple[str, ...]
    heartbeat_interval_seconds: float = 10.0
    tenant_id: str = "default"
    user_id: str = "source-agent"
    roles: str = "admin"

    def __post_init__(self) -> None:
        _validate_control_plane_url(self.control_plane_url)

    @property
    def proxy_url(self) -> str:
        return f"http://{_advertised_host(self.listen_host)}:{self.listen_port}"


def _advertised_host(listen_host: str) -> str:
    try:
        return "127.0.0.1" if ip_address(listen_host).is_unspecified else listen_host
    except ValueError:
        return listen_host


def _validate_control_plane_url(value: str) -> None:
    split = urlsplit(value)
    is_valid = all(
        (
            split.scheme in {"http", "https"},
            split.hostname,
            not split.username,
            not split.password,
            not split.query,
            not split.fragment,
        )
    )
    if not is_valid:
        raise ValueError("control plane URL must be an unauthenticated HTTP(S) endpoint without query or fragment")


class SourceAgentProxyServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, config: SourceAgentProxyConfig) -> None:
        self.config = config
        super().__init__((config.listen_host, config.listen_port), _SourceAgentConnectHandler)


class _SourceAgentConnectHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        request_line, headers, buffered = _read_connect_request(self.request)
        method, authority = _request_target(request_line)
        if method != "CONNECT":
            _send_status(self.request, 405, "CONNECT required")
            return
        config = _server_config(self.server)
        if headers.get("x-foundry-lite-agent-id") != config.agent_id:
            _send_status(self.request, 403, "Agent identity mismatch")
            return
        host, port = _authority(authority)
        if not _destination_allowed(config.allowed_destinations, host, port):
            _send_status(self.request, 403, "Destination denied")
            return
        upstream = _connect_upstream(self.request, host, port)
        if upstream is None:
            return
        try:
            _send_status(self.request, 200, "Connection established")
            if buffered:
                upstream.sendall(buffered)
            _relay(self.request, upstream)
        finally:
            upstream.close()


def run_source_agent_proxy(config: SourceAgentProxyConfig) -> None:
    with SourceAgentProxyServer(config) as server:
        heartbeat = threading.Thread(target=_heartbeat_loop, args=(config,), daemon=True)
        heartbeat.start()
        print(_event("source_agent.started", config, status="ready"), flush=True)
        try:
            server.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            pass
        finally:
            server.shutdown()
            print(_event("source_agent.stopped", config, status="stopped"), flush=True)


def _heartbeat_loop(config: SourceAgentProxyConfig) -> None:
    is_registered = False
    while True:
        try:
            if not is_registered:
                _register_agent(config)
                is_registered = True
            _heartbeat_agent(config)
        except (HTTPError, URLError, OSError, ValueError) as exc:
            print(
                _event("source_agent.control_plane_error", config, status="retrying", error=type(exc).__name__),
                flush=True,
            )
        time.sleep(config.heartbeat_interval_seconds)


def _register_agent(config: SourceAgentProxyConfig) -> None:
    payload = {
        "agentId": config.agent_id,
        "displayName": config.display_name,
        "mode": "agent_proxy",
        "capabilities": {"transparentTcpTunnel": True, "httpConnect": True},
        "networkSummary": {
            "proxyUrl": config.proxy_url,
            "heartbeatMode": "daemon",
            "allowedDestinationCount": len(config.allowed_destinations),
        },
    }
    _control_plane_request(
        config,
        "/api/sources/agents",
        payload=payload,
        extra_headers={"Idempotency-Key": f"source-agent-runtime:{config.agent_id}"},
    )


def _heartbeat_agent(config: SourceAgentProxyConfig) -> None:
    _control_plane_request(config, f"/api/sources/agents/{config.agent_id}/heartbeat", payload={})


def _control_plane_request(
    config: SourceAgentProxyConfig,
    path: str,
    *,
    payload: Mapping[str, object],
    extra_headers: Mapping[str, str] | None = None,
) -> None:
    headers = {
        "Content-Type": "application/json",
        "X-Tenant-ID": config.tenant_id,
        "X-User-ID": config.user_id,
        "X-Roles": config.roles,
        "X-Request-ID": f"source-agent:{config.agent_id}",
        **dict(extra_headers or {}),
    }
    request = Request(
        f"{config.control_plane_url.rstrip('/')}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    # SourceAgentProxyConfig rejects non-HTTP(S), credential-bearing, query, and fragment URLs.
    with urlopen(request, timeout=5) as response:  # nosec B310  # noqa: S310
        if response.status >= 300:
            raise OSError(f"control plane returned HTTP {response.status}")


def _read_connect_request(sock: socket.socket) -> tuple[str, dict[str, str], bytes]:
    payload = bytearray()
    while b"\r\n\r\n" not in payload:
        chunk = sock.recv(1024)
        if not chunk:
            raise OSError("client closed CONNECT request")
        payload.extend(chunk)
        if len(payload) > _MAX_HEADER_BYTES:
            raise OSError("CONNECT request exceeded header limit")
    # Split on the FIRST header terminator only. A client may pipeline tunnel bytes
    # in the same segment as the CONNECT header; those trailing bytes must be
    # forwarded to upstream, not discarded (which silently truncated the stream).
    header_bytes, _, buffered = bytes(payload).partition(b"\r\n\r\n")
    lines = header_bytes.split(b"\r\n")
    request_line = lines[0].decode("ascii", errors="strict")
    headers = _header_map(lines[1:])
    return request_line, headers, buffered


def _header_map(lines: Sequence[bytes]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in lines:
        if not line or b":" not in line:
            continue
        name, value = line.split(b":", 1)
        headers[name.decode("ascii").strip().lower()] = value.decode("ascii").strip()
    return headers


def _request_target(request_line: str) -> tuple[str, str]:
    parts = request_line.split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
        raise OSError("malformed CONNECT request line")
    return parts[0].upper(), parts[1]


def _authority(value: str) -> tuple[str, int]:
    split = urlsplit(f"//{value}")
    if not split.hostname or split.port is None:
        raise OSError("CONNECT authority must include host and port")
    return split.hostname, split.port


def _destination_allowed(allowed: Sequence[str], host: str, port: int) -> bool:
    return any(_destination_matches(item, host, port) for item in allowed)


def _destination_matches(entry: str, host: str, port: int) -> bool:
    split = urlsplit(entry if "://" in entry else f"//{entry}")
    expected_host = (split.hostname or "").rstrip(".").lower()
    try:
        expected_port = split.port
    except ValueError:
        return False
    return expected_host == host.rstrip(".").lower() and (expected_port is None or expected_port == port)


def _connect_upstream(client: socket.socket, host: str, port: int) -> socket.socket | None:
    try:
        return socket.create_connection((host, port), timeout=5)
    except OSError:
        _send_status(client, 502, "Upstream connection failed")
        return None


def _send_status(sock: socket.socket, status: int, reason: str) -> None:
    sock.sendall(f"HTTP/1.1 {status} {reason}\r\nConnection: close\r\n\r\n".encode("ascii"))


def _relay(client: socket.socket, upstream: socket.socket) -> None:
    sockets = [client, upstream]
    while sockets:
        readable, _, _ = select.select(sockets, [], [], 30)
        if not readable:
            return
        for source in readable:
            payload = source.recv(_BUFFER_BYTES)
            if not payload:
                return
            destination = upstream if source is client else client
            destination.sendall(payload)


def _server_config(server: socketserver.BaseServer) -> SourceAgentProxyConfig:
    if not isinstance(server, SourceAgentProxyServer):
        raise RuntimeError("Source Agent handler requires SourceAgentProxyServer")
    return server.config


def _event(event_type: str, config: SourceAgentProxyConfig, *, status: str, error: str | None = None) -> str:
    return json.dumps(
        {
            "eventType": event_type,
            "agentId": config.agent_id,
            "status": status,
            "proxyUrl": config.proxy_url,
            "errorType": error,
        },
        sort_keys=True,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a Foundry-lite Source Agent CONNECT tunnel")
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--display-name", default="Local Source Agent")
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=8765)
    parser.add_argument("--control-plane-url", default="http://127.0.0.1:8000")
    parser.add_argument("--allow", action="append", default=[], dest="allowed_destinations")
    parser.add_argument("--heartbeat-interval-seconds", type=float, default=10.0)
    parser.add_argument("--tenant-id", default="default")
    return parser


def _config(args: argparse.Namespace) -> SourceAgentProxyConfig:
    if not args.allowed_destinations:
        raise ValueError("at least one --allow host[:port] is required")
    if args.listen_port <= 0 or args.heartbeat_interval_seconds <= 0:
        raise ValueError("listen port and heartbeat interval must be positive")
    return SourceAgentProxyConfig(
        agent_id=args.agent_id,
        display_name=args.display_name,
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        control_plane_url=args.control_plane_url,
        allowed_destinations=tuple(args.allowed_destinations),
        heartbeat_interval_seconds=args.heartbeat_interval_seconds,
        tenant_id=args.tenant_id,
    )


def main(argv: list[str] | None = None) -> int:
    try:
        run_source_agent_proxy(_config(_parser().parse_args(argv)))
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "failed", "errorType": type(exc).__name__, "message": str(exc)}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
