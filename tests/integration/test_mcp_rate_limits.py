"""End-to-end admission proof for both authenticated MCP planes."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from foundry_lite.application.services.mcp_rate_limit_service import McpRateLimitConfig
from foundry_lite.infrastructure import schema as db
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.main import app
from sqlalchemy import func, select

from tests.integration.test_action_contract_v3_apply import _prepare_v3_demo
from tests.integration.test_ai_fde_platform import (
    _builder_mcp_application,
)
from tests.integration.test_ai_fde_platform import (
    _mcp_initialize_params as _builder_initialize_params,
)
from tests.integration.test_ontology_mcp import (
    MCP_USER,
    _mcp_application,
    _user_mcp_headers,
)
from tests.integration.test_ontology_mcp import (
    _mcp_initialize_params as _ontology_initialize_params,
)


def test_builder_tool_names_cannot_rotate_the_shared_tool_bucket(
    foundry: Any,
    monkeypatch: Any,
) -> None:
    app_id, headers = _builder_mcp_application(foundry, monkeypatch, "platform_qa")
    _configure_limits(foundry, endpoint_limit=20, tool_limit=1)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    session_headers = _initialize_builder(client, app_id, headers)

    first_payload = _builder_call(
        "builder-tool-1",
        "load_foundry_documentation_page",
        {"documentId": "missing-rate-limit-replay-document"},
    )
    first = client.post(
        f"/mcp/builder/{app_id}",
        headers={**session_headers, "X-Request-ID": "builder-tool-first"},
        json=first_payload,
    )
    replay = client.post(
        f"/mcp/builder/{app_id}",
        headers={**session_headers, "X-Request-ID": "builder-tool-replay"},
        json=first_payload,
    )
    denied = client.post(
        f"/mcp/builder/{app_id}",
        headers={**session_headers, "X-Request-ID": "builder-tool-denied"},
        json=_builder_call(
            "builder-tool-2",
            "platform.docs.search",
            {"query": "quality gate", "maxResults": 5},
        ),
    )

    assert first.status_code == 200
    assert first.json()["result"]["isError"] is True
    assert replay.status_code == 200
    assert replay.json()["result"]["isError"] is True
    assert replay.json()["result"]["isReplayed"] is True
    assert replay.json()["result"]["structuredContent"] == first.json()["result"]["structuredContent"]
    assert "Retry-After" not in replay.headers
    _assert_tool_denial(denied, "builder-tool-denied")
    _assert_one_denial_pair(foundry, plane="builder", application_id=app_id)


def test_ontology_tool_names_cannot_rotate_the_shared_tool_bucket(
    foundry: Any,
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    _prepare_v3_demo(foundry, tmp_path)
    app_id = _mcp_application(foundry)
    headers = _user_mcp_headers(foundry, monkeypatch, app_id, suffix="rate-limit-tools")
    _configure_limits(foundry, endpoint_limit=20, tool_limit=2)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    session_headers = _initialize_ontology(client, app_id, headers)

    current_run = foundry.objects.get("Order", "O-1001", ctx=MCP_USER)
    run_payload = _ontology_call(
        "ontology-run-1",
        "action.ExpediteOrder.apply",
        {
            "objectType": "Order",
            "objectId": "O-1001",
            "expectedObjectVersion": current_run["objectVersion"],
            "params": {"mode": "urgent"},
        },
    )
    first_run = client.post(
        f"/mcp/ontology/{app_id}",
        headers={**session_headers, "X-Request-ID": "ontology-run-first"},
        json=run_payload,
    )
    replayed_run = client.post(
        f"/mcp/ontology/{app_id}",
        headers={**session_headers, "X-Request-ID": "ontology-run-replay"},
        json=run_payload,
    )
    current_proposal = foundry.objects.get("Order", "O-1002", ctx=MCP_USER)
    proposal_payload = _ontology_call(
        "ontology-proposal-1",
        "action.ApproveOrder.apply",
        {
            "objectType": "Order",
            "objectId": "O-1002",
            "expectedObjectVersion": current_proposal["objectVersion"],
            "params": {"reason": "durable replay must not consume tool quota"},
        },
    )
    first_proposal = client.post(
        f"/mcp/ontology/{app_id}",
        headers={**session_headers, "X-Request-ID": "ontology-proposal-first"},
        json=proposal_payload,
    )
    replayed_proposal = client.post(
        f"/mcp/ontology/{app_id}",
        headers={**session_headers, "X-Request-ID": "ontology-proposal-replay"},
        json=proposal_payload,
    )
    denied = client.post(
        f"/mcp/ontology/{app_id}",
        headers={**session_headers, "X-Request-ID": "ontology-tool-denied"},
        json=_ontology_call("ontology-tool-2", "object.Order.search", {"limit": 1}),
    )

    first_run_result = first_run.json()["result"]["structuredContent"]
    replayed_run_result = replayed_run.json()["result"]["structuredContent"]
    first_proposal_result = first_proposal.json()["result"]["structuredContent"]
    replayed_proposal_result = replayed_proposal.json()["result"]["structuredContent"]
    assert replayed_run_result["actionRunId"] == first_run_result["actionRunId"]
    assert replayed_proposal_result["reviewId"] == first_proposal_result["reviewId"]
    assert "Retry-After" not in replayed_run.headers
    assert "Retry-After" not in replayed_proposal.headers
    _assert_tool_denial(denied, "ontology-tool-denied")
    _assert_one_denial_pair(foundry, plane="ontology", application_id=app_id, request_count=3)


def test_both_mcp_endpoint_limits_return_http_429_with_exact_retry_after(
    foundry: Any,
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    _prepare_v3_demo(foundry, tmp_path)
    ontology_app_id = _mcp_application(foundry)
    ontology_headers = _user_mcp_headers(foundry, monkeypatch, ontology_app_id, suffix="rate-limit-http")
    builder_app_id, builder_headers = _builder_mcp_application(foundry, monkeypatch, "platform_qa")
    _configure_limits(foundry, endpoint_limit=1, tool_limit=20)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)

    _initialize_builder(client, builder_app_id, builder_headers)
    builder_denied = client.post(
        f"/mcp/builder/{builder_app_id}",
        headers={**builder_headers, "X-Request-ID": "builder-endpoint-denied"},
        json={"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}},
    )
    _initialize_ontology(client, ontology_app_id, ontology_headers)
    ontology_denied = client.post(
        f"/mcp/ontology/{ontology_app_id}",
        headers={**ontology_headers, "X-Request-ID": "ontology-endpoint-denied"},
        json={"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}},
    )

    _assert_http_denial(builder_denied, "builder-endpoint-denied")
    _assert_http_denial(ontology_denied, "ontology-endpoint-denied")


@pytest.mark.parametrize("plane", ["builder", "ontology"])
@pytest.mark.parametrize("method", ["GET", "DELETE"])
def test_mcp_get_and_delete_consume_endpoint_quota_before_session_mutation(
    foundry: Any,
    tmp_path: Any,
    monkeypatch: Any,
    plane: str,
    method: str,
) -> None:
    client, app_id, session_headers = _plane_session(foundry, tmp_path, monkeypatch, plane)
    _configure_limits(foundry, endpoint_limit=1, tool_limit=20)
    request_id = f"{plane}-{method.lower()}-endpoint-denied"

    denied = client.request(
        method,
        f"/mcp/{plane}/{app_id}",
        headers={**session_headers, "X-Request-ID": request_id},
    )

    _assert_http_denial(denied, request_id)
    with foundry.engine.connect() as conn:
        session = (
            conn.execute(
                select(db.osdk_mcp_sessions).where(db.osdk_mcp_sessions.c.id == session_headers["Mcp-Session-Id"])
            )
            .mappings()
            .one()
        )
    assert session["status"] == "active"
    assert session["stream_lease_id"] is None


@pytest.mark.parametrize("plane", ["builder", "ontology"])
def test_mcp_unknown_and_terminated_notifications_and_repeated_delete_return_404(
    foundry: Any,
    tmp_path: Any,
    monkeypatch: Any,
    plane: str,
) -> None:
    client, app_id, session_headers = _plane_session(foundry, tmp_path, monkeypatch, plane)
    unknown_id = f"{plane}-mcp-unknown000" if plane == "ontology" else f"mcp-{'0' * 32}"
    unknown = client.post(
        f"/mcp/{plane}/{app_id}",
        headers={**session_headers, "Mcp-Session-Id": unknown_id},
        json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
    )
    closed = client.delete(f"/mcp/{plane}/{app_id}", headers=session_headers)
    terminated = client.post(
        f"/mcp/{plane}/{app_id}",
        headers=session_headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
    )
    repeated_delete = client.delete(f"/mcp/{plane}/{app_id}", headers=session_headers)

    assert unknown.status_code == 404
    assert closed.status_code == 204
    assert terminated.status_code == 404
    assert repeated_delete.status_code == 404
    assert unknown.json()["detail"]["details"]["resource"] == "mcp_session"
    assert terminated.json()["detail"]["details"]["resource"] == "mcp_session"


def test_builder_numeric_and_string_json_rpc_ids_have_distinct_internal_runs(
    foundry: Any,
    monkeypatch: Any,
) -> None:
    app_id, headers = _builder_mcp_application(foundry, monkeypatch, "platform_qa")
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    session_headers = _initialize_builder(client, app_id, headers)
    arguments = {"query": "typed JSON-RPC identity", "maxResults": 5}

    numeric = client.post(
        f"/mcp/builder/{app_id}",
        headers=session_headers,
        json=_builder_call(1, "search_tools", arguments),
    )
    textual = client.post(
        f"/mcp/builder/{app_id}",
        headers=session_headers,
        json=_builder_call("1", "search_tools", arguments),
    )

    numeric_body = numeric.json()
    textual_body = textual.json()
    assert numeric_body["id"] == 1 and type(numeric_body["id"]) is int
    assert textual_body["id"] == "1" and type(textual_body["id"]) is str
    assert numeric_body["result"]["aiRunId"] != textual_body["result"]["aiRunId"]
    assert numeric_body["result"]["isReplayed"] is False
    assert textual_body["result"]["isReplayed"] is False


def test_ontology_numeric_and_string_json_rpc_ids_preserve_wire_and_evidence_types(
    foundry: Any,
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    _prepare_v3_demo(foundry, tmp_path)
    app_id = _mcp_application(foundry)
    headers = _user_mcp_headers(foundry, monkeypatch, app_id, suffix="typed-json-rpc")
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    session_headers = _initialize_ontology(client, app_id, headers)
    current = foundry.objects.get("Order", "O-1002", ctx=MCP_USER)
    arguments = {
        "objectType": "Order",
        "objectId": "O-1002",
        "expectedObjectVersion": current["objectVersion"],
        "params": {"reason": "preserve the public JSON-RPC id type"},
    }

    numeric = client.post(
        f"/mcp/ontology/{app_id}",
        headers=session_headers,
        json=_ontology_call(1, "action.ApproveOrder.apply", arguments),
    )
    textual = client.post(
        f"/mcp/ontology/{app_id}",
        headers=session_headers,
        json=_ontology_call("1", "action.ApproveOrder.apply", arguments),
    )

    numeric_body = numeric.json()
    textual_body = textual.json()
    numeric_result = numeric_body["result"]["structuredContent"]
    textual_result = textual_body["result"]["structuredContent"]
    assert numeric_body["id"] == 1 and type(numeric_body["id"]) is int
    assert textual_body["id"] == "1" and type(textual_body["id"]) is str
    assert numeric_result["reviewId"] != textual_result["reviewId"]
    assert numeric_result["proposalId"] != textual_result["proposalId"]
    reviews = [
        foundry._services.insight_review.review_detail(str(result["reviewId"]), ctx=MCP_USER)
        for result in (numeric_result, textual_result)
    ]
    proposals = [review["actionProposal"] for review in reviews]
    assert [proposal["jsonRpcId"] for proposal in proposals] == [1, "1"]
    assert [proposal["evidenceRefs"][0]["jsonRpcId"] for proposal in proposals] == [1, "1"]
    with foundry.engine.connect() as conn:
        events = (
            conn.execute(
                select(db.osdk_mcp_session_events.c.payload_json)
                .where(db.osdk_mcp_session_events.c.session_id == session_headers["Mcp-Session-Id"])
                .order_by(db.osdk_mcp_session_events.c.sequence)
            )
            .scalars()
            .all()
        )
    completed_ids = [event["jsonRpcId"] for event in events if "jsonRpcId" in event]
    assert completed_ids[-2:] == [1, "1"]


def _plane_session(foundry: Any, tmp_path: Any, monkeypatch: Any, plane: str) -> tuple[TestClient, str, dict[str, str]]:
    _configure_limits(foundry, endpoint_limit=20, tool_limit=20)
    if plane == "builder":
        app_id, headers = _builder_mcp_application(foundry, monkeypatch, "platform_qa")
    else:
        _prepare_v3_demo(foundry, tmp_path)
        app_id = _mcp_application(foundry)
        headers = _user_mcp_headers(foundry, monkeypatch, app_id, suffix=f"session-{plane}")
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    initializer = _initialize_builder if plane == "builder" else _initialize_ontology
    return client, app_id, initializer(client, app_id, headers)


def _configure_limits(foundry: Any, *, endpoint_limit: int, tool_limit: int) -> None:
    foundry._services.mcp_rate_limits.config = McpRateLimitConfig(
        endpoint_limit=endpoint_limit,
        tool_limit=tool_limit,
        window_seconds=60,
    )


def _initialize_builder(client: TestClient, app_id: str, headers: dict[str, str]) -> dict[str, str]:
    response = client.post(
        f"/mcp/builder/{app_id}",
        headers={**headers, "X-Request-ID": "builder-rate-limit-init"},
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _builder_initialize_params()},
    )
    assert response.status_code == 200, response.text
    return {**headers, "Mcp-Session-Id": response.headers["Mcp-Session-Id"]}


def _initialize_ontology(client: TestClient, app_id: str, headers: dict[str, str]) -> dict[str, str]:
    response = client.post(
        f"/mcp/ontology/{app_id}",
        headers={**headers, "X-Request-ID": "ontology-rate-limit-init"},
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _ontology_initialize_params()},
    )
    assert response.status_code == 200, response.text
    return {**headers, "Mcp-Session-Id": response.headers["Mcp-Session-Id"]}


def _builder_call(rpc_id: str | int, tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": {
                "mode": "platform_qa",
                "workspaceRef": "tenant:tenant-demo",
                "arguments": arguments,
            },
        },
    }


def _ontology_call(rpc_id: str | int, tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }


def _assert_tool_denial(response: Any, request_id: str) -> None:
    assert response.status_code == 200
    result = response.json()["result"]
    error = result["structuredContent"]["error"]
    assert result["isError"] is True
    assert error["type"] == "RATE_LIMITED"
    assert error["requestId"] == request_id
    assert error["details"]["requestId"] == request_id
    assert error["details"]["retryAfterSeconds"] >= 1
    assert response.headers["Retry-After"] == str(error["details"]["retryAfterSeconds"])
    assert "aiRunId" not in result
    assert "toolCallId" not in result


def _assert_http_denial(response: Any, request_id: str) -> None:
    assert response.status_code == 429
    detail = response.json()["detail"]
    assert detail["code"] == "RATE_LIMITED"
    assert detail["request_id"] == request_id
    assert detail["details"]["requestId"] == request_id
    assert response.headers["Retry-After"] == str(detail["details"]["retryAfterSeconds"])


def _assert_one_denial_pair(foundry: Any, *, plane: str, application_id: str, request_count: int = 2) -> None:
    with foundry.engine.connect() as conn:
        window = (
            conn.execute(
                select(db.mcp_rate_limit_windows).where(
                    db.mcp_rate_limit_windows.c.plane == plane,
                    db.mcp_rate_limit_windows.c.application_id == application_id,
                    db.mcp_rate_limit_windows.c.limit_scope == "tool",
                )
            )
            .mappings()
            .one()
        )
        audit_count = conn.execute(
            select(func.count())
            .select_from(db.audit_events)
            .where(
                db.audit_events.c.event_type == "mcp.rate_limit.denied",
                db.audit_events.c.resource_id == window["id"],
            )
        ).scalar_one()
        outbox_count = conn.execute(
            select(func.count())
            .select_from(db.outbox_events)
            .where(
                db.outbox_events.c.event_type == "mcp.rate_limit.denied",
                db.outbox_events.c.aggregate_id == window["id"],
            )
        ).scalar_one()
    assert window["request_count"] == request_count
    assert window["denied_count"] == 1
    assert audit_count == 1
    assert outbox_count == 1
