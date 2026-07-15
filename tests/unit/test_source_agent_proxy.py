from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from foundry_lite.application.ports import (
    ConnectorNetworkRoute,
    ConnectorSnapshotRequest,
    RestPaginationConfig,
    RestSourceConfig,
)
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure.adapters import RestPullConnectorAdapter
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
