from __future__ import annotations

import io
import json
from argparse import Namespace
from collections.abc import Mapping
from urllib.request import Request

from scripts.ontology_mcp_stdio import (
    StdioProxyConfig,
    _config,
    forward_message,
    forward_notifications,
    run_stdio,
)


class _Response:
    def __init__(
        self,
        payload: Mapping[str, object] | None,
        *,
        status: int,
        session_id: str,
        raw: bytes | None = None,
    ) -> None:
        self.status = status
        self.headers = {"Mcp-Session-Id": session_id}
        self._payload = payload
        self._raw = raw

    def read(self, amount: int = -1) -> bytes:
        if self._raw is not None:
            return self._raw[:amount] if amount >= 0 else self._raw
        return json.dumps(self._payload).encode() if self._payload is not None else b""

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_stdio_proxy_forwards_bearer_and_resumes_http_mcp_session_without_leaking_token() -> None:
    requests: list[Request] = []
    responses = [
        _Response(
            {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-06-18"}},
            status=200,
            session_id="session-1",
        ),
        _Response({"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}, status=200, session_id="session-1"),
    ]

    def opener(request: Request, *, timeout: float) -> _Response:
        assert timeout == 30
        requests.append(request)
        return responses.pop(0)

    config = StdioProxyConfig("https://foundry.example", "app/orders", "secret-access-token")
    first, session_id = forward_message(config, _rpc(1, "initialize"), session_id=None, opener=opener)
    second, resumed = forward_message(config, _rpc(2, "tools/list"), session_id=session_id, opener=opener)

    assert first is not None and first["id"] == 1
    assert second is not None and second["id"] == 2
    assert resumed == "session-1"
    assert requests[0].full_url.endswith("/mcp/ontology/app%2Forders")
    assert requests[0].headers["Authorization"] == "Bearer secret-access-token"
    assert requests[1].headers["Mcp-session-id"] == "session-1"
    assert "secret-access-token" not in repr(config)


def test_stdio_loop_outputs_json_rpc_response_and_requires_token_from_environment() -> None:
    args = Namespace(base_url="https://foundry.example", application_id="app-1", timeout_seconds=30)
    config = _config(args, {"FOUNDRY_LITE_MCP_ACCESS_TOKEN": "token-from-env"})
    source = io.StringIO(json.dumps(_rpc(1, "initialize")) + "\n")
    sink = io.StringIO()

    requests: list[Request] = []

    def opener(request: Request, *, timeout: float) -> _Response:
        assert timeout == 30
        requests.append(request)
        if request.get_method() == "POST":
            return _Response({"jsonrpc": "2.0", "id": 1, "result": {}}, status=200, session_id="session-1")
        if request.get_method() == "GET":
            notification = {"jsonrpc": "2.0", "method": "notifications/message", "params": {"ready": True}}
            raw = f"id: session-1:1\nevent: session.opened\ndata: {json.dumps(notification)}\n\n".encode()
            return _Response(None, status=200, session_id="session-1", raw=raw)
        return _Response(None, status=204, session_id="session-1")

    assert run_stdio(config, source=source, sink=sink, opener=opener) == 0

    output = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert output[0]["result"] == {}
    assert output[1]["method"] == "notifications/message"
    assert [request.get_method() for request in requests] == ["POST", "GET", "DELETE"]
    assert requests[2].headers["Mcp-session-id"] == "session-1"
    assert config.access_token == "token-from-env"


def test_stdio_sse_resume_forwards_only_new_event_ids() -> None:
    requests: list[Request] = []
    notification_1 = {"jsonrpc": "2.0", "method": "notifications/message", "params": {"sequence": 1}}
    notification_2 = {"jsonrpc": "2.0", "method": "notifications/message", "params": {"sequence": 2}}
    responses = [
        _Response(
            None,
            status=200,
            session_id="session-1",
            raw=f"id: session-1:1\ndata: {json.dumps(notification_1)}\n\n".encode(),
        ),
        _Response(
            None,
            status=200,
            session_id="session-1",
            raw=(
                f"id: session-1:1\ndata: {json.dumps(notification_1)}\n\n"
                f"id: session-1:2\ndata: {json.dumps(notification_2)}\n\n"
            ).encode(),
        ),
    ]

    def opener(request: Request, *, timeout: float) -> _Response:
        assert timeout == 30
        requests.append(request)
        return responses.pop(0)

    config = StdioProxyConfig("https://foundry.example", "app-1", "secret-access-token")
    first, cursor = forward_notifications(config, "session-1", last_event_id=None, opener=opener)
    second, resumed = forward_notifications(config, "session-1", last_event_id=cursor, opener=opener)

    assert first == [notification_1]
    assert second == [notification_2]
    assert resumed == "session-1:2"
    assert requests[1].headers["Last-event-id"] == "session-1:1"


def _rpc(rpc_id: int, method: str) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": {}}
