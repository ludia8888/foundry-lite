from __future__ import annotations

import json
from argparse import Namespace
from collections.abc import Mapping
from typing import Any, cast
from urllib.request import Request

from scripts.builder_mcp_stdio import main
from scripts.ontology_mcp_stdio import StdioProxyConfig, _config, forward_message


class _Response:
    def __init__(self, payload: Mapping[str, object], *, status: int, session_id: str) -> None:
        self.status = status
        self.headers = {"Mcp-Session-Id": session_id}
        self._payload = payload

    def read(self, amount: int = -1) -> bytes:
        del amount
        return json.dumps(self._payload).encode()

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        del args


def _rpc(rpc_id: int, method: str) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": {}}


def test_builder_stdio_proxy_targets_builder_plane_without_reusable_confirmation_header() -> None:
    requests: list[Request] = []

    def opener(request: Request, *, timeout: float) -> _Response:
        assert timeout == 30
        requests.append(request)
        return _Response({"jsonrpc": "2.0", "id": 1, "result": {}}, status=200, session_id="builder-1")

    config = StdioProxyConfig(
        "https://foundry.example",
        "app/build tools",
        "secret-access-token",
        plane="builder",
    )
    payload = _rpc(1, "tools/call")
    payload["params"] = {
        "name": "ontology.branch.apply_patch",
        "arguments": {"confirmationReceipt": "one-time-receipt"},
    }
    response, session_id = forward_message(
        config,
        payload,
        session_id=None,
        opener=cast(Any, opener),
    )

    assert response is not None and response["id"] == 1
    assert session_id == "builder-1"
    assert requests[0].full_url.endswith("/mcp/builder/app%2Fbuild%20tools")
    assert set(requests[0].headers) == {"Accept", "Authorization", "Content-type", "Mcp-protocol-version"}
    forwarded = json.loads(cast(bytes, requests[0].data).decode())
    assert forwarded["params"]["arguments"]["confirmationReceipt"] == "one-time-receipt"
    assert "secret-access-token" not in repr(config)


def test_builder_stdio_entrypoint_selects_builder_plane_without_exposing_token() -> None:
    args = Namespace(
        base_url="https://foundry.example",
        application_id="app-1",
        timeout_seconds=30,
        plane="builder",
    )
    config = _config(
        args,
        {
            "FOUNDRY_LITE_MCP_ACCESS_TOKEN": "token-from-env",
        },
    )

    assert config.plane == "builder"
    assert callable(main)
