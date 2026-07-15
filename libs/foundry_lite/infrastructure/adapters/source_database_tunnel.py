"""Loopback bridge from SQL database drivers into Source Agent CONNECT routes."""

from __future__ import annotations

import select
import socket
import socketserver
import threading
import time
from dataclasses import dataclass

from foundry_lite.application.ports.connector_adapter import ConnectorNetworkRoute
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure.adapters.source_agent_proxy_transport import connect_source_target

_BUFFER_BYTES = 64 * 1024


@dataclass
class _TunnelStats:
    bytes_sent: int = 0
    bytes_received: int = 0
    connection_count: int = 0
    error: ValidationFailed | None = None


class SourceDatabaseTunnel:
    """Ephemeral loopback listener that preserves the original DB protocol and TLS."""

    def __init__(self, route: ConnectorNetworkRoute, target_host: str, target_port: int) -> None:
        self.route = route
        self.target_host = target_host
        self.target_port = target_port
        self.started_at = time.monotonic()
        self._server = _DatabaseTunnelServer(route, target_host, target_port)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def hostaddr(self) -> str:
        return "127.0.0.1"

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def __enter__(self) -> SourceDatabaseTunnel:
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    def route_error(self) -> ValidationFailed | None:
        return self._server.stats_snapshot().error

    def evidence(self, connection_id: str, *, response_flags: str) -> dict[str, object]:
        stats = self._server.stats_snapshot()
        is_agent = self.route.mode == "agent_proxy"
        return {
            "connectionId": f"{connection_id}:database",
            "egressSucceeded": stats.connection_count > 0,
            "responseFlags": response_flags,
            "bytesSent": stats.bytes_sent,
            "bytesReceived": stats.bytes_received,
            "durationMs": max(0, round((time.monotonic() - self.started_at) * 1000)),
            "destinationPort": self.target_port,
            "origin": "agent-proxy" if is_agent else "connectivity-sidecar",
            "networkType": self.route.mode,
            "networkResources": {
                "networkPolicy": self.route.policy_name,
                "agentId": self.route.agent_id,
            },
        }


class _DatabaseTunnelServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, route: ConnectorNetworkRoute, target_host: str, target_port: int) -> None:
        self.route = route
        self.target_host = target_host
        self.target_port = target_port
        self._stats = _TunnelStats()
        self._lock = threading.Lock()
        super().__init__(("127.0.0.1", 0), _DatabaseTunnelHandler)

    def record_connection(self) -> None:
        with self._lock:
            self._stats.connection_count += 1

    def record_transfer(self, *, sent: int = 0, received: int = 0) -> None:
        with self._lock:
            self._stats.bytes_sent += sent
            self._stats.bytes_received += received

    def record_error(self, error: ValidationFailed) -> None:
        with self._lock:
            self._stats.error = self._stats.error or error

    def stats_snapshot(self) -> _TunnelStats:
        with self._lock:
            return _TunnelStats(**self._stats.__dict__)


class _DatabaseTunnelHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server = _tunnel_server(self.server)
        try:
            upstream = connect_source_target(
                target_host=server.target_host,
                resolved_host=server.target_host,
                target_port=server.target_port,
                timeout=5,
                source_address=None,
                route=server.route,
            )
        except ValidationFailed as exc:
            server.record_error(exc)
            return
        server.record_connection()
        try:
            _relay_database(self.request, upstream, server)
        finally:
            upstream.close()


def _relay_database(client: socket.socket, upstream: socket.socket, server: _DatabaseTunnelServer) -> None:
    sockets = [client, upstream]
    while True:
        readable, _, _ = select.select(sockets, [], [], 30)
        if not readable:
            return
        for source in readable:
            payload = source.recv(_BUFFER_BYTES)
            if not payload:
                return
            destination = upstream if source is client else client
            destination.sendall(payload)
            server.record_transfer(
                sent=len(payload) if source is client else 0, received=len(payload) if source is upstream else 0
            )


def _tunnel_server(server: socketserver.BaseServer) -> _DatabaseTunnelServer:
    if not isinstance(server, _DatabaseTunnelServer):
        raise RuntimeError("database tunnel handler requires _DatabaseTunnelServer")
    return server
