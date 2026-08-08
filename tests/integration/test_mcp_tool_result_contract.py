"""Wire-level MCP tool-result compatibility and execution-error contracts."""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.main import app

from tests.integration.test_action_contract_v3_apply import _prepare_v3_demo
from tests.integration.test_ai_fde_platform import _builder_mcp_application, _mcp_tool_call_payload
from tests.integration.test_ontology_mcp import _mcp_application, _user_mcp_headers

_PROTOCOL_VERSION = "2025-06-18"


def test_builder_mcp_mirrors_structured_results_and_returns_execution_errors(foundry: Any, monkeypatch: Any) -> None:
    app_id, headers = _builder_mcp_application(foundry, monkeypatch, "platform_qa")
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    session_headers = _open_session(client, f"/mcp/builder/{app_id}", headers)

    succeeded = _builder_call(client, app_id, session_headers, "result-success", "get_documentation_summaries", {})
    failed = _builder_call(
        client,
        app_id,
        session_headers,
        "result-not-found",
        "load_foundry_documentation_page",
        {"documentId": "missing-document"},
    )
    failed_replay = _builder_call(
        client,
        app_id,
        session_headers,
        "result-not-found",
        "load_foundry_documentation_page",
        {"documentId": "missing-document"},
    )

    _assert_text_mirror(succeeded)
    _assert_text_mirror(failed)
    _assert_text_mirror(failed_replay)
    assert succeeded["isError"] is False
    assert failed["isError"] is True
    assert failed_replay["isError"] is True
    assert failed_replay["isReplayed"] is True
    assert failed_replay["structuredContent"] == failed["structuredContent"]
    assert failed["structuredContent"]["error"] == {
        "type": "NOT_FOUND",
        "message": "curated platform document not found",
        "details": {"documentId": "missing-document"},
        "requestId": "builder-platform-qa",
    }


def test_ontology_mcp_mirrors_structured_results_and_returns_execution_errors(
    foundry: Any, tmp_path: Any, monkeypatch: Any
) -> None:
    _prepare_v3_demo(foundry, tmp_path)
    app_id = _mcp_application(foundry)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    headers = _user_mcp_headers(foundry, monkeypatch, app_id)
    client = TestClient(app)
    session_headers = _open_session(client, f"/mcp/ontology/{app_id}", headers)

    succeeded = _ontology_call(client, app_id, session_headers, "result-success", "O-1001")
    failed = _ontology_call(client, app_id, session_headers, "result-not-found", "missing-order")

    _assert_text_mirror(succeeded)
    _assert_text_mirror(failed)
    assert succeeded["isError"] is False
    assert failed["isError"] is True
    assert failed["structuredContent"]["error"]["type"] == "NOT_FOUND"
    assert failed["structuredContent"]["error"]["requestId"].startswith("ontology-mcp-")


def test_builder_mcp_approval_challenge_has_a_structured_text_mirror(foundry: Any, monkeypatch: Any) -> None:
    app_id, headers = _builder_mcp_application(foundry, monkeypatch, "governance")
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    session_headers = _open_session(client, f"/mcp/builder/{app_id}", headers)
    payload = _mcp_tool_call_payload(
        "approval-mirror",
        "governance",
        "tenant:tenant-demo",
        "create_foundry_project",
        {"displayName": "Mirror Only", "idempotencyKey": "approval-mirror"},
    )

    response = client.post(f"/mcp/builder/{app_id}", headers=session_headers, json=payload)
    result = response.json()["result"]

    assert response.status_code == 200
    assert result["structuredContent"]["status"] == "approval_required"
    _assert_text_mirror(result)


def _open_session(client: TestClient, path: str, headers: dict[str, str]) -> dict[str, str]:
    initialized = client.post(
        path,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": "initialize-result-contract",
            "method": "initialize",
            "params": {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "foundry-lite-result-contract", "version": "1.0.0"},
            },
        },
    )
    assert initialized.status_code == 200, initialized.text
    session_headers = {
        **headers,
        "Mcp-Session-Id": initialized.headers["Mcp-Session-Id"],
        "MCP-Protocol-Version": _PROTOCOL_VERSION,
    }
    acknowledged = client.post(
        path,
        headers=session_headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
    )
    assert acknowledged.status_code == 202
    return session_headers


def _builder_call(
    client: TestClient,
    app_id: str,
    headers: dict[str, str],
    rpc_id: str,
    tool_name: str,
    arguments: dict[str, object],
) -> dict[str, Any]:
    payload = _mcp_tool_call_payload(
        rpc_id,
        "platform_qa",
        "tenant:tenant-demo",
        tool_name,
        arguments,
    )
    response = client.post(f"/mcp/builder/{app_id}", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert "error" not in body, body
    return dict(body["result"])


def _ontology_call(
    client: TestClient,
    app_id: str,
    headers: dict[str, str],
    rpc_id: str,
    object_id: str,
) -> dict[str, Any]:
    response = client.post(
        f"/mcp/ontology/{app_id}",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": "tools/call",
            "params": {"name": "object.Order.get", "arguments": {"objectId": object_id}},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "error" not in body, body
    return dict(body["result"])


def _assert_text_mirror(result: dict[str, Any]) -> None:
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    assert result["content"][0]["text"] == json.dumps(
        result["structuredContent"], ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
