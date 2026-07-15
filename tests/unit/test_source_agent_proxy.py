from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from foundry_lite.application.ports import (
    ConnectorNetworkRoute,
    ConnectorSnapshotRequest,
    RestPaginationConfig,
    RestSourceConfig,
)
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure.adapters import RestPullConnectorAdapter
from foundry_lite_worker import source_agent_proxy as agent_module
from foundry_lite_worker.source_agent_proxy import SourceAgentProxyConfig, SourceAgentProxyServer


class _JsonSourceHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract.
        payload = json.dumps({"items": [{"id": "order-1"}], "nextCursor": None}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _: str, *args: object) -> None:
        del args


def test_source_agent_proxy_carries_real_rest_snapshot_and_records_egress() -> None:
    with _json_source() as source_port, _agent_proxy(source_port) as proxy_port:
        snapshot = RestPullConnectorAdapter().snapshot(
            _snapshot_request(source_port, proxy_port, allowed=f"127.0.0.1:{source_port}")
        )

    assert snapshot.rows == ({"id": "order-1"},)
    assert snapshot.network_evidence["origin"] == "agent-proxy"
    assert snapshot.network_evidence["networkType"] == "agent_proxy"
    assert snapshot.network_evidence["destinationPort"] == source_port
    assert snapshot.network_evidence["egressSucceeded"] is True
    assert snapshot.network_evidence["responseFlags"] == "NONE"


def test_worker_policy_denies_destination_without_contacting_agent() -> None:
    with _json_source() as source_port, _agent_proxy(source_port) as proxy_port:
        with pytest.raises(ValidationFailed) as error:
            RestPullConnectorAdapter().snapshot(_snapshot_request(source_port, proxy_port, allowed="127.0.0.1:1"))

    evidence = error.value.details["networkEvidence"]
    assert isinstance(evidence, dict)
    assert evidence["responseFlags"] == "POLICY_DENIED"
    assert evidence["egressSucceeded"] is False


def test_agent_local_allowlist_can_deny_worker_approved_destination() -> None:
    with _json_source() as source_port, _agent_proxy(1) as proxy_port:
        with pytest.raises(ValidationFailed) as error:
            RestPullConnectorAdapter().snapshot(
                _snapshot_request(source_port, proxy_port, allowed=f"127.0.0.1:{source_port}")
            )

    evidence = error.value.details["networkEvidence"]
    assert isinstance(evidence, dict)
    assert evidence["responseFlags"] == "AGENT_POLICY_DENIED"
    assert evidence["egressSucceeded"] is False


def test_agent_tunnel_success_keeps_tls_failure_separate_from_egress() -> None:
    with _json_source() as source_port, _agent_proxy(source_port) as proxy_port:
        with pytest.raises(ValidationFailed) as error:
            RestPullConnectorAdapter().snapshot(
                _snapshot_request(
                    source_port,
                    proxy_port,
                    allowed=f"127.0.0.1:{source_port}",
                    scheme="https",
                )
            )

    evidence = error.value.details["networkEvidence"]
    assert isinstance(evidence, dict)
    assert error.value.details["networkStage"] == "tls"
    assert evidence["responseFlags"] == "TLS_ERROR"
    assert evidence["egressSucceeded"] is True


def test_source_agent_config_rejects_non_http_control_plane_url() -> None:
    with pytest.raises(ValueError, match="control plane URL"):
        SourceAgentProxyConfig(
            agent_id="orders_agent",
            display_name="Orders agent",
            listen_host="127.0.0.1",
            listen_port=0,
            control_plane_url="file:///tmp/control-plane",
            allowed_destinations=("127.0.0.1:443",),
        )


@pytest.mark.parametrize(
    "control_plane_url",
    [
        "http://user@127.0.0.1:8000",
        "http://127.0.0.1:8000/api?token=secret",
        "http://127.0.0.1:8000/#fragment",
    ],
)
def test_source_agent_config_rejects_credential_or_route_bearing_control_plane_url(control_plane_url: str) -> None:
    with pytest.raises(ValueError, match="control plane URL"):
        SourceAgentProxyConfig(
            agent_id="orders_agent",
            display_name="Orders agent",
            listen_host="127.0.0.1",
            listen_port=0,
            control_plane_url=control_plane_url,
            allowed_destinations=("127.0.0.1:443",),
        )


def test_source_agent_main_builds_runtime_config_and_reports_failures(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: list[SourceAgentProxyConfig] = []
    monkeypatch.setattr(agent_module, "run_source_agent_proxy", captured.append)

    assert (
        agent_module.main(
            [
                "--agent-id",
                "orders_agent",
                "--display-name",
                "Orders agent",
                "--listen-host",
                "0.0.0.0",
                "--listen-port",
                "8765",
                "--allow",
                "db.internal:5432",
                "--heartbeat-interval-seconds",
                "2",
                "--tenant-id",
                "tenant-live",
            ]
        )
        == 0
    )
    assert captured[0].proxy_url == "http://127.0.0.1:8765"
    assert captured[0].allowed_destinations == ("db.internal:5432",)
    assert captured[0].tenant_id == "tenant-live"

    assert agent_module.main(["--agent-id", "orders_agent"]) == 1
    assert json.loads(capsys.readouterr().out)["errorType"] == "ValueError"
    assert agent_module.main(["--agent-id", "orders_agent", "--allow", "db:5432", "--listen-port", "0"]) == 1
    capsys.readouterr()

    monkeypatch.setattr(agent_module, "run_source_agent_proxy", lambda _config: (_ for _ in ()).throw(OSError("busy")))
    assert agent_module.main(["--agent-id", "orders_agent", "--allow", "db:5432"]) == 1
    assert json.loads(capsys.readouterr().out)["message"] == "busy"


def test_source_agent_registers_and_heartbeats_with_control_plane_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[object] = []

    class _Response:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def fake_urlopen(request, *, timeout):
        assert timeout == 5
        requests.append(request)
        return _Response()

    monkeypatch.setattr(agent_module, "urlopen", fake_urlopen)
    config = _agent_config()

    agent_module._register_agent(config)
    agent_module._heartbeat_agent(config)

    register, heartbeat = requests
    assert register.full_url.endswith("/api/sources/agents")
    assert heartbeat.full_url.endswith("/api/sources/agents/orders_agent/heartbeat")
    assert register.headers["X-tenant-id"] == "tenant-live"
    assert register.headers["Idempotency-key"] == "source-agent-runtime:orders_agent"
    assert json.loads(register.data)["networkSummary"]["allowedDestinationCount"] == 1


def test_source_agent_heartbeat_loop_reports_retryable_control_plane_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(agent_module, "_register_agent", lambda _config: calls.append("register"))
    monkeypatch.setattr(agent_module, "_heartbeat_agent", lambda _config: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr(agent_module.time, "sleep", lambda _seconds: (_ for _ in ()).throw(RuntimeError("stop")))

    with pytest.raises(RuntimeError, match="stop"):
        agent_module._heartbeat_loop(_agent_config())

    assert calls == ["register"]
    event = json.loads(capsys.readouterr().out)
    assert event["eventType"] == "source_agent.control_plane_error"
    assert event["errorType"] == "OSError"


def test_source_agent_daemon_lifecycle_emits_start_and_stop(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lifecycle: list[str] = []

    class _Server:
        def __init__(self, _config) -> None:
            lifecycle.append("created")

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            lifecycle.append("closed")

        def serve_forever(self, *, poll_interval: float) -> None:
            assert poll_interval == 0.5
            raise KeyboardInterrupt

        def shutdown(self) -> None:
            lifecycle.append("shutdown")

    monkeypatch.setattr(agent_module, "SourceAgentProxyServer", _Server)
    monkeypatch.setattr(agent_module, "_heartbeat_loop", lambda _config: None)

    agent_module.run_source_agent_proxy(_agent_config())

    events = [json.loads(line)["eventType"] for line in capsys.readouterr().out.splitlines()]
    assert events == ["source_agent.started", "source_agent.stopped"]
    assert lifecycle == ["created", "shutdown", "closed"]


def test_source_agent_protocol_helpers_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Socket:
        def __init__(self, chunks: list[bytes]) -> None:
            self.chunks = iter(chunks)
            self.sent = b""

        def recv(self, _size: int) -> bytes:
            return next(self.chunks, b"")

        def sendall(self, payload: bytes) -> None:
            self.sent += payload

    request_socket = _Socket([b"CONNECT db.internal:5432 HTTP/1.1\r\nX-Test: yes\r\n\r\n"])
    request_line, headers = agent_module._read_connect_request(request_socket)
    assert request_line == "CONNECT db.internal:5432 HTTP/1.1"
    assert headers == {"x-test": "yes"}
    assert agent_module._request_target(request_line) == ("CONNECT", "db.internal:5432")
    assert agent_module._authority("db.internal:5432") == ("db.internal", 5432)
    assert agent_module._destination_matches("DB.INTERNAL", "db.internal.", 5432) is True
    assert agent_module._destination_matches("db.internal:bad", "db.internal", 5432) is False

    with pytest.raises(OSError, match="closed"):
        agent_module._read_connect_request(_Socket([]))
    with pytest.raises(OSError, match="malformed"):
        agent_module._request_target("CONNECT db.internal")
    with pytest.raises(OSError, match="host and port"):
        agent_module._authority("db.internal")
    with pytest.raises(RuntimeError, match="requires SourceAgentProxyServer"):
        agent_module._server_config(SimpleNamespace())

    denied_socket = _Socket([])
    monkeypatch.setattr(
        agent_module.socket,
        "create_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError()),
    )
    assert agent_module._connect_upstream(denied_socket, "db.internal", 5432) is None
    assert b"502 Upstream connection failed" in denied_socket.sent

    monkeypatch.setattr(agent_module.select, "select", lambda *_args, **_kwargs: ([], [], []))
    agent_module._relay(_Socket([]), _Socket([]))


def _agent_config() -> SourceAgentProxyConfig:
    return SourceAgentProxyConfig(
        agent_id="orders_agent",
        display_name="Orders agent",
        listen_host="localhost",
        listen_port=8765,
        control_plane_url="http://127.0.0.1:8000",
        allowed_destinations=("db.internal:5432",),
        heartbeat_interval_seconds=0.01,
        tenant_id="tenant-live",
    )


def _snapshot_request(
    source_port: int,
    proxy_port: int,
    *,
    allowed: str,
    scheme: str = "http",
) -> ConnectorSnapshotRequest:
    return ConnectorSnapshotRequest(
        connector_name="agent_orders",
        resource_name="orders",
        tenant_id="default",
        request_id="req-agent-proxy",
        rest=RestSourceConfig(
            base_url=f"{scheme}://127.0.0.1:{source_port}",
            resource_path="orders",
            pagination=RestPaginationConfig(),
            allow_private_network=True,
        ),
        network_route=ConnectorNetworkRoute(
            mode="agent_proxy",
            policy_name="orders_policy",
            agent_id="orders_agent",
            proxy_url=f"http://127.0.0.1:{proxy_port}",
            allowed_destinations=(allowed,),
        ),
    )


@contextmanager
def _json_source() -> Iterator[int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _JsonSourceHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@contextmanager
def _agent_proxy(allowed_port: int) -> Iterator[int]:
    config = SourceAgentProxyConfig(
        agent_id="orders_agent",
        display_name="Orders agent",
        listen_host="127.0.0.1",
        listen_port=0,
        control_plane_url="http://127.0.0.1:8000",
        allowed_destinations=(f"127.0.0.1:{allowed_port}",),
    )
    server = SourceAgentProxyServer(config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
