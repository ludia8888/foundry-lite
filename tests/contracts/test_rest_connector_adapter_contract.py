from __future__ import annotations

import json
import socket
import threading
from collections.abc import Mapping
from contextlib import AbstractContextManager
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

import foundry_lite.infrastructure.adapters.rest_connector as rest_connector_module
import pytest
from foundry_lite.application.ports.connector_adapter import (
    ConnectorRateLimitedError,
    ConnectorSnapshotRequest,
    RestAuthConfig,
    RestPaginationConfig,
    RestSourceConfig,
)
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure.adapters import RestPullConnectorAdapter


class MockRestServer(AbstractContextManager["MockRestServer"]):
    def __init__(self) -> None:
        handler = _handler_class()
        self.requests = handler.requests
        self._server = HTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> MockRestServer:
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()


def test_rest_connector_fetches_page_and_returns_resume_cursor() -> None:
    with MockRestServer() as server:
        adapter = RestPullConnectorAdapter()
        snapshot = adapter.snapshot(
            ConnectorSnapshotRequest(
                connector_name="rest",
                resource_name="orders",
                tenant_id="tenant-demo",
                request_id="req-rest-1",
                rest=RestSourceConfig(
                    base_url=server.base_url,
                    resource_path="/orders",
                    auth=RestAuthConfig(mode="bearer", token="secret-token"),
                    schema_columns=("order_id", "amount"),
                    allow_private_network=True,
                ),
            )
        )

    assert snapshot.rows == ({"order_id": "O-1001", "amount": 100},)
    assert snapshot.schema == {"columns": ["order_id", "amount"]}
    assert snapshot.cursor == {"cursor": "page-2"}
    assert server.requests[0]["authorization"] == "Bearer secret-token"


def test_rest_connector_sends_cursor_when_resuming() -> None:
    with MockRestServer() as server:
        adapter = RestPullConnectorAdapter()
        snapshot = adapter.snapshot(
            ConnectorSnapshotRequest(
                connector_name="rest",
                resource_name="orders",
                tenant_id="tenant-demo",
                request_id="req-rest-2",
                cursor={"cursor": "page-2"},
                rest=RestSourceConfig(base_url=server.base_url, resource_path="/orders", allow_private_network=True),
            )
        )

    assert snapshot.rows == ({"order_id": "O-1002", "amount": 200},)
    assert snapshot.cursor is None
    assert server.requests[0]["cursor"] == "page-2"


def test_rest_connector_omits_cursor_when_resume_key_is_missing() -> None:
    with MockRestServer() as server:
        snapshot = RestPullConnectorAdapter().snapshot(
            ConnectorSnapshotRequest(
                connector_name="rest",
                resource_name="orders",
                tenant_id="tenant-demo",
                request_id="req-rest-missing-cursor",
                cursor={"offset": "page-2"},
                rest=RestSourceConfig(base_url=server.base_url, resource_path="/orders", allow_private_network=True),
            )
        )

    assert snapshot.rows == ({"order_id": "O-1001", "amount": 100},)
    assert server.requests[0]["cursor"] is None


def test_rest_connector_supports_custom_header_auth() -> None:
    with MockRestServer() as server:
        RestPullConnectorAdapter().snapshot(
            ConnectorSnapshotRequest(
                connector_name="rest",
                resource_name="orders",
                tenant_id="tenant-demo",
                request_id="req-rest-header-auth",
                rest=RestSourceConfig(
                    base_url=server.base_url,
                    resource_path="/orders",
                    auth=RestAuthConfig(mode="header", header_name="X-Api-Key", header_value="key-1"),
                    allow_private_network=True,
                ),
            )
        )

    assert server.requests[0]["api_key"] == "key-1"


def test_rest_connector_raises_rate_limit_error() -> None:
    with MockRestServer() as server:
        adapter = RestPullConnectorAdapter()
        with pytest.raises(ConnectorRateLimitedError, match="retry_after=30") as exc_info:
            adapter.snapshot(
                ConnectorSnapshotRequest(
                    connector_name="rest",
                    resource_name="limited",
                    tenant_id="tenant-demo",
                    request_id="req-rest-3",
                    rest=RestSourceConfig(
                        base_url=server.base_url,
                        resource_path="/rate-limit",
                        allow_private_network=True,
                    ),
                )
            )

    failure = exc_info.value.failure
    assert failure.kind == "rate_limited"
    assert failure.is_retryable is True
    assert failure.timeout_seconds == 30
    assert failure.adapter_profile == "rest-pull-connector"


def test_rest_connector_rejects_missing_or_incomplete_config() -> None:
    adapter = RestPullConnectorAdapter()
    with pytest.raises(ValidationFailed, match="source config"):
        adapter.snapshot(ConnectorSnapshotRequest("rest", "orders", "tenant-demo", "req-rest-no-config"))
    with pytest.raises(ValidationFailed, match="baseUrl"):
        adapter.snapshot(
            ConnectorSnapshotRequest(
                connector_name="rest",
                resource_name="orders",
                tenant_id="tenant-demo",
                request_id="req-rest-blank-config",
                rest=RestSourceConfig(base_url="", resource_path="/orders"),
            )
        )
    with pytest.raises(ValidationFailed, match="auth config"):
        adapter.snapshot(
            ConnectorSnapshotRequest(
                connector_name="rest",
                resource_name="orders",
                tenant_id="tenant-demo",
                request_id="req-rest-bad-auth",
                rest=RestSourceConfig(
                    base_url="http://127.0.0.1",
                    resource_path="/orders",
                    auth=RestAuthConfig(mode="bearer"),
                ),
            )
        )


def test_rest_connector_rejects_non_http_source_url() -> None:
    adapter = RestPullConnectorAdapter()
    with pytest.raises(ValidationFailed, match="http or https"):
        adapter.snapshot(
            ConnectorSnapshotRequest(
                connector_name="rest",
                resource_name="orders",
                tenant_id="tenant-demo",
                request_id="req-rest-4",
                rest=RestSourceConfig(base_url="file:///tmp", resource_path="/orders"),
            )
        )


@pytest.mark.parametrize(
    ("base_url", "reason"),
    [
        ("http://127.0.0.1", "loopback_ip"),
        ("http://[::1]", "loopback_ip"),
        ("http://localhost", "local_hostname"),
        ("http://10.0.0.5", "private_ip"),
        ("http://169.254.169.254", "link_local_ip"),
        ("http://metadata.google.internal", "metadata_hostname"),
        ("http://service.internal", "internal_hostname"),
        ("http://224.0.0.1", "multicast_ip"),
        ("http://0.0.0.0", "unspecified_ip"),
        ("http://240.0.0.1", "reserved_ip"),
        ("http://100.64.0.1", "non_global_ip"),
    ],
)
def test_rest_connector_rejects_private_network_source_url(base_url: str, reason: str) -> None:
    with pytest.raises(ValidationFailed, match="private or local network") as exc_info:
        RestPullConnectorAdapter().snapshot(
            ConnectorSnapshotRequest(
                connector_name="rest",
                resource_name="orders",
                tenant_id="tenant-demo",
                request_id="req-rest-private-url",
                rest=RestSourceConfig(base_url=base_url, resource_path="/orders"),
            )
        )

    assert exc_info.value.details["reason"] == reason


def test_rest_connector_rejects_invalid_source_url_port() -> None:
    with pytest.raises(ValidationFailed, match="invalid port") as exc_info:
        RestPullConnectorAdapter().snapshot(
            ConnectorSnapshotRequest(
                connector_name="rest",
                resource_name="orders",
                tenant_id="tenant-demo",
                request_id="req-rest-invalid-port",
                rest=RestSourceConfig(base_url="http://example.com:bad", resource_path="/orders"),
            )
        )

    assert exc_info.value.details["url"] == "http://example.com:bad/orders"


def test_rest_connector_rejects_source_url_resolving_to_private_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_getaddrinfo(
        host: str,
        port: int,
        *,
        type: socket.SocketKind,
    ) -> list[tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[str, int]]]:
        return [(socket.AF_INET, type, 0, "", ("10.1.2.3", port))]

    monkeypatch.setattr(rest_connector_module.socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(ValidationFailed, match="private or local network") as exc_info:
        RestPullConnectorAdapter().snapshot(
            ConnectorSnapshotRequest(
                connector_name="rest",
                resource_name="orders",
                tenant_id="tenant-demo",
                request_id="req-rest-resolved-private",
                rest=RestSourceConfig(base_url="https://api.example.test", resource_path="/orders"),
            )
        )

    assert exc_info.value.details["reason"] == "resolved_private_ip"


def test_rest_connector_rejects_unresolvable_source_host(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(
        host: str,
        port: int,
        *,
        type: socket.SocketKind,
    ) -> list[tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[str, int]]]:
        raise socket.gaierror("no answer")

    monkeypatch.setattr(rest_connector_module.socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(ValidationFailed, match="could not be resolved") as exc_info:
        RestPullConnectorAdapter().snapshot(
            ConnectorSnapshotRequest(
                connector_name="rest",
                resource_name="orders",
                tenant_id="tenant-demo",
                request_id="req-rest-unresolved",
                rest=RestSourceConfig(base_url="https://api.example.test", resource_path="/orders"),
            )
        )

    assert exc_info.value.details["host"] == "api.example.test"


def test_rest_connector_accepts_source_url_resolving_to_global_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[str, int]] = []

    def fake_getaddrinfo(
        host: str,
        port: int,
        *,
        type: socket.SocketKind,
    ) -> list[tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[str, int]]]:
        return [(socket.AF_INET, type, 0, "", ("93.184.216.34", port))]

    def fake_urlopen(request: Request, timeout: int) -> _FakeHTTPResponse:
        requests.append((str(request.full_url), timeout))
        return _FakeHTTPResponse({"items": [{"order_id": "O-1003"}], "nextCursor": ""})

    monkeypatch.setattr(rest_connector_module.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(rest_connector_module, "urlopen", fake_urlopen)

    snapshot = RestPullConnectorAdapter().snapshot(
        ConnectorSnapshotRequest(
            connector_name="rest",
            resource_name="orders",
            tenant_id="tenant-demo",
            request_id="req-rest-public-dns",
            rest=RestSourceConfig(base_url="https://api.example.test", resource_path="/orders"),
        )
    )

    assert snapshot.rows == ({"order_id": "O-1003"},)
    assert snapshot.cursor is None
    assert requests == [("https://api.example.test/orders", 5)]


@pytest.mark.parametrize(
    ("resource_path", "message", "pagination"),
    [
        ("/server-error", "request failed", RestPaginationConfig()),
        ("/invalid-json", "invalid JSON", RestPaginationConfig()),
        ("/array-payload", "JSON object", RestPaginationConfig()),
        ("/items-object", "items path", RestPaginationConfig()),
        ("/item-scalar", "item must be an object", RestPaginationConfig()),
        ("/nested-non-object", "items path", RestPaginationConfig(items_path="data.items")),
    ],
)
def test_rest_connector_rejects_invalid_http_responses(
    resource_path: str,
    message: str,
    pagination: RestPaginationConfig,
) -> None:
    with MockRestServer() as server:
        with pytest.raises(ValidationFailed, match=message):
            RestPullConnectorAdapter().snapshot(
                ConnectorSnapshotRequest(
                    connector_name="rest",
                    resource_name="orders",
                    tenant_id="tenant-demo",
                    request_id="req-rest-invalid-response",
                    rest=RestSourceConfig(
                        base_url=server.base_url,
                        resource_path=resource_path,
                        pagination=pagination,
                        allow_private_network=True,
                    ),
                )
            )


def test_rest_connector_returns_empty_schema_for_empty_page_without_declared_columns() -> None:
    with MockRestServer() as server:
        snapshot = RestPullConnectorAdapter().snapshot(
            ConnectorSnapshotRequest(
                connector_name="rest",
                resource_name="orders",
                tenant_id="tenant-demo",
                request_id="req-rest-empty-page",
                rest=RestSourceConfig(base_url=server.base_url, resource_path="/empty", allow_private_network=True),
            )
        )

    assert snapshot.rows == ()
    assert snapshot.schema == {"columns": []}


class _FakeHTTPResponse(AbstractContextManager["_FakeHTTPResponse"]):
    def __init__(self, payload: Mapping[str, object]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _handler_class() -> type[BaseHTTPRequestHandler]:
    requests: list[dict[str, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        requests: ClassVar[list[dict[str, str | None]]]

        def do_GET(self) -> None:
            split = urlsplit(self.path)
            params = parse_qs(split.query)
            cursor = params.get("cursor", [None])[0]
            self.requests.append(
                {
                    "authorization": self.headers.get("Authorization"),
                    "api_key": self.headers.get("X-Api-Key"),
                    "cursor": cursor,
                    "path": split.path,
                }
            )
            if split.path == "/rate-limit":
                self.send_response(429)
                self.send_header("Retry-After", "30")
                self.end_headers()
                return
            if split.path == "/server-error":
                self.send_response(500)
                self.end_headers()
                return
            if split.path == "/invalid-json":
                self._write_raw(b"not-json")
                return
            if split.path == "/array-payload":
                self._write_json([])
                return
            self._write_json(_page_payload(split.path, cursor))

        def log_message(self, format: str, *args: object) -> None:
            return

        def _write_json(self, payload: object) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _write_raw(self, body: bytes) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    Handler.requests = requests
    return Handler


def _page_payload(path: str, cursor: str | None) -> Mapping[str, object]:
    if path == "/items-object":
        return {"items": {"order_id": "O-1001"}, "nextCursor": None}
    if path == "/item-scalar":
        return {"items": [1], "nextCursor": None}
    if path == "/nested-non-object":
        return {"data": [], "nextCursor": None}
    if path == "/empty":
        return {"items": [], "nextCursor": None}
    if cursor == "page-2":
        return {"items": [{"order_id": "O-1002", "amount": 200}], "nextCursor": None}
    return {"items": [{"order_id": "O-1001", "amount": 100}], "nextCursor": "page-2"}
