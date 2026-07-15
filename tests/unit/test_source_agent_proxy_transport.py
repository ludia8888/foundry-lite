from __future__ import annotations

from collections.abc import Iterator

import pytest
from foundry_lite.application.ports import ConnectorNetworkRoute
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure.adapters import source_agent_proxy_transport as transport


class _Socket:
    def __init__(self, chunks: list[bytes] | None = None) -> None:
        self.chunks: Iterator[bytes] = iter(chunks or [])
        self.sent = b""
        self.is_closed = False

    def sendall(self, payload: bytes) -> None:
        self.sent += payload

    def recv(self, _size: int) -> bytes:
        return next(self.chunks, b"")

    def close(self) -> None:
        self.is_closed = True


def test_source_target_direct_routes_apply_optional_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    connected = _Socket()
    calls: list[tuple[object, ...]] = []

    def create_connection(address, timeout, source_address):
        calls.append((address, timeout, source_address))
        return connected

    monkeypatch.setattr(transport.socket, "create_connection", create_connection)
    assert (
        transport.connect_source_target(
            target_host="db.internal",
            resolved_host="10.0.0.5",
            target_port=5432,
            timeout=3,
            source_address=None,
            route=None,
        )
        is connected
    )
    direct = ConnectorNetworkRoute(mode="direct", allowed_destinations=("db.internal:5432",))
    assert (
        transport.connect_source_target(
            target_host="DB.INTERNAL.",
            resolved_host="10.0.0.5",
            target_port=5432,
            timeout=4,
            source_address=("127.0.0.1", 0),
            route=direct,
        )
        is connected
    )
    assert calls[-1] == (("10.0.0.5", 5432), 4, ("127.0.0.1", 0))

    with pytest.raises(ValidationFailed) as denied:
        transport.connect_source_target(
            target_host="other.internal",
            resolved_host="10.0.0.6",
            target_port=5432,
            timeout=3,
            source_address=None,
            route=direct,
        )
    assert denied.value.details["networkEvidence"]["responseFlags"] == "POLICY_DENIED"


def test_source_agent_route_validation_and_proxy_endpoint_fail_closed() -> None:
    incomplete = ConnectorNetworkRoute(mode="agent_proxy")
    with pytest.raises(ValidationFailed) as invalid:
        transport._require_agent_route(incomplete, "db.internal", 5432)
    assert invalid.value.details["networkEvidence"]["responseFlags"] == "AGENT_ROUTE_INVALID"

    denied = _agent_route(allowed=("other.internal:5432",))
    with pytest.raises(ValidationFailed) as policy_denied:
        transport._require_agent_route(denied, "db.internal", 5432)
    assert policy_denied.value.details["networkEvidence"]["responseFlags"] == "POLICY_DENIED"

    for proxy_url in (
        "https://agent.internal:8765",
        "http://user@agent.internal:8765",
        "http://agent.internal:8765/path",
        "http://agent.internal:8765?query=yes",
        "http://agent.internal:bad",
    ):
        with pytest.raises(ValidationFailed) as endpoint_error:
            transport._proxy_endpoint(_agent_route(proxy_url=proxy_url), "db.internal", 5432)
        assert endpoint_error.value.details["networkEvidence"]["responseFlags"] == "AGENT_ROUTE_INVALID"

    assert transport._proxy_endpoint(_agent_route(proxy_url="http://agent.internal"), "db.internal", 5432) == (
        "agent.internal",
        80,
    )


@pytest.mark.parametrize(
    ("status", "flag"),
    [(403, "AGENT_POLICY_DENIED"), (503, "AGENT_DRAINING"), (502, "UPSTREAM_CONNECT_FAILURE")],
)
def test_source_agent_proxy_http_failures_close_tunnel(monkeypatch: pytest.MonkeyPatch, status: int, flag: str) -> None:
    sock = _Socket([f"HTTP/1.1 {status} failure\r\n\r\n".encode()])
    monkeypatch.setattr(transport.socket, "create_connection", lambda *_args: sock)

    with pytest.raises(ValidationFailed) as error:
        transport._connect_agent_proxy(_agent_route(), "db.internal", 5432, 3, None)

    assert sock.is_closed is True
    assert error.value.details["networkEvidence"]["responseFlags"] == flag
    assert b"X-Foundry-Lite-Agent-Id: agent-1" in sock.sent


def test_source_agent_proxy_success_and_unreachable_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    connected = _Socket([b"HTTP/1.1 200 Connection established\r\n\r\n"])
    monkeypatch.setattr(transport.socket, "create_connection", lambda *_args: connected)
    assert transport._connect_agent_proxy(_agent_route(), "db.internal", 5432, 3, None) is connected
    assert connected.is_closed is False

    monkeypatch.setattr(
        transport.socket,
        "create_connection",
        lambda *_args: (_ for _ in ()).throw(TimeoutError("offline")),
    )
    with pytest.raises(ValidationFailed) as unreachable:
        transport._connect_agent_proxy(_agent_route(), "db.internal", 5432, 3, None)
    assert unreachable.value.details["networkEvidence"]["responseFlags"] == "AGENT_UNREACHABLE"


def test_source_agent_proxy_response_parser_rejects_closed_oversized_and_malformed_responses() -> None:
    with pytest.raises(OSError, match="closed"):
        transport._proxy_response_status(_Socket())
    with pytest.raises(OSError, match="exceeded limit"):
        transport._proxy_response_status(_Socket([b"x" * (transport._MAX_PROXY_RESPONSE_BYTES + 1)]))
    with pytest.raises(OSError, match="malformed"):
        transport._proxy_response_status(_Socket([b"NOT-HTTP\r\n\r\n"]))
    assert transport._destination_matches("", "db.internal", 5432) is False
    assert transport._destination_matches("db.internal:bad", "db.internal", 5432) is False


def _agent_route(
    *,
    proxy_url: str = "http://agent.internal:8765",
    allowed: tuple[str, ...] = ("db.internal:5432",),
) -> ConnectorNetworkRoute:
    return ConnectorNetworkRoute(
        mode="agent_proxy",
        policy_name="db-policy",
        agent_id="agent-1",
        proxy_url=proxy_url,
        allowed_destinations=allowed,
    )
