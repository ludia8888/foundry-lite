from __future__ import annotations

import base64
import json
import socket
import threading
from collections.abc import Mapping
from contextlib import AbstractContextManager
from http.client import HTTPMessage
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar, Protocol, cast
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

import foundry_lite.infrastructure.adapters.rest_connector as rest_connector_module
import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.connector_adapter import (
    ConnectorRateLimitedError,
    ConnectorSnapshotRequest,
    RestAuthConfig,
    RestPaginationConfig,
    RestSourceConfig,
)
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure.adapters import RestPullConnectorAdapter
from foundry_lite.infrastructure.secrets import EnvSecretProvider


class _RedirectHandler(Protocol):
    def redirect_request(
        self,
        req: Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> Request | None: ...


class _RequestRecordingHandler(Protocol):
    requests: ClassVar[list[dict[str, str | None]]]


def _redirect_handler_from_handlers(handlers: tuple[object, ...]) -> _RedirectHandler:
    for handler in handlers:
        if isinstance(handler, rest_connector_module._ValidatingRedirectHandler):
            return cast(_RedirectHandler, handler)
    raise AssertionError("redirect validation handler was not installed")


class MockRestServer(AbstractContextManager["MockRestServer"]):
    def __init__(self) -> None:
        handler = _handler_class()
        self.requests = cast(type[_RequestRecordingHandler], handler).requests
        self._server = HTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = cast(tuple[str, int], self._server.server_address)
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


def test_rest_connector_supports_redacted_basic_credentials_secret() -> None:
    environ = {"FOUNDRY_LITE_SECRET_SAP_BASIC": "sap-user:sap-password"}
    with MockRestServer() as server:
        RestPullConnectorAdapter(secret_provider=EnvSecretProvider(environ=environ)).snapshot(
            ConnectorSnapshotRequest(
                connector_name="sap",
                resource_name="sales_orders",
                tenant_id="tenant-demo",
                request_id="req-sap-basic",
                rest=RestSourceConfig(
                    base_url=server.base_url,
                    resource_path="/orders",
                    auth=RestAuthConfig(mode="basic", basic_credentials_secret_ref="sap-basic"),
                    allow_private_network=True,
                ),
            )
        )

    encoded = base64.b64encode(b"sap-user:sap-password").decode("ascii")
    assert server.requests[0]["authorization"] == f"Basic {encoded}"


def test_rest_connector_rejects_invalid_basic_credentials_without_leaking_value() -> None:
    with MockRestServer() as server:
        with pytest.raises(ValidationFailed, match="username:password") as exc_info:
            RestPullConnectorAdapter().snapshot(
                ConnectorSnapshotRequest(
                    connector_name="sap",
                    resource_name="sales_orders",
                    tenant_id="tenant-demo",
                    request_id="req-sap-basic-invalid",
                    rest=RestSourceConfig(
                        base_url=server.base_url,
                        resource_path="/orders",
                        auth=RestAuthConfig(mode="basic", basic_credentials="sap-password"),
                        allow_private_network=True,
                    ),
                )
            )

    assert exc_info.value.details["secret_value"] == "***REDACTED***"


def test_rest_connector_follows_same_origin_odata_next_link() -> None:
    pagination = RestPaginationConfig(
        strategy="next_link",
        items_path="value",
        next_cursor_path="@odata.nextLink",
        cursor_key="nextLink",
    )
    with MockRestServer() as server:
        adapter = RestPullConnectorAdapter()
        config = RestSourceConfig(
            base_url=f"{server.base_url}/odata",
            resource_path="/SalesOrderSet?$format=json",
            pagination=pagination,
            allow_private_network=True,
        )
        first = adapter.snapshot(
            ConnectorSnapshotRequest("sap", "sales_orders", "tenant-demo", "req-sap-page-1", rest=config)
        )
        second = adapter.snapshot(
            ConnectorSnapshotRequest(
                "sap",
                "sales_orders",
                "tenant-demo",
                "req-sap-page-2",
                cursor=first.cursor,
                rest=config,
            )
        )

    assert first.rows == ({"SalesOrder": "500001", "Status": "Open"},)
    assert first.cursor is not None
    assert second.rows == ({"SalesOrder": "500002", "Status": "Released"},)
    assert second.cursor is None
    assert server.requests[1]["skiptoken"] == "page-2"


def test_rest_connector_collects_bounded_odata_pages_in_one_snapshot() -> None:
    with MockRestServer() as server:
        snapshot = RestPullConnectorAdapter().snapshot(
            ConnectorSnapshotRequest(
                "sap",
                "sales_orders",
                "tenant-demo",
                "req-sap-full-snapshot",
                rest=RestSourceConfig(
                    base_url=f"{server.base_url}/odata",
                    resource_path="/SalesOrderSet?$format=json",
                    pagination=RestPaginationConfig(
                        strategy="next_link",
                        items_path="value",
                        next_cursor_path="@odata.nextLink",
                        cursor_key="nextLink",
                        max_pages_per_snapshot=100,
                    ),
                    allow_private_network=True,
                ),
            )
        )

    assert snapshot.rows == (
        {"SalesOrder": "500001", "Status": "Open"},
        {"SalesOrder": "500002", "Status": "Released"},
    )
    assert snapshot.cursor is None
    assert snapshot.network_evidence["pageCount"] == 2
    assert snapshot.network_evidence["connectionCount"] == 2
    assert len(cast(list[object], snapshot.network_evidence["pageConnections"])) == 2


def test_rest_connector_rejects_cross_origin_next_link() -> None:
    with pytest.raises(ValidationFailed, match="configured origin") as exc_info:
        RestPullConnectorAdapter().snapshot(
            ConnectorSnapshotRequest(
                "sap",
                "sales_orders",
                "tenant-demo",
                "req-sap-cross-origin",
                cursor={"nextLink": "https://attacker.example/collect"},
                rest=RestSourceConfig(
                    base_url="https://sap.example.com/odata",
                    resource_path="/SalesOrderSet",
                    pagination=RestPaginationConfig(strategy="next_link", cursor_key="nextLink"),
                ),
            )
        )

    assert exc_info.value.details == {"nextLinkOrigin": "https://attacker.example:443"}


def test_connector_refreshes_rotated_secret() -> None:
    environ = {"FOUNDRY_LITE_SECRET_CONNECTOR_TOKEN": "token-v1"}
    adapter = RestPullConnectorAdapter(secret_provider=EnvSecretProvider(environ=environ))
    source = RestSourceConfig(
        base_url="",
        resource_path="/orders",
        auth=RestAuthConfig(mode="bearer", token_secret_ref="connector-token"),
        allow_private_network=True,
    )

    with MockRestServer() as server:
        source = RestSourceConfig(
            base_url=server.base_url,
            resource_path=source.resource_path,
            auth=source.auth,
            allow_private_network=source.allow_private_network,
        )
        adapter.snapshot(ConnectorSnapshotRequest("rest", "orders", "tenant-demo", "req-rest-secret-v1", rest=source))
        environ["FOUNDRY_LITE_SECRET_CONNECTOR_TOKEN"] = "token-v2"
        adapter.snapshot(ConnectorSnapshotRequest("rest", "orders", "tenant-demo", "req-rest-secret-v2", rest=source))

    assert [request["authorization"] for request in server.requests] == [
        "Bearer token-v1",
        "Bearer token-v2",
    ]


def test_rest_connector_secret_ref_requires_provider_without_leaking_value() -> None:
    with MockRestServer() as server:
        with pytest.raises(ValidationFailed, match="requires SecretProvider") as exc_info:
            RestPullConnectorAdapter().snapshot(
                ConnectorSnapshotRequest(
                    connector_name="rest",
                    resource_name="orders",
                    tenant_id="tenant-demo",
                    request_id="req-rest-secret-provider-missing",
                    rest=RestSourceConfig(
                        base_url=server.base_url,
                        resource_path="/orders",
                        auth=RestAuthConfig(mode="bearer", token_secret_ref="connector-token"),
                        allow_private_network=True,
                    ),
                )
            )

    assert exc_info.value.details == {
        "secret_ref": "connector-token",
        "secret_value": "***REDACTED***",
    }


def test_rest_connector_rejects_mixed_direct_secret_and_secret_ref_without_leaking_value() -> None:
    with MockRestServer() as server:
        with pytest.raises(ValidationFailed, match="cannot mix") as exc_info:
            RestPullConnectorAdapter(secret_provider=EnvSecretProvider(environ={})).snapshot(
                ConnectorSnapshotRequest(
                    connector_name="rest",
                    resource_name="orders",
                    tenant_id="tenant-demo",
                    request_id="req-rest-secret-mixed",
                    rest=RestSourceConfig(
                        base_url=server.base_url,
                        resource_path="/orders",
                        auth=RestAuthConfig(
                            mode="bearer",
                            token="direct-token",
                            token_secret_ref="connector-token",
                        ),
                        allow_private_network=True,
                    ),
                )
            )

    assert exc_info.value.details == {
        "secret_ref": "connector-token",
        "secret_value": "***REDACTED***",
    }


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


def test_rest_connector_applies_configured_local_rate_limit() -> None:
    current_time = [100.0]
    with MockRestServer() as server:
        adapter = RestPullConnectorAdapter(clock=lambda: current_time[0])
        source = RestSourceConfig(
            base_url=server.base_url,
            resource_path="/orders",
            allow_private_network=True,
            rate_limit_per_minute=60,
        )
        first = adapter.snapshot(
            ConnectorSnapshotRequest("rest", "orders", "tenant-demo", "req-rest-local-rate-1", rest=source)
        )
        with pytest.raises(AdapterError) as exc_info:
            adapter.snapshot(
                ConnectorSnapshotRequest("rest", "orders", "tenant-demo", "req-rest-local-rate-2", rest=source)
            )
        other_tenant = adapter.snapshot(
            ConnectorSnapshotRequest("rest", "orders", "tenant-other", "req-rest-local-rate-other", rest=source)
        )
        current_time[0] = 101.0
        retry = adapter.snapshot(
            ConnectorSnapshotRequest("rest", "orders", "tenant-demo", "req-rest-local-rate-3", rest=source)
        )

    failure = exc_info.value.failure
    assert first.rows == ({"order_id": "O-1001", "amount": 100},)
    assert other_tenant.rows == ({"order_id": "O-1001", "amount": 100},)
    assert retry.rows == ({"order_id": "O-1001", "amount": 100},)
    assert failure.kind == "rate_limited"
    assert failure.is_retryable is True
    assert failure.timeout_seconds == 1
    assert failure.details["tenantId"] == "tenant-demo"


def test_rest_connector_rejects_invalid_local_rate_limit() -> None:
    with pytest.raises(ValidationFailed, match="rateLimitPerMinute"):
        RestPullConnectorAdapter().snapshot(
            ConnectorSnapshotRequest(
                connector_name="rest",
                resource_name="orders",
                tenant_id="tenant-demo",
                request_id="req-rest-invalid-local-rate",
                rest=RestSourceConfig(
                    base_url="https://api.example.test",
                    resource_path="/orders",
                    rate_limit_per_minute=0,
                ),
            )
        )


def test_rest_connector_caps_response_size_before_json_parsing() -> None:
    with MockRestServer() as server:
        with pytest.raises(AdapterError) as exc_info:
            RestPullConnectorAdapter().snapshot(
                ConnectorSnapshotRequest(
                    connector_name="rest",
                    resource_name="orders",
                    tenant_id="tenant-demo",
                    request_id="req-rest-large-response",
                    rest=RestSourceConfig(
                        base_url=server.base_url,
                        resource_path="/large-response",
                        allow_private_network=True,
                        max_response_bytes=64,
                    ),
                )
            )

    failure = exc_info.value.failure
    assert failure.adapter_profile == "rest-pull-connector"
    assert failure.operation == "snapshot"
    assert failure.kind == "validation"
    assert failure.is_retryable is False
    assert failure.details == {"maxResponseBytes": 64, "bytesRead": 65}


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
    with pytest.raises(ValidationFailed, match="maxResponseBytes") as exc_info:
        adapter.snapshot(
            ConnectorSnapshotRequest(
                connector_name="rest",
                resource_name="orders",
                tenant_id="tenant-demo",
                request_id="req-rest-invalid-response-size",
                rest=RestSourceConfig(
                    base_url="http://127.0.0.1",
                    resource_path="/orders",
                    max_response_bytes=0,
                    allow_private_network=True,
                ),
            )
        )

    assert exc_info.value.details == {"maxResponseBytes": 0}


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


def test_rest_mutable_pagination_detected_or_marked_non_replayable() -> None:
    with pytest.raises(ValidationFailed, match="non-replayable") as exc_info:
        RestPullConnectorAdapter().snapshot(
            ConnectorSnapshotRequest(
                connector_name="rest",
                resource_name="orders",
                tenant_id="tenant-demo",
                request_id="req-rest-page-number",
                rest=RestSourceConfig(
                    base_url="https://api.example.test",
                    resource_path="/orders",
                    pagination=RestPaginationConfig(strategy="page_number"),
                ),
            )
        )

    assert exc_info.value.details == {"pagination_strategy": "page_number", "non_replayable": True}


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


def test_rest_redirect_to_private_ip_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(
        host: str,
        port: int,
        *,
        type: socket.SocketKind,
    ) -> list[tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[str, int]]]:
        return [(socket.AF_INET, type, 0, "", ("93.184.216.34", port))]

    class RedirectingOpener:
        def __init__(self, handler: _RedirectHandler) -> None:
            self._handler = handler

        def open(self, request: Request, timeout: int) -> _FakeHTTPResponse:
            self._handler.redirect_request(
                request,
                object(),
                302,
                "Found",
                {},
                "http://169.254.169.254/latest/meta-data",
            )
            return _FakeHTTPResponse({"items": [], "nextCursor": None})

    def fake_build_opener(*handlers: object) -> RedirectingOpener:
        return RedirectingOpener(_redirect_handler_from_handlers(handlers))

    monkeypatch.setattr(rest_connector_module.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(rest_connector_module, "build_opener", fake_build_opener)

    with pytest.raises(ValidationFailed, match="private or local network") as exc_info:
        RestPullConnectorAdapter().snapshot(
            ConnectorSnapshotRequest(
                connector_name="rest",
                resource_name="orders",
                tenant_id="tenant-demo",
                request_id="req-rest-private-redirect",
                rest=RestSourceConfig(base_url="https://api.example.test", resource_path="/orders"),
            )
        )

    assert exc_info.value.details["host"] == "169.254.169.254"
    assert exc_info.value.details["reason"] == "link_local_ip"


@pytest.mark.parametrize(
    ("redirect_url", "host", "reason"),
    [
        ("http://2130706433/latest/meta-data", "2130706433", "loopback_ip"),
        ("http://0177.0000.0000.0001/latest/meta-data", "0177.0000.0000.0001", "loopback_ip"),
        ("http://%31%36%39.254.169.254/latest/meta-data", "%31%36%39.254.169.254", "link_local_ip"),
        ("http://%6c%6f%63%61%6c%68%6f%73%74/orders", "%6c%6f%63%61%6c%68%6f%73%74", "local_hostname"),
    ],
)
def test_rest_redirect_encoded_decimal_octal_private_hosts_blocked(
    monkeypatch: pytest.MonkeyPatch,
    redirect_url: str,
    host: str,
    reason: str,
) -> None:
    def fake_getaddrinfo(
        host: str,
        port: int,
        *,
        type: socket.SocketKind,
    ) -> list[tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[str, int]]]:
        return [(socket.AF_INET, type, 0, "", ("93.184.216.34", port))]

    class RedirectingOpener:
        def __init__(self, handler: _RedirectHandler) -> None:
            self._handler = handler

        def open(self, request: Request, timeout: int) -> _FakeHTTPResponse:
            self._handler.redirect_request(request, object(), 302, "Found", {}, redirect_url)
            return _FakeHTTPResponse({"items": [], "nextCursor": None})

    def fake_build_opener(*handlers: object) -> RedirectingOpener:
        return RedirectingOpener(_redirect_handler_from_handlers(handlers))

    monkeypatch.setattr(rest_connector_module.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(rest_connector_module, "build_opener", fake_build_opener)

    with pytest.raises(ValidationFailed, match="private or local network") as exc_info:
        RestPullConnectorAdapter().snapshot(
            ConnectorSnapshotRequest(
                connector_name="rest",
                resource_name="orders",
                tenant_id="tenant-demo",
                request_id="req-rest-encoded-private-redirect",
                rest=RestSourceConfig(base_url="https://api.example.test", resource_path="/orders"),
            )
        )

    assert exc_info.value.details["host"] == host
    assert exc_info.value.details["reason"] == reason


def test_rest_url_validation_rejects_missing_host() -> None:
    with pytest.raises(ValidationFailed, match="must include a host"):
        rest_connector_module._validated_http_url("http://:80/orders")


def test_rest_legacy_ipv4_parser_rejects_invalid_private_host_spellings() -> None:
    assert rest_connector_module._legacy_ipv4_literal("127.0.0.999") is None
    assert rest_connector_module._legacy_ipv4_literal("127:0:0:1") is None
    assert rest_connector_module._ipv4_from_int("999999999999999999999", 10) is None
    assert rest_connector_module._legacy_ipv4_part("0x7f") == 127
    assert rest_connector_module._legacy_ipv4_part("0177") == 127
    assert rest_connector_module._legacy_ipv4_part("127") == 127
    assert rest_connector_module._legacy_ipv4_part("not-a-number") is None
    assert rest_connector_module._int_part("0xzz", 16) is None


def test_rest_dns_rebinding_to_private_ip_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    resolutions: list[str] = []

    def fake_getaddrinfo(
        host: str,
        port: int,
        *,
        type: socket.SocketKind,
    ) -> list[tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[str, int]]]:
        address = "93.184.216.34" if not resolutions else "10.0.0.8"
        resolutions.append(address)
        return [(socket.AF_INET, type, 0, "", (address, port))]

    class RebindingRedirectOpener:
        def __init__(self, handler: _RedirectHandler) -> None:
            self._handler = handler

        def open(self, request: Request, timeout: int) -> _FakeHTTPResponse:
            self._handler.redirect_request(
                request,
                object(),
                302,
                "Found",
                {},
                "https://api.example.test/orders?cursor=next",
            )
            return _FakeHTTPResponse({"items": [], "nextCursor": None})

    def fake_build_opener(*handlers: object) -> RebindingRedirectOpener:
        return RebindingRedirectOpener(_redirect_handler_from_handlers(handlers))

    monkeypatch.setattr(rest_connector_module.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(rest_connector_module, "build_opener", fake_build_opener)

    with pytest.raises(ValidationFailed, match="private or local network") as exc_info:
        RestPullConnectorAdapter().snapshot(
            ConnectorSnapshotRequest(
                connector_name="rest",
                resource_name="orders",
                tenant_id="tenant-demo",
                request_id="req-rest-dns-rebind",
                rest=RestSourceConfig(base_url="https://api.example.test", resource_path="/orders"),
            )
        )

    assert resolutions == ["93.184.216.34", "10.0.0.8"]
    assert exc_info.value.details["reason"] == "resolved_private_ip"


def test_rest_pinned_http_connection_uses_validated_ip_not_original_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolutions: list[str] = []
    connection_attempts: list[tuple[tuple[str, int], object, object]] = []

    def fake_getaddrinfo(
        host: str,
        port: int,
        *,
        type: socket.SocketKind,
    ) -> list[tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[str, int]]]:
        address = "93.184.216.34" if not resolutions else "10.0.0.8"
        resolutions.append(address)
        return [(socket.AF_INET, type, 0, "", (address, port))]

    def fake_create_connection(address: tuple[str, int], timeout: object, source_address: object) -> object:
        connection_attempts.append((address, timeout, source_address))
        return object()

    monkeypatch.setattr(rest_connector_module.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(rest_connector_module.socket, "create_connection", fake_create_connection)

    target = rest_connector_module._validated_http_target(
        "http://api.example.test/orders",
        allow_private_network=False,
    )
    connection = rest_connector_module._pinned_http_connection(target)("api.example.test", timeout=5)
    connection.connect()

    assert resolutions == ["93.184.216.34"]
    assert connection_attempts == [(("93.184.216.34", 80), 5, None)]


def test_rest_pinned_https_connection_preserves_sni_for_original_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection_attempts: list[tuple[tuple[str, int], object, object]] = []
    server_names: list[str | None] = []

    class FakeTLSContext:
        def wrap_socket(self, sock: object, *, server_hostname: str | None) -> object:
            server_names.append(server_hostname)
            return sock

    def fake_getaddrinfo(
        host: str,
        port: int,
        *,
        type: socket.SocketKind,
    ) -> list[tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[str, int]]]:
        return [(socket.AF_INET, type, 0, "", ("93.184.216.34", port))]

    def fake_create_connection(address: tuple[str, int], timeout: object, source_address: object) -> object:
        connection_attempts.append((address, timeout, source_address))
        return object()

    monkeypatch.setattr(rest_connector_module.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(rest_connector_module.socket, "create_connection", fake_create_connection)

    target = rest_connector_module._validated_http_target(
        "https://api.example.test/orders",
        allow_private_network=False,
    )
    connection = rest_connector_module._pinned_https_connection(target)(
        "api.example.test",
        timeout=5,
        context=FakeTLSContext(),
    )
    connection.connect()

    assert connection_attempts == [(("93.184.216.34", 443), 5, None)]
    assert server_names == ["api.example.test"]


def test_rest_redirect_handler_sets_validated_target_for_public_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_getaddrinfo(
        host: str,
        port: int,
        *,
        type: socket.SocketKind,
    ) -> list[tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[str, int]]]:
        return [(socket.AF_INET, type, 0, "", ("93.184.216.34", port))]

    monkeypatch.setattr(rest_connector_module.socket, "getaddrinfo", fake_getaddrinfo)

    handler = rest_connector_module._ValidatingRedirectHandler(allow_private_network=False)
    redirected = handler.redirect_request(
        Request("https://api.example.test/orders"),
        object(),
        302,
        "Found",
        HTTPMessage(),
        "https://api.example.test/orders?cursor=next",
    )

    assert redirected is not None
    target = getattr(redirected, rest_connector_module._TARGET_ATTR)
    assert target.host == "api.example.test"
    assert target.resolved_host == "93.184.216.34"


def test_rest_https_handler_uses_pinned_connection_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, object]] = []

    def fake_getaddrinfo(
        host: str,
        port: int,
        *,
        type: socket.SocketKind,
    ) -> list[tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[str, int]]]:
        return [(socket.AF_INET, type, 0, "", ("93.184.216.34", port))]

    def fake_do_open(connection_factory: object, request: Request, **kwargs: object) -> _FakeHTTPResponse:
        calls.append((connection_factory.__name__, request.full_url, kwargs["context"]))
        return _FakeHTTPResponse({"items": [], "nextCursor": None})

    monkeypatch.setattr(rest_connector_module.socket, "getaddrinfo", fake_getaddrinfo)
    handler = rest_connector_module._PinnedHTTPSHandler(allow_private_network=False)
    monkeypatch.setattr(handler, "do_open", fake_do_open)

    response = handler.https_open(Request("https://api.example.test/orders"))

    assert isinstance(response, _FakeHTTPResponse)
    assert calls[0][0:2] == ("_PinnedHTTPSConnection", "https://api.example.test/orders")
    assert calls[0][2] is not None


def test_rest_pinned_http_connection_tunnels_after_connecting_validated_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tunnels: list[str] = []
    connection_attempts: list[tuple[str, int]] = []

    def fake_create_connection(address: tuple[str, int], timeout: object, source_address: object) -> object:
        connection_attempts.append(address)
        return object()

    monkeypatch.setattr(rest_connector_module.socket, "create_connection", fake_create_connection)

    target = rest_connector_module._ValidatedHttpTarget(
        url="http://api.example.test/orders",
        host="api.example.test",
        port=80,
        resolved_host="93.184.216.34",
    )
    connection = rest_connector_module._pinned_http_connection(target)("api.example.test", timeout=5)
    connection._tunnel_host = "proxy.example.test"
    connection._tunnel = lambda: tunnels.append("tunnel")
    connection.connect()

    assert connection_attempts == [("93.184.216.34", 80)]
    assert tunnels == ["tunnel"]


def test_rest_pinned_https_connection_tunnel_uses_tunnel_host_for_sni(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tunnels: list[str] = []
    server_names: list[str | None] = []

    class FakeTLSContext:
        def wrap_socket(self, sock: object, *, server_hostname: str | None) -> object:
            server_names.append(server_hostname)
            return sock

    def fake_create_connection(address: tuple[str, int], timeout: object, source_address: object) -> object:
        return object()

    monkeypatch.setattr(rest_connector_module.socket, "create_connection", fake_create_connection)

    target = rest_connector_module._ValidatedHttpTarget(
        url="https://api.example.test/orders",
        host="api.example.test",
        port=443,
        resolved_host="93.184.216.34",
    )
    connection = rest_connector_module._pinned_https_connection(target)(
        "api.example.test",
        timeout=5,
        context=FakeTLSContext(),
    )
    connection._tunnel_host = "proxy.example.test"
    connection._tunnel = lambda: tunnels.append("tunnel")
    connection.connect()

    assert tunnels == ["tunnel"]
    assert server_names == ["proxy.example.test"]


def test_rest_private_network_reason_helpers_cover_hostname_literal_and_resolved_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert rest_connector_module._private_network_reason_for_addresses("localhost", ()) == "local_hostname"
    assert rest_connector_module._private_network_reason("localhost", 443) == "local_hostname"
    assert rest_connector_module._private_network_reason("8.8.8.8", 443) is None

    monkeypatch.setattr(
        rest_connector_module,
        "_resolved_addresses",
        lambda host, port: (rest_connector_module.ip_address("10.0.0.7"),),
    )

    assert rest_connector_module._private_network_reason("api.example.test", 443) == "resolved_private_ip"


def test_rest_resolution_helpers_reject_empty_answers_and_skip_empty_sockaddr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(rest_connector_module, "_resolved_addresses", lambda host, port: ())

        with pytest.raises(ValidationFailed, match="could not be resolved") as exc_info:
            rest_connector_module._resolved_target_addresses("api.example.test", 443)

        assert exc_info.value.details == {"host": "api.example.test"}

    def fake_getaddrinfo(
        host: str,
        port: int,
        *,
        type: socket.SocketKind,
    ) -> list[tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[str, int] | tuple[()]]]:
        return [
            (socket.AF_INET, type, 0, "", ()),
            (socket.AF_INET, type, 0, "", ("93.184.216.34", port)),
            (socket.AF_INET, type, 0, "", ("93.184.216.34", port)),
        ]

    monkeypatch.setattr(rest_connector_module.socket, "getaddrinfo", fake_getaddrinfo)

    assert rest_connector_module._resolved_addresses("api.example.test", 443) == (
        rest_connector_module.ip_address("93.184.216.34"),
    )


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

    class SuccessfulOpener:
        def open(self, request: Request, timeout: int) -> _FakeHTTPResponse:
            requests.append((str(request.full_url), timeout))
            return _FakeHTTPResponse({"items": [{"order_id": "O-1003"}], "nextCursor": ""})

    def fake_build_opener(*_handlers: object) -> SuccessfulOpener:
        return SuccessfulOpener()

    monkeypatch.setattr(rest_connector_module.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(rest_connector_module, "build_opener", fake_build_opener)

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
        self._offset = 0

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def read(self, amt: int | None = None) -> bytes:
        if amt is None or amt < 0:
            chunk = self._body[self._offset :]
            self._offset = len(self._body)
            return chunk
        start = self._offset
        self._offset = min(len(self._body), start + amt)
        return self._body[start : self._offset]


def _handler_class() -> type[BaseHTTPRequestHandler]:
    requests: list[dict[str, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        requests: ClassVar[list[dict[str, str | None]]]

        def do_GET(self) -> None:
            split = urlsplit(self.path)
            params = parse_qs(split.query)
            cursor = params.get("cursor", [None])[0]
            skiptoken = params.get("$skiptoken", [None])[0]
            self.requests.append(
                {
                    "authorization": self.headers.get("Authorization"),
                    "api_key": self.headers.get("X-Api-Key"),
                    "cursor": cursor,
                    "skiptoken": skiptoken,
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
            if split.path == "/large-response":
                self._write_json({"items": [{"payload": "x" * 1024}], "nextCursor": None})
                return
            if split.path == "/array-payload":
                self._write_json([])
                return
            if split.path == "/odata/SalesOrderSet":
                if skiptoken == "page-2":
                    self._write_json({"value": [{"SalesOrder": "500002", "Status": "Released"}]})
                    return
                host = self.headers.get("Host")
                self._write_json(
                    {
                        "value": [{"SalesOrder": "500001", "Status": "Open"}],
                        "@odata.nextLink": (f"http://{host}/odata/SalesOrderSet?$skiptoken=page-2&$format=json"),
                    }
                )
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
