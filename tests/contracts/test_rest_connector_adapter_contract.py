from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from contextlib import AbstractContextManager
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar
from urllib.parse import parse_qs, urlsplit

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
                rest=RestSourceConfig(base_url=server.base_url, resource_path="/orders"),
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
                rest=RestSourceConfig(base_url=server.base_url, resource_path="/orders"),
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
                ),
            )
        )

    assert server.requests[0]["api_key"] == "key-1"


def test_rest_connector_raises_rate_limit_error() -> None:
    with MockRestServer() as server:
        adapter = RestPullConnectorAdapter()
        with pytest.raises(ConnectorRateLimitedError, match="retry_after=30"):
            adapter.snapshot(
                ConnectorSnapshotRequest(
                    connector_name="rest",
                    resource_name="limited",
                    tenant_id="tenant-demo",
                    request_id="req-rest-3",
                    rest=RestSourceConfig(base_url=server.base_url, resource_path="/rate-limit"),
                )
            )


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
                rest=RestSourceConfig(base_url=server.base_url, resource_path="/empty"),
            )
        )

    assert snapshot.rows == ()
    assert snapshot.schema == {"columns": []}


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
