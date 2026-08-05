from __future__ import annotations

import io
import json
from argparse import Namespace
from collections.abc import Mapping
from urllib.request import Request

from scripts.ontology_mcp_stdio import StdioProxyConfig, _config, forward_message, run_stdio


class _Response:
    def __init__(self, payload: Mapping[str, object] | None, *, status: int, session_id: str) -> None:
        self.status = status
        self.headers = {"Mcp-Session-Id": session_id}
        self._payload = payload

    def read(self, amount: int = -1) -> bytes:
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

    def opener(request: Request, *, timeout: float) -> _Response:
        return _Response({"jsonrpc": "2.0", "id": 1, "result": {}}, status=200, session_id="session-1")

    from scripts import ontology_mcp_stdio

    original = ontology_mcp_stdio.forward_message
    ontology_mcp_stdio.forward_message = lambda proxy, payload, session_id: original(  # type: ignore[assignment]
        proxy, payload, session_id=session_id, opener=opener
    )
    try:
        assert run_stdio(config, source=source, sink=sink) == 0
    finally:
        ontology_mcp_stdio.forward_message = original

    assert json.loads(sink.getvalue())["result"] == {}
    assert config.access_token == "token-from-env"


def _rpc(rpc_id: int, method: str) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": {}}
