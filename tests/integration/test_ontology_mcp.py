"""Consumer Ontology MCP application restriction and Action policy proof."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import replace
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, PermissionDenied, ValidationFailed
from foundry_lite.infrastructure.auth import JwtOidcAuthConfig, JwtOidcAuthProvider
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.main import app
from foundry_lite_api.ontology_mcp_ui import BUSINESS_SYSTEM_RESOURCE_URI
from foundry_lite_api.routers import ontology_mcp as ontology_mcp_router

from tests.integration.test_action_contract_v3_apply import _prepare_demo, _prepare_v3_demo, _v3_ontology

MCP_USER = RequestContext(
    tenant_id="tenant-demo",
    actor_user_id="ontology-mcp-user",
    roles=("admin", "ops_manager", "data_engineer"),
    request_id="ontology-mcp-setup",
)
MCP_REVIEWER = RequestContext(
    tenant_id="tenant-demo",
    actor_user_id="ontology-mcp-human-reviewer",
    roles=("admin", "ops_manager", "data_engineer"),
    request_id="ontology-mcp-human-review",
)


def test_ontology_mcp_projects_only_app_resources_and_enforces_action_risk(
    foundry: Any,
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    _prepare_v3_demo(foundry, tmp_path)
    app_id = _mcp_application(foundry)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    headers = _user_mcp_headers(foundry, monkeypatch, app_id)
    client = TestClient(app)

    initialized = client.post(
        f"/mcp/ontology/{app_id}",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _mcp_initialize_params()},
    )
    session_id = initialized.headers["Mcp-Session-Id"]
    request_headers = {**headers, "Mcp-Session-Id": session_id}
    listed = client.post(
        f"/mcp/ontology/{app_id}",
        headers=request_headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    tools = {item["name"]: item for item in listed.json()["result"]["tools"]}

    assert set(tools) == {
        "object.Order.get",
        "object.Order.search",
        "object.Order.unifiedSearch",
        "object.Order.links",
        "object.Order.searchAround",
        "action.ExpediteOrder.plan",
        "action.ExpediteOrder.apply",
        "action.ApproveOrder.plan",
        "action.ApproveOrder.apply",
        "action_run.get",
        "action_approval.get",
    }
    assert "object.Customer.get" not in tools
    # The consumer surface exposes meaning-based retrieval, not just keyword match, and an
    # object-anchored search that reaches into bound documents. Both stay behind the same
    # object:read grant, so search is not a way around the projection.
    assert "semanticText" in tools["object.Order.search"]["inputSchema"]["properties"]
    assert "object.Customer.unifiedSearch" not in tools
    assert tools["action.ExpediteOrder.apply"]["inputSchema"]["properties"]["params"]["required"] == ["mode"]
    assert tools["action.ExpediteOrder.apply"]["inputSchema"]["properties"]["objectType"]["const"] == "Order"

    object_result = _call(
        client,
        app_id,
        request_headers,
        rpc_id="get-order",
        name="object.Order.get",
        arguments={"objectId": "O-1001"},
    )
    before_version = object_result["objectVersion"]
    applied = _call(
        client,
        app_id,
        request_headers,
        rpc_id="apply-expedite",
        name="action.ExpediteOrder.apply",
        arguments={
            "objectType": "Order",
            "objectId": "O-1001",
            "expectedObjectVersion": before_version,
            "params": {"mode": "urgent"},
        },
    )
    replay = _call(
        client,
        app_id,
        request_headers,
        rpc_id="apply-expedite",
        name="action.ExpediteOrder.apply",
        arguments={
            "objectType": "Order",
            "objectId": "O-1001",
            "expectedObjectVersion": before_version,
            "params": {"mode": "urgent"},
        },
    )

    assert applied["status"] in {"queued", "running", "succeeded"}
    assert replay["actionRunId"] == applied["actionRunId"]

    current = foundry.objects.get("Order", "O-1002", ctx=MCP_USER)
    approval = _call(
        client,
        app_id,
        request_headers,
        rpc_id="high-risk-approval",
        name="action.ApproveOrder.apply",
        arguments={
            "objectType": "Order",
            "objectId": "O-1002",
            "expectedObjectVersion": current["objectVersion"],
            "params": {"reason": "MCP request requiring human review"},
        },
    )
    approval_replay = _call(
        client,
        app_id,
        request_headers,
        rpc_id="high-risk-approval",
        name="action.ApproveOrder.apply",
        arguments={
            "objectType": "Order",
            "objectId": "O-1002",
            "expectedObjectVersion": current["objectVersion"],
            "params": {"reason": "MCP request requiring human review"},
        },
    )
    conflicting_replay = client.post(
        f"/mcp/ontology/{app_id}",
        headers=request_headers,
        json={
            "jsonrpc": "2.0",
            "id": "high-risk-approval",
            "method": "tools/call",
            "params": {
                "name": "action.ApproveOrder.apply",
                "arguments": {
                    "objectType": "Order",
                    "objectId": "O-1002",
                    "expectedObjectVersion": current["objectVersion"],
                    "params": {"reason": "different payload under the same RPC id"},
                },
            },
        },
    )

    assert approval["status"] == "approval_required"
    assert str(approval["planHash"]).startswith("sha256:")
    assert str(approval["proposalId"]).startswith("aip-proposal-")
    assert str(approval["reviewId"]).startswith("insight_review_")
    assert approval_replay["reviewId"] == approval["reviewId"]
    conflict_result = conflicting_replay.json()["result"]
    assert conflicting_replay.status_code == 200
    assert conflict_result["isError"] is True
    assert conflict_result["structuredContent"]["error"]["type"] == "CONFLICT"
    reviews = foundry.insights.list(ctx=MCP_USER)
    assert [item["id"] for item in reviews["items"]] == [approval["reviewId"]]
    assert foundry.objects.get("Order", "O-1002", ctx=MCP_USER)["properties"]["status"] != "APPROVED"

    pending = _call(
        client,
        app_id,
        request_headers,
        rpc_id="approval-status-pending",
        name="action_approval.get",
        arguments={"reviewId": approval["reviewId"]},
    )
    with pytest.raises(NotFound):
        foundry._services.action_proposal.external_mcp_status(
            MCP_USER, application_id="another-application", review_id=str(approval["reviewId"])
        )
    foundry.insights.decide(
        str(approval["reviewId"]),
        decision="approved",
        idempotency_key="human-approve-external-mcp",
        ctx=MCP_REVIEWER,
    )
    executed = foundry.aip.execute_approved_action(
        review_id=str(approval["reviewId"]),
        expected_proposal_fingerprint=str(approval["proposalFingerprint"]),
        idempotency_key="human-execute-external-mcp",
        ctx=MCP_REVIEWER,
    )
    completed = _call(
        client,
        app_id,
        request_headers,
        rpc_id="approval-status-completed",
        name="action_approval.get",
        arguments={"reviewId": approval["reviewId"]},
    )
    run = _call(
        client,
        app_id,
        request_headers,
        rpc_id="approved-action-run-status",
        name="action_run.get",
        arguments={"runId": executed.action_run_id},
    )

    assert pending["status"] == "approval_pending"
    assert completed["status"] == "succeeded"
    assert completed["actionRunId"] == executed.action_run_id
    assert run["actionRunId"] == executed.action_run_id
    assert foundry.objects.get("Order", "O-1002", ctx=MCP_USER)["properties"]["status"] == "APPROVED"


def test_pilot_business_system_opens_the_same_live_work_screen_inside_gpt(
    foundry: Any,
    monkeypatch: Any,
) -> None:
    foundry.ontology.apply_text("objectTypes: []\nactionTypes: []\nlinkTypes: []\n", ctx=MCP_USER)
    arguments = _pilot_business_arguments()
    plan = foundry.aip.plan_pilot_application(arguments, ctx=MCP_USER)
    bundle = foundry.aip.generate_pilot_application(plan, idempotency_key="pilot-live-work", ctx=MCP_USER)
    branch = foundry.ontology.get_branch(str(bundle["ontologyBranch"]["id"]), ctx=MCP_USER)
    foundry.ontology.apply_text(str(branch["yamlText"]), ctx=MCP_USER)
    foundry.objects.reindex("WorkItem", ctx=MCP_USER)
    app_id = str(bundle["osdkApplication"]["application"]["id"])
    foundry.developer_console.configure_ontology_mcp_server(
        app_id,
        status="enabled",
        description_markdown="비개발자 업무 운영 화면",
        allowed_origins=("https://chat.example.test",),
        idempotency_key="pilot-live-work-mcp",
        ctx=MCP_USER,
    )
    scopes = tuple(str(scope) for resource in plan["applicationResources"] for scope in resource["scopes"])
    generated_role = str(plan["domainOsBlueprint"]["actorRoles"][0]["role"])
    operator = RequestContext(
        tenant_id=MCP_USER.tenant_id,
        actor_user_id=MCP_USER.actor_user_id,
        roles=(*MCP_USER.roles, generated_role),
        request_id="pilot-live-work-operator",
    )
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    headers = _user_mcp_headers(
        foundry,
        monkeypatch,
        app_id,
        scopes=scopes,
        actor_ctx=operator,
        suffix="pilot-live-work",
        is_default_scope_included=False,
    )
    client = TestClient(app)
    initialized = client.post(
        f"/mcp/ontology/{app_id}",
        headers=headers,
        json={"jsonrpc": "2.0", "id": "pilot-init", "method": "initialize", "params": _mcp_initialize_params()},
    )
    session_headers = {**headers, "Mcp-Session-Id": initialized.headers["Mcp-Session-Id"]}
    tools_response = client.post(
        f"/mcp/ontology/{app_id}",
        headers=session_headers,
        json={"jsonrpc": "2.0", "id": "pilot-tools", "method": "tools/list", "params": {}},
    )
    resources_response = client.post(
        f"/mcp/ontology/{app_id}",
        headers=session_headers,
        json={"jsonrpc": "2.0", "id": "pilot-resources", "method": "resources/list", "params": {}},
    )
    resource_response = client.post(
        f"/mcp/ontology/{app_id}",
        headers=session_headers,
        json={
            "jsonrpc": "2.0",
            "id": "pilot-resource",
            "method": "resources/read",
            "params": {"uri": BUSINESS_SYSTEM_RESOURCE_URI},
        },
    )

    tools = {tool["name"]: tool for tool in tools_response.json()["result"]["tools"]}
    assert tools["business_system.get"]["_meta"]["ui"]["resourceUri"] == BUSINESS_SYSTEM_RESOURCE_URI
    assert "object.WorkItem.search" in tools
    assert "action.CompleteWorkItem.apply" in tools
    assert resources_response.json()["result"]["resources"][0]["uri"] == BUSINESS_SYSTEM_RESOURCE_URI
    html = resource_response.json()["result"]["contents"][0]["text"]
    content_hash = hashlib.sha256(html.encode()).hexdigest()[:12]
    assert BUSINESS_SYSTEM_RESOURCE_URI == f"ui://foundry-lite/business-system-v4-{content_hash}.html"
    assert "createFoundryLiteBusinessSystemOsdk" in html

    definition_result = _call(
        client,
        app_id,
        session_headers,
        rpc_id="business-system-get",
        name="business_system.get",
        arguments={},
    )
    definition = definition_result["businessSystemDefinition"]
    assert definition["definitionFingerprint"] == bundle["businessSystemDefinition"]["definitionFingerprint"]
    surfaces = definition["experience"]["surfaces"]
    assert surfaces[0]["pageIds"] == surfaces[1]["pageIds"] == surfaces[2]["pageIds"]
    assert all(surface["runtime"] == "workshop" for surface in surfaces)
    operating = foundry.aip.get_operating_pilot_application(app_id, ctx=operator)
    assert operating["operatingApplication"]["status"] == "operating"
    assert operating["operatingApplication"]["definitionFingerprint"] == definition["definitionFingerprint"]
    assert operating["operatingApplication"]["operatingPath"] == bundle["operatingPath"]
    with pytest.raises(PermissionDenied, match="배정된 역할"):
        foundry.aip.operating_pilot_context(
            app_id,
            "object",
            "WorkItem",
            ctx=MCP_REVIEWER,
        )

    work = _call(
        client,
        app_id,
        session_headers,
        rpc_id="business-system-work",
        name="object.WorkItem.search",
        arguments={"limit": 50},
    )
    item = work["items"][0]
    proposed = _call(
        client,
        app_id,
        session_headers,
        rpc_id="business-system-action",
        name="action.CompleteWorkItem.apply",
        arguments={
            "objectType": "WorkItem",
            "objectId": item["objectId"],
            "expectedObjectVersion": item["objectVersion"],
            "params": {"completionNote": "현장 확인 완료"},
        },
    )
    assert proposed["status"] == "approval_required"
    assert foundry.objects.get("WorkItem", str(item["objectId"]), ctx=operator)["properties"]["status"] == "NEW"

    app_headers = _human_control_headers(foundry)
    queried = client.post(
        f"/api/aip/pilot/operating-applications/{app_id}/objects/WorkItem/query",
        headers=app_headers,
        json={"limit": 50},
    )
    assert queried.status_code == 200, queried.text
    app_item = queried.json()["items"][0]
    executed = client.post(
        f"/api/aip/pilot/operating-applications/{app_id}/actions/CompleteWorkItem/runs",
        params={"waitSeconds": 5},
        headers={**app_headers, "Idempotency-Key": "pilot-operating-complete"},
        json={
            "target": {"objectType": "WorkItem", "objectId": app_item["objectId"]},
            "expectedObjectVersion": app_item["objectVersion"],
            "params": {"completionNote": "사람이 운영 앱에서 확인하고 완료"},
        },
    )
    assert executed.status_code == 200
    assert executed.json()["status"] == "succeeded"
    completed = foundry.objects.get("WorkItem", str(app_item["objectId"]), ctx=operator)
    assert completed["properties"]["status"] == "DONE", executed.text


def test_ontology_mcp_rejects_untrusted_origin_and_application_mismatch(
    foundry: Any,
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    _prepare_v3_demo(foundry, tmp_path)
    app_id = _mcp_application(foundry)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    headers = _user_mcp_headers(foundry, monkeypatch, app_id)
    client = TestClient(app)

    origin_denied = client.post(
        f"/mcp/ontology/{app_id}",
        headers={**headers, "Origin": "https://attacker.invalid"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    initialized = client.post(
        f"/mcp/ontology/{app_id}",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": "mismatch-session",
            "method": "initialize",
            "params": _mcp_initialize_params(),
        },
    )
    mismatch = client.post(
        "/mcp/ontology/not-the-token-app",
        headers={**headers, "Mcp-Session-Id": initialized.headers["Mcp-Session-Id"]},
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )

    assert origin_denied.status_code == 400
    assert mismatch.status_code == 401
    assert mismatch.json()["detail"]["code"] == "PERMISSION_DENIED"


def test_external_mcp_approval_rechecks_current_application_scope_before_human_execution(
    foundry: Any,
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    _prepare_v3_demo(foundry, tmp_path)
    app_id = _mcp_application(foundry)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    headers = _user_mcp_headers(foundry, monkeypatch, app_id)
    client = TestClient(app)
    initialized = client.post(
        f"/mcp/ontology/{app_id}",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _mcp_initialize_params()},
    )
    request_headers = {**headers, "Mcp-Session-Id": initialized.headers["Mcp-Session-Id"]}
    current = foundry.objects.get("Order", "O-1002", ctx=MCP_USER)
    approval = _call(
        client,
        app_id,
        request_headers,
        rpc_id="approval-before-scope-revocation",
        name="action.ApproveOrder.apply",
        arguments={
            "objectType": "Order",
            "objectId": "O-1002",
            "expectedObjectVersion": current["objectVersion"],
            "params": {"reason": "Scope must still exist after human review."},
        },
    )
    foundry.insights.decide(
        str(approval["reviewId"]),
        decision="approved",
        idempotency_key="approve-before-scope-revocation",
        ctx=MCP_REVIEWER,
    )
    foundry.developer_console.update_osdk_application_resources(
        app_id,
        resources=(),
        idempotency_key="revoke-all-mcp-resources-before-execution",
        ctx=MCP_REVIEWER,
    )

    with pytest.raises(PermissionDenied):
        foundry.aip.execute_approved_action(
            review_id=str(approval["reviewId"]),
            expected_proposal_fingerprint=str(approval["proposalFingerprint"]),
            idempotency_key="execute-after-scope-revocation",
            ctx=MCP_REVIEWER,
        )

    review = foundry.insights.get(str(approval["reviewId"]), ctx=MCP_REVIEWER)
    assert review["executionStatus"] == "pending_review"
    assert foundry.objects.get("Order", "O-1002", ctx=MCP_USER)["properties"]["status"] != "APPROVED"


def test_ontology_mcp_hub_configuration_is_replay_safe_and_disable_fails_closed(
    foundry: Any,
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    _prepare_v3_demo(foundry, tmp_path)
    app_id = _mcp_application(foundry)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    headers = _user_mcp_headers(foundry, monkeypatch, app_id)
    client = TestClient(app)
    human_headers = _human_control_headers(foundry)
    hub = client.get("/api/developer-console/mcp-servers", headers=human_headers)
    initialized = client.post(
        f"/mcp/ontology/{app_id}",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": "disable-session",
            "method": "initialize",
            "params": _mcp_initialize_params(),
        },
    )
    request_headers = {**headers, "Mcp-Session-Id": initialized.headers["Mcp-Session-Id"]}
    disable_payload = {
        "status": "disabled",
        "descriptionMarkdown": "Order operations paused by an operator.",
        "allowedOrigins": ["https://chat.example.test"],
    }
    disabled = client.put(
        f"/api/developer-console/osdk-applications/{app_id}/mcp-server",
        headers={**human_headers, "Idempotency-Key": "disable-ontology-mcp"},
        json=disable_payload,
    )
    replay = client.put(
        f"/api/developer-console/osdk-applications/{app_id}/mcp-server",
        headers={**human_headers, "Idempotency-Key": "disable-ontology-mcp"},
        json=disable_payload,
    )
    denied = client.post(
        f"/mcp/ontology/{app_id}",
        headers=request_headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )

    assert hub.status_code == 200
    assert hub.json()[0]["applicationId"] == app_id
    assert hub.json()[0]["resourceCount"] == 4
    assert set(hub.json()[0]["authModes"]) == {"authorization_code_pkce", "client_credentials"}
    assert disabled.json()["status"] == "disabled"
    assert replay.json()["updated_at"] == disabled.json()["updated_at"]
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "PERMISSION_DENIED"


def test_ontology_mcp_durable_session_reconnect_resumes_after_last_event_and_delete_terminates(
    foundry: Any,
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    _prepare_v3_demo(foundry, tmp_path)
    app_id = _mcp_application(foundry)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    headers = _user_mcp_headers(foundry, monkeypatch, app_id)
    client = TestClient(app)
    initialized = client.post(
        f"/mcp/ontology/{app_id}",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _mcp_initialize_params()},
    )
    session_id = initialized.headers["Mcp-Session-Id"]
    request_headers = {**headers, "Mcp-Session-Id": session_id}
    accepted_notification = client.post(
        f"/mcp/ontology/{app_id}",
        headers=request_headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
    )
    ping = client.post(
        f"/mcp/ontology/{app_id}",
        headers=request_headers,
        json={"jsonrpc": "2.0", "id": "ping", "method": "ping", "params": {}},
    )
    rejected_notification = client.post(
        f"/mcp/ontology/{app_id}",
        headers=request_headers,
        json={"jsonrpc": "2.0", "method": "notifications/unsupported", "params": {}},
    )
    _call(
        client,
        app_id,
        request_headers,
        rpc_id="get-order-for-session-event",
        name="object.Order.get",
        arguments={"objectId": "O-1001"},
    )
    stream_ctx = api_runtime.get_auth_provider().authenticate_for_audience(headers, _ontology_resource(app_id))
    active_lease = foundry.ontology_mcp.claim_session_stream(stream_ctx, app_id, session_id)
    concurrent_stream = client.get(f"/mcp/ontology/{app_id}", headers=request_headers)
    assert foundry.ontology_mcp.release_session_stream(stream_ctx, app_id, session_id, active_lease.lease_id)
    all_events = client.get(f"/mcp/ontology/{app_id}", headers=request_headers)
    released_lease = foundry.ontology_mcp.claim_session_stream(stream_ctx, app_id, session_id)
    assert foundry.ontology_mcp.release_session_stream(stream_ctx, app_id, session_id, released_lease.lease_id)
    original_session_events = foundry.ontology_mcp.session_events

    def fail_session_events(*args: object, **kwargs: object) -> list[object]:
        raise ValidationFailed("forced event read failure")

    monkeypatch.setattr(foundry.ontology_mcp, "session_events", fail_session_events)
    failed_event_read = client.get(f"/mcp/ontology/{app_id}", headers=request_headers)
    monkeypatch.setattr(foundry.ontology_mcp, "session_events", original_session_events)
    guard_lease = foundry.ontology_mcp.claim_session_stream(stream_ctx, app_id, session_id)
    assert foundry.ontology_mcp.release_session_stream(stream_ctx, app_id, session_id, guard_lease.lease_id)
    response_class = ontology_mcp_router.StreamingResponse

    def fail_streaming_response(*args: object, **kwargs: object) -> object:
        raise RuntimeError("forced response setup failure")

    monkeypatch.setattr(ontology_mcp_router, "StreamingResponse", fail_streaming_response)
    failed_response_setup = TestClient(app, raise_server_exceptions=False).get(
        f"/mcp/ontology/{app_id}", headers=request_headers
    )
    monkeypatch.setattr(ontology_mcp_router, "StreamingResponse", response_class)
    response_guard_lease = foundry.ontology_mcp.claim_session_stream(stream_ctx, app_id, session_id)
    assert foundry.ontology_mcp.release_session_stream(stream_ctx, app_id, session_id, response_guard_lease.lease_id)
    resumed = client.get(
        f"/mcp/ontology/{app_id}",
        headers={**request_headers, "Last-Event-ID": f"{session_id}:1"},
    )
    caught_up = client.get(
        f"/mcp/ontology/{app_id}",
        headers={**request_headers, "Last-Event-ID": f"{session_id}:2"},
    )
    terminated = client.delete(f"/mcp/ontology/{app_id}", headers=request_headers)
    denied_reuse = client.post(
        f"/mcp/ontology/{app_id}",
        headers=request_headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    denied_stream_reuse = client.get(f"/mcp/ontology/{app_id}", headers=request_headers)

    assert f"id: {session_id}:2" in resumed.text
    assert concurrent_stream.status_code == 409
    assert concurrent_stream.json()["detail"]["code"] == "CONFLICT"
    assert concurrent_stream.json()["detail"]["details"]["resource"] == "mcp_session_stream"
    assert failed_event_read.status_code == 400
    assert failed_response_setup.status_code == 500
    assert accepted_notification.status_code == 202 and not accepted_notification.content
    assert ping.status_code == 200
    assert ping.json() == {"jsonrpc": "2.0", "id": "ping", "result": {}}
    assert rejected_notification.status_code == 400
    assert "jsonrpc" not in rejected_notification.text
    assert '"method": "notifications/session.ready"' in all_events.text
    assert '"method": "notifications/tool.completed"' in all_events.text
    assert "event: message" in resumed.text
    assert '"method": "notifications/tool.completed"' in resumed.text
    assert '"method": "notifications/message"' not in resumed.text
    assert "object.Order.get" in resumed.text
    assert "id:" not in caught_up.text
    assert ": heartbeat" in caught_up.text
    assert terminated.status_code == 204
    assert denied_reuse.status_code == 404
    assert denied_stream_reuse.status_code == 404
    assert denied_reuse.json()["detail"]["code"] == "NOT_FOUND"
    assert denied_reuse.json()["detail"]["details"]["isSessionTerminated"] is True


def test_ontology_mcp_rejects_foreign_session_namespaces_on_every_transport_method(
    foundry: Any,
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    _prepare_v3_demo(foundry, tmp_path)
    app_id = _mcp_application(foundry)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    headers = _user_mcp_headers(foundry, monkeypatch, app_id)
    client = TestClient(app)
    path = f"/mcp/ontology/{app_id}"

    for session_id in ("mcp-builder-foreign-0001", "mcp-release-foreign-0001"):
        foreign_headers = {**headers, "Mcp-Session-Id": session_id}
        responses = (
            client.post(
                path,
                headers=foreign_headers,
                json={"jsonrpc": "2.0", "id": session_id, "method": "tools/list", "params": {}},
            ),
            client.get(path, headers=foreign_headers),
            client.delete(path, headers=foreign_headers),
        )

        assert {response.status_code for response in responses} == {400}
        assert {response.json()["detail"]["details"]["reason"] for response in responses} == {
            "ontology_session_namespace_required"
        }


def test_ontology_mcp_session_transport_statuses_fail_closed(
    foundry: Any,
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    _prepare_v3_demo(foundry, tmp_path)
    app_id = _mcp_application(foundry)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    headers = _user_mcp_headers(foundry, monkeypatch, app_id)
    client = TestClient(app)
    initialized = client.post(
        f"/mcp/ontology/{app_id}",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _mcp_initialize_params()},
    )
    session_id = initialized.headers["Mcp-Session-Id"]
    second_initialized = client.post(
        f"/mcp/ontology/{app_id}",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": _mcp_initialize_params()},
    )
    missing_post = client.post(
        f"/mcp/ontology/{app_id}",
        headers=headers,
        json={"jsonrpc": "2.0", "id": "missing", "method": "tools/list", "params": {}},
    )
    missing_get = client.get(f"/mcp/ontology/{app_id}", headers=headers)
    missing_delete = client.delete(f"/mcp/ontology/{app_id}", headers=headers)
    wrong_owner = replace(
        MCP_USER,
        actor_user_id="another-mcp-user",
        request_id="ontology-mcp-wrong-owner",
    )
    wrong_owner_headers = {
        **_user_mcp_headers(foundry, monkeypatch, app_id, actor_ctx=wrong_owner, suffix="wrong-owner"),
        "Mcp-Session-Id": session_id,
    }
    wrong_owner_post = client.post(
        f"/mcp/ontology/{app_id}",
        headers=wrong_owner_headers,
        json={"jsonrpc": "2.0", "id": "wrong-owner", "method": "tools/list", "params": {}},
    )
    wrong_owner_get = client.get(f"/mcp/ontology/{app_id}", headers=wrong_owner_headers)
    wrong_owner_delete = client.delete(f"/mcp/ontology/{app_id}", headers=wrong_owner_headers)
    unknown_session = client.post(
        f"/mcp/ontology/{app_id}",
        headers={**headers, "Mcp-Session-Id": "ontology-mcp-unknown000"},
        json={"jsonrpc": "2.0", "id": "unknown", "method": "tools/list", "params": {}},
    )

    assert initialized.status_code == 200
    assert second_initialized.headers["Mcp-Session-Id"] != session_id
    assert {missing_post.status_code, missing_get.status_code, missing_delete.status_code} == {400}
    assert {
        missing_post.json()["detail"]["message"],
        missing_get.json()["detail"]["message"],
        missing_delete.json()["detail"]["message"],
    } == {"Ontology MCP request requires Mcp-Session-Id after initialize"}
    assert {wrong_owner_post.status_code, wrong_owner_get.status_code, wrong_owner_delete.status_code} == {404}
    assert unknown_session.status_code == 404
    assert wrong_owner_post.json()["detail"]["details"] == {"resource": "mcp_session"}
    assert unknown_session.json()["detail"]["details"] == {"resource": "mcp_session"}


def test_ontology_mcp_rejects_explicit_unsupported_protocol_version(
    foundry: Any,
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    _prepare_v3_demo(foundry, tmp_path)
    app_id = _mcp_application(foundry)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    headers = _user_mcp_headers(foundry, monkeypatch, app_id)

    response = TestClient(app).post(
        f"/mcp/ontology/{app_id}",
        headers={**headers, "MCP-Protocol-Version": "1900-01-01"},
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _mcp_initialize_params()},
    )
    client = TestClient(app)
    initialized = client.post(
        f"/mcp/ontology/{app_id}",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": "valid-version",
            "method": "initialize",
            "params": _mcp_initialize_params(),
        },
    )
    invalid_headers = {
        **headers,
        "Mcp-Session-Id": initialized.headers["Mcp-Session-Id"],
        "MCP-Protocol-Version": "1900-01-01",
    }
    invalid_get = client.get(f"/mcp/ontology/{app_id}", headers=invalid_headers)
    invalid_delete = client.delete(f"/mcp/ontology/{app_id}", headers=invalid_headers)

    assert response.status_code == 400
    assert response.json()["detail"]["details"] == {
        "receivedProtocolVersion": "1900-01-01",
        "supportedProtocolVersions": ["2025-06-18"],
    }
    assert {invalid_get.status_code, invalid_delete.status_code} == {400}
    assert invalid_get.json()["detail"]["details"] == response.json()["detail"]["details"]
    assert invalid_delete.json()["detail"]["details"] == response.json()["detail"]["details"]


def test_ontology_mcp_enforces_initialize_and_json_rpc_wire_lifecycle(
    foundry: Any,
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    _prepare_v3_demo(foundry, tmp_path)
    app_id = _mcp_application(foundry)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    headers = _user_mcp_headers(foundry, monkeypatch, app_id)
    client = TestClient(app)
    path = f"/mcp/ontology/{app_id}"

    invalid_params = client.post(
        path,
        headers=headers,
        json={"jsonrpc": "2.0", "id": "invalid-params", "method": "initialize", "params": {}},
    )
    null_id = client.post(
        path,
        headers=headers,
        json={"jsonrpc": "2.0", "id": None, "method": "initialize", "params": _mcp_initialize_params()},
    )
    missing_id = client.post(
        path,
        headers=headers,
        json={"jsonrpc": "2.0", "method": "initialize", "params": _mcp_initialize_params()},
    )
    negotiated = client.post(
        path,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": "negotiated",
            "method": "initialize",
            "params": _mcp_initialize_params("2099-01-01"),
        },
    )
    session_headers = {**headers, "Mcp-Session-Id": negotiated.headers["Mcp-Session-Id"]}
    reinitialized = client.post(
        path,
        headers=session_headers,
        json={"jsonrpc": "2.0", "id": "reinitialize", "method": "initialize", "params": _mcp_initialize_params()},
    )
    initialized = client.post(
        path,
        headers=session_headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
    )
    initialized_with_null_id = client.post(
        path,
        headers=session_headers,
        json={"jsonrpc": "2.0", "id": None, "method": "notifications/initialized", "params": {}},
    )
    unknown_request = client.post(
        path,
        headers=session_headers,
        json={"jsonrpc": "2.0", "id": "unknown", "method": "prompts/list", "params": {}},
    )
    unknown_notification = client.post(
        path,
        headers=session_headers,
        json={"jsonrpc": "2.0", "method": "notifications/unsupported", "params": {}},
    )
    invalid_cursor = client.post(
        path,
        headers=session_headers,
        json={"jsonrpc": "2.0", "id": "cursor", "method": "tools/list", "params": {"cursor": 7}},
    )
    complete_list = client.post(
        path,
        headers=session_headers,
        json={"jsonrpc": "2.0", "id": "complete", "method": "tools/list", "params": {}},
    )
    missing_protocol_headers = {
        key: value for key, value in session_headers.items() if key.lower() != "mcp-protocol-version"
    }
    missing_protocol = client.post(
        path,
        headers=missing_protocol_headers,
        json={"jsonrpc": "2.0", "id": "missing-version", "method": "ping", "params": {}},
    )
    overlong_session = client.post(
        path,
        headers={
            **session_headers,
            "Mcp-Session-Id": f"{session_headers['Mcp-Session-Id']}{'x' * 256}",
        },
        json={"jsonrpc": "2.0", "id": "overlong-session", "method": "ping", "params": {}},
    )

    assert invalid_params.json()["error"]["code"] == -32602
    assert null_id.json()["error"]["code"] == -32600
    assert missing_id.status_code == 400 and missing_id.content == b""
    assert all("Mcp-Session-Id" not in response.headers for response in (invalid_params, null_id, missing_id))
    assert negotiated.json()["result"]["protocolVersion"] == "2025-06-18"
    assert reinitialized.json()["error"]["code"] == -32600
    assert "Mcp-Session-Id" not in reinitialized.headers
    assert initialized.status_code == 202 and initialized.content == b""
    assert initialized_with_null_id.json()["error"]["code"] == -32600
    assert unknown_request.json()["error"]["code"] == -32601
    assert unknown_notification.status_code == 400 and unknown_notification.content == b""
    assert invalid_cursor.json()["error"]["code"] == -32602
    assert "nextCursor" not in complete_list.json()["result"]
    assert len(complete_list.json()["result"]["tools"]) > 0
    # A missing header resolves to the version this session negotiated; only a header that is
    # present and unsupported is a 400 (proved by the protocol-version test above).
    assert missing_protocol.status_code == 200
    assert missing_protocol.json()["result"] == {}
    assert overlong_session.status_code == 400
    assert overlong_session.json()["detail"]["details"] == {"resource": "mcp_session"}

    original_dispatch = ontology_mcp_router._dispatch

    def explode(*args: object, **kwargs: object) -> dict[str, object]:
        raise RuntimeError("sensitive downstream failure")

    monkeypatch.setattr(ontology_mcp_router, "_dispatch", explode)
    internal_request = client.post(
        path,
        headers=session_headers,
        json={"jsonrpc": "2.0", "id": "internal", "method": "ping", "params": {}},
    )
    internal_notification = client.post(
        path,
        headers=session_headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
    )
    monkeypatch.setattr(ontology_mcp_router, "_dispatch", original_dispatch)

    assert internal_request.json() == {
        "jsonrpc": "2.0",
        "id": "internal",
        "error": {
            "code": -32603,
            "message": "Internal error",
            "data": {"requestId": "ontology-mcp-default"},
        },
    }
    assert internal_notification.status_code == 500 and internal_notification.content == b""


def test_ontology_mcp_projects_effective_token_scopes_and_validates_advertised_schema(
    foundry: Any,
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    _prepare_v3_demo(foundry, tmp_path)
    app_id = _mcp_application(foundry)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    reduced_headers = _user_mcp_headers(
        foundry,
        monkeypatch,
        app_id,
        scopes=("osdk:object:Order:read", "osdk:action:ExpediteOrder:validate"),
        suffix="reduced-token",
    )
    initialized = client.post(
        f"/mcp/ontology/{app_id}",
        headers=reduced_headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _mcp_initialize_params()},
    )
    request_headers = {**reduced_headers, "Mcp-Session-Id": initialized.headers["Mcp-Session-Id"]}
    listed = client.post(
        f"/mcp/ontology/{app_id}",
        headers=request_headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    hidden_apply = client.post(
        f"/mcp/ontology/{app_id}",
        headers=request_headers,
        json={
            "jsonrpc": "2.0",
            "id": "hidden-apply",
            "method": "tools/call",
            "params": {"name": "action.ExpediteOrder.apply", "arguments": {}},
        },
    )
    invalid_plan = client.post(
        f"/mcp/ontology/{app_id}",
        headers=request_headers,
        json={
            "jsonrpc": "2.0",
            "id": "invalid-plan",
            "method": "tools/call",
            "params": {
                "name": "action.ExpediteOrder.plan",
                "arguments": {
                    "objectType": "Order",
                    "objectId": "O-1001",
                    "expectedObjectVersion": 1,
                    "params": {"mode": "urgent", "undeclared": True},
                },
            },
        },
    )
    invalid_target = client.post(
        f"/mcp/ontology/{app_id}",
        headers=request_headers,
        json={
            "jsonrpc": "2.0",
            "id": "invalid-target",
            "method": "tools/call",
            "params": {
                "name": "action.ExpediteOrder.plan",
                "arguments": {
                    "objectType": "Customer",
                    "objectId": "O-1001",
                    "expectedObjectVersion": 1,
                    "params": {"mode": "urgent"},
                },
            },
        },
    )

    assert {tool["name"] for tool in listed.json()["result"]["tools"]} == {
        "object.Order.get",
        "object.Order.search",
        "object.Order.unifiedSearch",
        "object.Order.links",
        "object.Order.searchAround",
        "action.ExpediteOrder.plan",
    }
    assert hidden_apply.json()["error"]["data"]["type"] == "VALIDATION_FAILED"
    assert invalid_plan.json()["error"]["data"] == {
        "type": "VALIDATION_FAILED",
        "path": "$.params",
        "schemaRule": "additionalProperties",
        "fields": ["undeclared"],
        "requestId": "ontology-mcp-reduced-token",
    }
    assert invalid_target.json()["error"]["data"] == {
        "type": "VALIDATION_FAILED",
        "path": "$.objectType",
        "schemaRule": "const",
        "requestId": "ontology-mcp-reduced-token",
    }


def test_ontology_mcp_accepts_pkce_bearer_and_preserves_application_restrictions(
    foundry: Any,
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    _prepare_v3_demo(foundry, tmp_path)
    app_id = _mcp_application(foundry)
    scopes = _mcp_scopes()
    foundry.developer_console.create_osdk_application_client(
        app_id,
        client_id="ontology-mcp-pkce-client",
        redirect_uris=("https://chat.example.test/oauth/callback",),
        allowed_scopes=scopes,
        idempotency_key="ontology-mcp-pkce-client",
        ctx=MCP_USER,
    )
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    verifier = "ontology-mcp-oauth-verifier"
    resource = _ontology_resource(app_id)
    authorized = client.get(
        "/api/auth/osdk/oauth/authorize",
        params={
            "client_id": "ontology-mcp-pkce-client",
            "redirect_uri": "https://chat.example.test/oauth/callback",
            "code_challenge": _s256(verifier),
            "scope": " ".join(scopes),
            "resource": resource,
        },
        headers=_principal_headers(),
    )
    token = client.post(
        "/api/auth/osdk/oauth/token",
        headers=_principal_headers(),
        json={
            "clientId": "ontology-mcp-pkce-client",
            "code": authorized.json()["code"],
            "redirectUri": "https://chat.example.test/oauth/callback",
            "codeVerifier": verifier,
            "resource": resource,
        },
    )
    issuer = foundry._services.osdk_oauth_sessions.oauth_token_issuer
    provider = JwtOidcAuthProvider(
        JwtOidcAuthConfig(issuer=issuer.issuer, audience=issuer.audience, jwks=issuer.public_jwks())
    )
    monkeypatch.setattr(api_runtime, "get_auth_provider", lambda: provider)
    bearer_headers = {
        "Authorization": f"Bearer {token.json()['accessToken']}",
        "MCP-Protocol-Version": "2025-06-18",
        "X-Request-ID": "mcp-pkce",
    }
    principal = provider.authenticate_for_audience(bearer_headers, resource)
    initialized = client.post(
        f"/mcp/ontology/{app_id}",
        headers=bearer_headers,
        json={
            "jsonrpc": "2.0",
            "id": "pkce-initialize",
            "method": "initialize",
            "params": _mcp_initialize_params(),
        },
    )
    listed = client.post(
        f"/mcp/ontology/{app_id}",
        headers={**bearer_headers, "Mcp-Session-Id": initialized.headers["Mcp-Session-Id"]},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )

    assert authorized.status_code == 200
    assert token.status_code == 200
    assert token.json()["access_token"] == token.json()["accessToken"]
    assert token.json()["token_type"] == token.json()["tokenType"]
    assert principal.application_id == app_id
    assert principal.client_id == "ontology-mcp-pkce-client"
    assert listed.status_code == 200
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    assert "object.Order.get" in names
    assert "object.Customer.get" not in names


def test_ontology_mcp_accepts_confidential_service_principal_with_app_scope_ceiling(
    foundry: Any,
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    _prepare_v3_demo(foundry, tmp_path)
    app_id = _mcp_application(foundry)
    scopes = _mcp_scopes()
    client_id = "ontology-mcp-service"
    machine_client = foundry.developer_console.create_osdk_application_client(
        app_id,
        client_id=client_id,
        redirect_uris=(),
        allowed_scopes=scopes,
        access_token_ttl_seconds=120,
        idempotency_key="ontology-mcp-service-client",
        ctx=MCP_USER,
    )
    rotated = foundry.developer_console.rotate_osdk_application_client_secret(
        app_id,
        str(machine_client["id"]),
        reason="Ontology MCP service principal",
        idempotency_key="ontology-mcp-service-secret",
        ctx=MCP_USER,
    )
    client_secret = str(rotated["clientSecret"])
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    original_auth_provider = api_runtime.get_auth_provider
    issuer = foundry._services.osdk_oauth_client_credentials.oauth_token_issuer
    provider = JwtOidcAuthProvider(
        JwtOidcAuthConfig(issuer=issuer.issuer, audience=issuer.audience, jwks=issuer.public_jwks())
    )
    monkeypatch.setattr(api_runtime, "get_auth_provider", lambda: provider)
    bootstrap_headers = {
        "X-Tenant-ID": MCP_USER.tenant_id,
        "X-Request-ID": "mcp-service-token-bootstrap",
    }

    metadata = client.get("/.well-known/oauth-authorization-server")
    denied = client.post(
        "/api/auth/osdk/oauth/token",
        headers=bootstrap_headers,
        json={
            "grantType": "client_credentials",
            "clientId": client_id,
            "clientSecret": "wrong-secret",
            "scope": " ".join(scopes),
        },
    )
    wrong_tenant = client.post(
        "/api/auth/osdk/oauth/token",
        headers={"X-Tenant-ID": "another-tenant"},
        json={
            "grantType": "client_credentials",
            "clientId": client_id,
            "clientSecret": client_secret,
            "scope": " ".join(scopes),
        },
    )
    conflicting_tenant = client.post(
        "/api/auth/osdk/oauth/token",
        headers={"X-Tenant-ID": MCP_USER.tenant_id},
        json={
            "grantType": "client_credentials",
            "clientId": client_id,
            "clientSecret": client_secret,
            "tenantId": "another-tenant",
            "scope": " ".join(scopes),
        },
    )
    missing_tenant = client.post(
        "/api/auth/osdk/oauth/token",
        json={
            "grantType": "client_credentials",
            "clientId": client_id,
            "clientSecret": client_secret,
            "scope": " ".join(scopes),
        },
    )
    token = client.post(
        "/api/auth/osdk/oauth/token",
        headers=bootstrap_headers,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": " ".join(scopes),
            "resource": _ontology_resource(app_id),
        },
    )
    principal = provider.authenticate_for_audience(
        {"Authorization": f"Bearer {token.json()['accessToken']}"}, _ontology_resource(app_id)
    )
    bearer_headers = {
        "Authorization": f"Bearer {token.json()['accessToken']}",
        "MCP-Protocol-Version": "2025-06-18",
        "X-Request-ID": "mcp-service",
    }
    initialized = client.post(
        f"/mcp/ontology/{app_id}",
        headers=bearer_headers,
        json={
            "jsonrpc": "2.0",
            "id": "service-initialize",
            "method": "initialize",
            "params": _mcp_initialize_params(),
        },
    )
    listed = client.post(
        f"/mcp/ontology/{app_id}",
        headers={**bearer_headers, "Mcp-Session-Id": initialized.headers["Mcp-Session-Id"]},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    current = foundry.objects.get("Order", "O-1002", ctx=MCP_USER)
    approval = client.post(
        f"/mcp/ontology/{app_id}",
        headers={
            "Authorization": f"Bearer {token.json()['accessToken']}",
            "MCP-Protocol-Version": "2025-06-18",
            "Mcp-Session-Id": listed.headers["Mcp-Session-Id"],
            "X-Request-ID": "mcp-service-high-risk",
        },
        json={
            "jsonrpc": "2.0",
            "id": "mcp-service-high-risk",
            "method": "tools/call",
            "params": {
                "name": "action.ApproveOrder.apply",
                "arguments": {
                    "objectType": "Order",
                    "objectId": "O-1002",
                    "expectedObjectVersion": current["objectVersion"],
                    "params": {"reason": "Machine request requiring human approval"},
                },
            },
        },
    )
    approval_result = approval.json()["result"]["structuredContent"]
    pending_status = _call(
        client,
        app_id,
        {**bearer_headers, "Mcp-Session-Id": listed.headers["Mcp-Session-Id"]},
        rpc_id="mcp-service-approval-status",
        name="action_approval.get",
        arguments={"reviewId": approval_result["reviewId"]},
    )
    with pytest.raises(NotFound):
        foundry._services.action_proposal.external_mcp_status(
            principal, application_id="another-application", review_id=str(approval_result["reviewId"])
        )
    with pytest.raises(PermissionDenied, match="token scope denied"):
        foundry._services.action_proposal.external_mcp_status(
            replace(principal, token_scopes=("osdk:object:Order:read",)),
            application_id=app_id,
            review_id=str(approval_result["reviewId"]),
        )
    foundry.insights.decide(
        str(approval_result["reviewId"]),
        decision="approved",
        idempotency_key="mcp-service-human-approve",
        ctx=MCP_REVIEWER,
    )
    executed = foundry.aip.execute_approved_action(
        review_id=str(approval_result["reviewId"]),
        expected_proposal_fingerprint=str(approval_result["proposalFingerprint"]),
        idempotency_key="mcp-service-human-execute",
        ctx=MCP_REVIEWER,
    )
    approved_run = _call(
        client,
        app_id,
        {**bearer_headers, "Mcp-Session-Id": listed.headers["Mcp-Session-Id"]},
        rpc_id="mcp-service-approved-run",
        name="action_run.get",
        arguments={"runId": executed.action_run_id},
    )
    other_client_id, other_secret = _machine_client(
        foundry,
        app_id,
        "ontology-mcp-other-service",
        scopes,
    )
    other_token = _client_credentials_token(
        client,
        other_client_id,
        other_secret,
        scopes,
        resource=_ontology_resource(app_id),
    )
    other_headers = {
        "Authorization": f"Bearer {other_token['accessToken']}",
        "MCP-Protocol-Version": "2025-06-18",
        "X-Request-ID": "mcp-other-service-run-read",
    }
    other_initialized = client.post(
        f"/mcp/ontology/{app_id}",
        headers=other_headers,
        json={
            "jsonrpc": "2.0",
            "id": "other-service-initialize",
            "method": "initialize",
            "params": _mcp_initialize_params(),
        },
    )
    other_run = client.post(
        f"/mcp/ontology/{app_id}",
        headers={**other_headers, "Mcp-Session-Id": other_initialized.headers["Mcp-Session-Id"]},
        json={
            "jsonrpc": "2.0",
            "id": "other-service-run-read",
            "method": "tools/call",
            "params": {
                "name": "action_run.get",
                "arguments": {"runId": executed.action_run_id},
            },
        },
    )
    other_result = other_run.json()["result"]
    assert approved_run["actionRunId"] == executed.action_run_id
    assert approved_run["status"] == "succeeded"
    assert other_result["isError"] is True
    assert other_result["structuredContent"]["error"]["type"] == "NOT_FOUND"
    monkeypatch.setattr(api_runtime, "get_auth_provider", original_auth_provider)
    replayed_rotation = client.post(
        f"/api/developer-console/osdk-applications/{app_id}/clients/{machine_client['id']}/secrets/rotate",
        headers={**_principal_headers(), "Idempotency-Key": "ontology-mcp-service-secret"},
        json={"reason": "Ontology MCP service principal"},
    )
    second_rotation = client.post(
        f"/api/developer-console/osdk-applications/{app_id}/clients/{machine_client['id']}/secrets/rotate",
        headers={**_principal_headers(), "Idempotency-Key": "ontology-mcp-service-secret-2"},
        json={"reason": "scheduled rotation"},
    )
    second_secret = second_rotation.json()["clientSecret"]
    monkeypatch.setattr(api_runtime, "get_auth_provider", lambda: provider)
    rotated_old_access_denied = client.post(
        f"/mcp/ontology/{app_id}",
        headers={"Authorization": f"Bearer {token.json()['accessToken']}", "X-Request-ID": "mcp-old-access"},
        json={
            "jsonrpc": "2.0",
            "id": "old-access",
            "method": "initialize",
            "params": _mcp_initialize_params(),
        },
    )
    monkeypatch.setattr(api_runtime, "get_auth_provider", original_auth_provider)
    old_secret_denied = client.post(
        "/api/auth/osdk/oauth/token",
        headers=_principal_headers(),
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": " ".join(scopes),
        },
    )
    rotated_token = client.post(
        "/api/auth/osdk/oauth/token",
        headers=_principal_headers(),
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": second_secret,
            "scope": " ".join(scopes),
            "resource": _ontology_resource(app_id),
        },
    )
    history = client.get(
        f"/api/developer-console/osdk-applications/{app_id}/clients/{machine_client['id']}/secrets",
        headers=_principal_headers(),
    )
    revoked = client.post(
        f"/api/developer-console/osdk-applications/{app_id}/clients/{machine_client['id']}/secrets/revoke",
        headers={**_principal_headers(), "Idempotency-Key": "ontology-mcp-service-revoke"},
    )
    monkeypatch.setattr(api_runtime, "get_auth_provider", lambda: provider)
    revoked_access_denied = client.post(
        f"/mcp/ontology/{app_id}",
        headers={
            "Authorization": f"Bearer {rotated_token.json()['accessToken']}",
            "X-Request-ID": "mcp-revoked-access",
        },
        json={
            "jsonrpc": "2.0",
            "id": "revoked-access",
            "method": "initialize",
            "params": _mcp_initialize_params(),
        },
    )
    monkeypatch.setattr(api_runtime, "get_auth_provider", original_auth_provider)
    revoked_secret_denied = client.post(
        "/api/auth/osdk/oauth/token",
        headers=_principal_headers(),
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": second_secret,
            "scope": " ".join(scopes),
        },
    )

    assert "client_credentials" in metadata.json()["grant_types_supported"]
    assert "client_secret_post" in metadata.json()["token_endpoint_auth_methods_supported"]
    assert denied.status_code == 403
    assert "invalid" in denied.text
    assert client_secret not in denied.text
    assert wrong_tenant.status_code == 403
    assert client_secret not in wrong_tenant.text
    assert conflicting_tenant.status_code == 400
    assert missing_tenant.status_code == 400
    assert token.status_code == 200
    assert token.json()["access_token"] == token.json()["accessToken"]
    assert token.json()["expires_in"] == token.json()["expiresIn"]
    assert token.json()["grantType"] == "client_credentials"
    assert "refreshToken" not in token.json()
    assert principal.actor_user_id == f"service-principal:{client_id}"
    assert principal.application_id == app_id
    assert set(principal.token_scopes) == set(scopes)
    assert principal.oauth_session_id == token.json()["sessionId"]
    assert principal.roles == ("osdk_service_principal",)
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    assert "object.Order.get" in names
    assert "action.ExpediteOrder.apply" in names
    assert "object.Customer.get" not in names
    assert approval.status_code == 200
    assert approval_result["status"] == "approval_required"
    assert pending_status["status"] == "approval_pending"
    assert replayed_rotation.status_code == 200
    assert replayed_rotation.json()["clientSecret"] is None
    assert replayed_rotation.json()["isReplayed"] is True
    assert second_rotation.status_code == 200
    assert second_rotation.json()["revokedSessionCount"] == 1
    assert rotated_old_access_denied.status_code == 401
    assert rotated_old_access_denied.json()["detail"]["code"] == "PERMISSION_DENIED"
    assert old_secret_denied.status_code == 403
    assert rotated_token.status_code == 200
    assert [item["status"] for item in history.json()] == ["active", "rotated"]
    assert client_secret not in history.text and second_secret not in history.text
    assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"
    assert revoked.json()["revokedSessionCount"] == 1
    assert revoked_access_denied.status_code == 401
    assert revoked_access_denied.json()["detail"]["code"] == "PERMISSION_DENIED"
    assert revoked_secret_denied.status_code == 403


def test_client_credentials_use_narrow_mcp_action_and_function_entrypoints(
    foundry: Any,
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    _prepare_v3_function_demo(foundry, tmp_path)
    resources = (
        _resource("object", "Order", "read"),
        _resource("action", "ExpediteOrder", "validate", "execute"),
        _resource("function", "echoInputs", "execute"),
    )
    app_id = _service_mcp_application(foundry, "NarrowMachineMcp", resources)
    scopes = tuple(scope for resource in resources for scope in resource["scopes"])
    client_id, client_secret = _machine_client(foundry, app_id, "narrow-machine", scopes)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    provider = _strict_service_provider(foundry)
    monkeypatch.setattr(api_runtime, "get_auth_provider", lambda: provider)
    token = _client_credentials_token(client, client_id, client_secret, scopes, resource=_ontology_resource(app_id))
    bearer = {
        "Authorization": f"Bearer {token['accessToken']}",
        "MCP-Protocol-Version": "2025-06-18",
        "X-Request-ID": "narrow-machine",
    }
    principal = provider.authenticate_for_audience(bearer, _ontology_resource(app_id))
    initialized = client.post(
        f"/mcp/ontology/{app_id}",
        headers=bearer,
        json={
            "jsonrpc": "2.0",
            "id": "narrow-init",
            "method": "initialize",
            "params": _mcp_initialize_params(),
        },
    )
    mcp_headers = {**bearer, "Mcp-Session-Id": initialized.headers["Mcp-Session-Id"]}
    listed = client.post(
        f"/mcp/ontology/{app_id}",
        headers=mcp_headers,
        json={"jsonrpc": "2.0", "id": "narrow-list", "method": "tools/list", "params": {}},
    )
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    current = foundry.objects.get("Order", "O-1001", ctx=MCP_USER)
    object_result = _call(
        client,
        app_id,
        mcp_headers,
        rpc_id="narrow-object",
        name="object.Order.get",
        arguments={"objectId": "O-1001"},
    )
    other_object = client.post(
        f"/mcp/ontology/{app_id}",
        headers=mcp_headers,
        json={
            "jsonrpc": "2.0",
            "id": "narrow-other-object",
            "method": "tools/call",
            "params": {"name": "object.Customer.get", "arguments": {"objectId": "C-1001"}},
        },
    )
    action_args = {
        "objectType": "Order",
        "objectId": "O-1001",
        "expectedObjectVersion": current["objectVersion"],
        "params": {"mode": "urgent"},
    }
    planned = _call(
        client,
        app_id,
        mcp_headers,
        rpc_id="narrow-plan",
        name="action.ExpediteOrder.plan",
        arguments=action_args,
    )
    function_result = _call(
        client,
        app_id,
        mcp_headers,
        rpc_id="narrow-function",
        name="function.echoInputs.execute",
        arguments={"inputs": {"note": "hello from MCP"}},
    )
    applied = _call(
        client,
        app_id,
        mcp_headers,
        rpc_id="narrow-apply",
        name="action.ExpediteOrder.apply",
        arguments=action_args,
    )
    run = _call(
        client,
        app_id,
        mcp_headers,
        rpc_id="narrow-run",
        name="action_run.get",
        arguments={"runId": applied["actionRunId"]},
    )
    action_only_token = _client_credentials_token(
        client,
        client_id,
        client_secret,
        ("osdk:action:ExpediteOrder:execute",),
        resource=_ontology_resource(app_id),
    )
    action_only_principal = provider.authenticate_for_audience(
        {"Authorization": f"Bearer {action_only_token['accessToken']}"},
        _ontology_resource(app_id),
    )
    with pytest.raises(PermissionDenied, match="token scope denied"):
        foundry.ontology_mcp.actions.get_external_mcp_run(
            str(applied["actionRunId"]),
            ctx=action_only_principal,
        )
    generic_plan = client.post(
        "/api/actions/ExpediteOrder/plan",
        headers=bearer,
        json={
            "target": {"objectType": "Order", "objectId": "O-1001"},
            "expectedObjectVersion": current["objectVersion"],
            "params": {"mode": "urgent"},
        },
    )
    generic_function = client.post(
        "/api/functions/echoInputs/execute", headers=bearer, json={"inputs": {"note": "blocked"}}
    )
    generic_operations = client.get("/api/operations/runs", headers=bearer)
    generic_datasets = client.get("/api/datasets", headers=bearer)
    generic_ontology = client.get("/api/ontology/catalog", headers=bearer)
    generic_pipelines = client.get("/api/pipelines/node-types", headers=bearer)

    assert principal.roles == ("osdk_service_principal",)
    assert names == {
        "object.Order.get",
        "object.Order.search",
        "object.Order.unifiedSearch",
        "object.Order.links",
        "object.Order.searchAround",
        "action.ExpediteOrder.plan",
        "action.ExpediteOrder.apply",
        "function.echoInputs.execute",
        "action_run.get",
        "action_approval.get",
    }
    assert planned["approval"]["canAgentExecuteAutonomously"] is True
    assert object_result["objectId"] == "O-1001"
    assert other_object.json()["error"]["data"]["type"] == "VALIDATION_FAILED"
    assert function_result["status"] == "succeeded"
    assert function_result["output"] == {"value": {"note": "hello from MCP"}}
    assert applied["status"] in {"queued", "running", "succeeded"}
    assert run["actionRunId"] == applied["actionRunId"]
    assert run["status"] != "failed"
    assert generic_plan.status_code == 403
    assert generic_function.status_code == 403
    assert generic_operations.status_code == 403
    assert generic_datasets.status_code == 403
    assert generic_ontology.status_code == 403
    assert generic_pipelines.status_code == 403


def test_validate_only_service_principal_plan_is_online_and_fail_closed(
    foundry: Any,
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    _prepare_v3_demo(foundry, tmp_path)
    resources = (
        _resource("object", "Order", "read"),
        _resource("action", "ExpediteOrder", "validate", "execute"),
    )
    app_id = _service_mcp_application(foundry, "ValidateOnlyMcp", resources)
    allowed_scopes = tuple(scope for resource in resources for scope in resource["scopes"])
    client_id, client_secret = _machine_client(foundry, app_id, "validate-only-machine", allowed_scopes)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    provider = _strict_service_provider(foundry)
    monkeypatch.setattr(api_runtime, "get_auth_provider", lambda: provider)
    fake_scope = client.post(
        "/api/auth/osdk/oauth/token",
        headers={"X-Tenant-ID": MCP_USER.tenant_id},
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "osdk:action:Ghost:validate",
        },
    )
    action_only_scope = ("osdk:action:ExpediteOrder:validate",)
    action_only_token = _client_credentials_token(
        client, client_id, client_secret, action_only_scope, resource=_ontology_resource(app_id)
    )
    action_only_headers = {
        "Authorization": f"Bearer {action_only_token['accessToken']}",
        "MCP-Protocol-Version": "2025-06-18",
        "X-Request-ID": "validate-without-target-read",
    }
    action_only_init = client.post(
        f"/mcp/ontology/{app_id}",
        headers=action_only_headers,
        json={
            "jsonrpc": "2.0",
            "id": "action-only-init",
            "method": "initialize",
            "params": _mcp_initialize_params(),
        },
    )
    action_only_list = client.post(
        f"/mcp/ontology/{app_id}",
        headers={**action_only_headers, "Mcp-Session-Id": action_only_init.headers["Mcp-Session-Id"]},
        json={"jsonrpc": "2.0", "id": "action-only-list", "method": "tools/list", "params": {}},
    )
    validate_scope = ("osdk:object:Order:read", "osdk:action:ExpediteOrder:validate")
    token = _client_credentials_token(
        client, client_id, client_secret, validate_scope, resource=_ontology_resource(app_id)
    )
    bearer = {
        "Authorization": f"Bearer {token['accessToken']}",
        "MCP-Protocol-Version": "2025-06-18",
        "X-Request-ID": "validate-only",
    }
    principal = provider.authenticate_for_audience(bearer, _ontology_resource(app_id))
    initialized = client.post(
        f"/mcp/ontology/{app_id}",
        headers=bearer,
        json={
            "jsonrpc": "2.0",
            "id": "validate-init",
            "method": "initialize",
            "params": _mcp_initialize_params(),
        },
    )
    mcp_headers = {**bearer, "Mcp-Session-Id": initialized.headers["Mcp-Session-Id"]}
    listed = client.post(
        f"/mcp/ontology/{app_id}",
        headers=mcp_headers,
        json={"jsonrpc": "2.0", "id": "validate-list", "method": "tools/list", "params": {}},
    )
    current = foundry.objects.get("Order", "O-1001", ctx=MCP_USER)
    action_args = {
        "objectType": "Order",
        "objectId": "O-1001",
        "expectedObjectVersion": current["objectVersion"],
        "params": {"mode": "standard"},
    }
    planned = _call(
        client,
        app_id,
        mcp_headers,
        rpc_id="validate-plan",
        name="action.ExpediteOrder.plan",
        arguments=action_args,
    )
    fake_context = RequestContext(
        tenant_id=MCP_USER.tenant_id,
        actor_user_id=f"service-principal:{client_id}",
        roles=("osdk_service_principal",),
        application_id=app_id,
        client_id=client_id,
        token_scopes=validate_scope,
        oauth_session_id="missing-session",
    )
    with pytest.raises(PermissionDenied, match="access session is inactive"):
        foundry.ontology_mcp.actions.plan_external_mcp("ExpediteOrder", **_action_kwargs(action_args), ctx=fake_context)
    foundry.developer_console.update_osdk_application_resources(
        app_id,
        resources=(_resource("object", "Order", "read"),),
        idempotency_key="validate-only-revoke-action",
        ctx=MCP_USER,
    )
    revoked_list = client.post(
        f"/mcp/ontology/{app_id}",
        headers=mcp_headers,
        json={"jsonrpc": "2.0", "id": "revoked-list", "method": "tools/list", "params": {}},
    )
    revoked_call = client.post(
        f"/mcp/ontology/{app_id}",
        headers=mcp_headers,
        json={
            "jsonrpc": "2.0",
            "id": "revoked-plan",
            "method": "tools/call",
            "params": {"name": "action.ExpediteOrder.plan", "arguments": action_args},
        },
    )

    assert fake_scope.status_code == 403
    assert principal.roles == ("osdk_service_principal",)
    assert principal.token_scopes == validate_scope
    assert action_only_list.json()["result"]["tools"] == []
    assert {tool["name"] for tool in listed.json()["result"]["tools"]} == {
        "object.Order.get",
        "object.Order.search",
        "object.Order.unifiedSearch",
        "object.Order.links",
        "object.Order.searchAround",
        "action.ExpediteOrder.plan",
    }
    assert planned["actionApiName"] == "ExpediteOrder"
    assert planned["authorization"]["roles"] == ["osdk_service_principal"]
    assert planned["authorization"]["effectivePolicyRoles"] == ["viewer"]
    assert {tool["name"] for tool in revoked_list.json()["result"]["tools"]} == {
        "object.Order.get",
        "object.Order.search",
        "object.Order.unifiedSearch",
        "object.Order.links",
        "object.Order.searchAround",
    }
    assert revoked_call.json()["error"]["data"]["type"] == "VALIDATION_FAILED"


def test_resource_grant_change_notifies_live_sessions_that_the_tool_list_changed(
    foundry: Any,
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    """Regression: editing grants silently invalidated every connected client's catalog.

    Consumer MCP projects granted resources into `tools/list`, so a Developer Console edit
    changes the catalog mid-session. The server declared `listChanged: false`, so clients
    never refetched and kept calling names that dispatch now rejects as "not available".
    """

    _prepare_v3_demo(foundry, tmp_path)
    app_id = _mcp_application(foundry)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    headers = _user_mcp_headers(foundry, monkeypatch, app_id)
    client = TestClient(app)

    initialized = client.post(
        f"/mcp/ontology/{app_id}",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _mcp_initialize_params()},
    )
    session_id = initialized.headers["Mcp-Session-Id"]
    session_headers = {**headers, "Mcp-Session-Id": session_id, "MCP-Protocol-Version": "2025-06-18"}

    foundry.developer_console.update_osdk_application_resources(
        app_id,
        resources=[_resource("object", "Order", "read")],
        idempotency_key="ontology-mcp-grant-change",
        ctx=MCP_USER,
    )
    after_change = client.get(f"/mcp/ontology/{app_id}", headers=session_headers)

    # The same edit applied twice is not a catalog change, so it must not re-notify.
    foundry.developer_console.update_osdk_application_resources(
        app_id,
        resources=[_resource("object", "Order", "read")],
        idempotency_key="ontology-mcp-grant-change-repeat",
        ctx=MCP_USER,
    )
    after_noop = client.get(f"/mcp/ontology/{app_id}", headers=session_headers)

    assert initialized.json()["result"]["capabilities"]["tools"]["listChanged"] is True
    assert after_change.text.count('"method": "notifications/tools/list_changed"') == 1
    assert after_noop.text.count('"method": "notifications/tools/list_changed"') == 1


def test_protected_resource_metadata_is_not_found_when_the_plane_has_no_scope(
    foundry: Any,
    monkeypatch: Any,
) -> None:
    """A connector-only application has no Ontology scope, so its metadata must 404, not 500."""

    monkeypatch.setattr(api_runtime, "foundry", foundry)
    app_id = _service_mcp_application(
        foundry,
        "ConnectorOnlyMcp",
        (_resource("connector", "fde_exploration", "execute"),),
    )
    client = TestClient(app)

    ontology_metadata = client.get(f"/.well-known/oauth-protected-resource/mcp/ontology/{app_id}")
    builder_metadata = client.get(f"/.well-known/oauth-protected-resource/mcp/builder/{app_id}")

    assert ontology_metadata.status_code == 404
    assert ontology_metadata.json()["detail"]["code"] == "NOT_FOUND"
    assert builder_metadata.status_code == 200
    assert builder_metadata.json()["scopes_supported"] == ["osdk:connector:fde_exploration:execute"]


def _mcp_initialize_params(protocol_version: str = "2025-06-18") -> dict[str, object]:
    return {
        "protocolVersion": protocol_version,
        "capabilities": {},
        "clientInfo": {"name": "foundry-lite-ontology-test", "version": "1.0.0"},
    }


def _mcp_application(foundry: Any) -> str:
    resources = []
    for resource_type, name, operations in (
        ("object", "Order", ("read",)),
        # Traversal is a separate grant from reading Order itself.
        ("link", "OrderCustomer", ("read",)),
        ("action", "ExpediteOrder", ("validate", "execute")),
        ("action", "ApproveOrder", ("validate", "execute")),
    ):
        resource_scopes = [f"osdk:{resource_type}:{name}:{operation}" for operation in operations]
        resources.append({"resourceType": resource_type, "resourceApiName": name, "scopes": resource_scopes})
    created = foundry.developer_console.create_osdk_application(
        app_api_name="OntologyMcpDining",
        display_name="Ontology MCP Dining",
        client_id="ontology-mcp-client",
        resources=resources,
        idempotency_key="ontology-mcp-application",
        ctx=MCP_USER,
    )
    app_id = str(created["application"]["id"])
    foundry.developer_console.configure_ontology_mcp_server(
        app_id,
        status="enabled",
        description_markdown="Order operations for the external dining agent.",
        allowed_origins=("https://chat.example.test",),
        idempotency_key="ontology-mcp-server-enable",
        ctx=MCP_USER,
    )
    return app_id


def _mcp_scopes() -> tuple[str, ...]:
    return (
        "osdk:object:Order:read",
        "osdk:action:ExpediteOrder:validate",
        "osdk:action:ExpediteOrder:execute",
        "osdk:action:ApproveOrder:validate",
        "osdk:action:ApproveOrder:execute",
    )


def _user_mcp_headers(
    foundry: Any,
    monkeypatch: Any,
    app_id: str,
    *,
    scopes: tuple[str, ...] | None = None,
    actor_ctx: RequestContext = MCP_USER,
    suffix: str = "default",
    is_default_scope_included: bool = True,
) -> dict[str, str]:
    requested_scopes = scopes or _mcp_scopes()
    default_scopes = _mcp_scopes() if is_default_scope_included else ()
    client_id = f"ontology-mcp-user-{suffix}"
    redirect_uri = f"https://chat.example.test/oauth/{suffix}"
    verifier = f"foundry-lite-ontology-user-{suffix}-verifier"
    foundry.developer_console.create_osdk_application_client(
        app_id,
        client_id=client_id,
        redirect_uris=(redirect_uri,),
        allowed_scopes=tuple({*default_scopes, *requested_scopes}),
        idempotency_key=f"{client_id}-public-client",
        ctx=MCP_USER,
    )
    authorization = foundry.auth.osdk_oauth_authorize(
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=_s256(verifier),
        scopes=requested_scopes,
        resource=_ontology_resource(app_id),
        resource_application_id=app_id,
        ctx=actor_ctx,
    )
    token = foundry.auth.osdk_oauth_token(
        client_id=client_id,
        code=str(authorization["code"]),
        redirect_uri=redirect_uri,
        code_verifier=verifier,
        resource=_ontology_resource(app_id),
        resource_application_id=app_id,
        ctx=actor_ctx,
    )
    issuer = foundry._services.osdk_oauth_sessions.oauth_token_issuer
    provider = JwtOidcAuthProvider(
        JwtOidcAuthConfig(issuer=issuer.issuer, audience=issuer.audience, jwks=issuer.public_jwks())
    )
    monkeypatch.setattr(api_runtime, "get_auth_provider", lambda: provider)
    return {
        "Authorization": f"Bearer {token['accessToken']}",
        "MCP-Protocol-Version": "2025-06-18",
        "X-Request-ID": f"ontology-mcp-{suffix}",
    }


def _pilot_business_arguments() -> dict[str, object]:
    return {
        "applicationName": "현장 업무 도우미",
        "domainDescription": "접수된 현장 업무를 담당자가 확인하고 완료 증거를 남길 때까지 한곳에서 처리합니다.",
        "domainBrief": {
            "actors": ["ops_manager"],
            "records": [
                {
                    "name": "현장 업무",
                    "apiName": "WorkItem",
                    "fields": [{"name": "우선순위", "apiName": "priority", "type": "string", "required": True}],
                }
            ],
            "lifecycleStates": ["NEW", "DONE"],
            "actions": [
                {
                    "name": "업무 완료",
                    "apiName": "CompleteWorkItem",
                    "fromStates": ["NEW"],
                    "toState": "DONE",
                    "requiredInformation": ["completionNote"],
                    "allowedActors": ["ops_manager"],
                    "requiresApproval": True,
                }
            ],
            "policies": [
                {
                    "name": "완료 확인",
                    "statement": "완료 전 담당자가 처리 내용을 확인합니다.",
                    "enforcement": "manual_review",
                    "appliesToActions": ["CompleteWorkItem"],
                    "evidence": "담당자와 완료 시각",
                }
            ],
            "evidence": ["상태 변경 전후", "담당자", "완료 메모"],
            "integrations": [],
            "successMeasures": ["미처리 누락 0건"],
        },
    }


def _human_control_headers(foundry: Any) -> dict[str, str]:
    issuer = foundry._services.osdk_oauth_sessions.oauth_token_issuer
    token = issuer.issue_access_token(
        {
            "tenant_id": MCP_USER.tenant_id,
            "actor_user_id": MCP_USER.actor_user_id,
            "roles": list(MCP_USER.roles),
            "application_id": "",
            "client_id": "",
            "scopes": [],
            "session_id": "ontology-mcp-human-control",
        },
        ttl_seconds=300,
    )
    return {
        "Authorization": f"Bearer {token['accessToken']}",
        "X-Request-ID": "ontology-mcp-human-control",
    }


def _call(
    client: TestClient,
    app_id: str,
    headers: dict[str, str],
    *,
    rpc_id: str,
    name: str,
    arguments: dict[str, object],
) -> dict[str, object]:
    response = client.post(
        f"/mcp/ontology/{app_id}",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert "error" not in payload, payload
    return dict(payload["result"]["structuredContent"])


def _principal_headers() -> dict[str, str]:
    return {
        "X-Tenant-ID": MCP_USER.tenant_id,
        "X-User-ID": MCP_USER.actor_user_id,
        "X-Roles": ",".join(MCP_USER.roles),
        "X-Request-ID": "ontology-mcp-oauth-setup",
    }


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _prepare_v3_function_demo(foundry: Any, tmp_path: Any) -> None:
    payload = yaml.safe_load(_v3_ontology(tmp_path).read_text(encoding="utf-8"))
    payload["functionTypes"].append(
        {
            "apiName": "echoInputs",
            "displayName": "Echo inputs",
            "version": "v1",
            "runtime": "logic_dag",
            "inputs": [{"apiName": "note", "type": "string", "required": True}],
            "output": {"type": "string"},
            "permissions": {"allowedRoles": ["ops_manager"]},
            "definition": {
                "blocks": [
                    {"blockId": "input", "kind": "Input"},
                    {
                        "blockId": "output",
                        "kind": "Output",
                        "dependsOn": ["input"],
                        "inputs": {"fromBlock": "input"},
                    },
                ]
            },
        }
    )
    ontology_path = tmp_path / "order-customer-v3-function.yaml"
    ontology_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    _prepare_demo(foundry, ontology_path)


def _resource(resource_type: str, name: str, *operations: str) -> dict[str, Any]:
    return {
        "resourceType": resource_type,
        "resourceApiName": name,
        "scopes": [f"osdk:{resource_type}:{name}:{operation}" for operation in operations],
    }


def _service_mcp_application(foundry: Any, app_api_name: str, resources: tuple[dict[str, Any], ...]) -> str:
    created = foundry.developer_console.create_osdk_application(
        app_api_name=app_api_name,
        display_name=app_api_name,
        client_id=f"{app_api_name}-bootstrap",
        resources=resources,
        idempotency_key=f"{app_api_name}-create",
        ctx=MCP_USER,
    )
    app_id = str(created["application"]["id"])
    foundry.developer_console.configure_ontology_mcp_server(
        app_id,
        status="enabled",
        description_markdown=f"{app_api_name} test server",
        allowed_origins=("https://chat.example.test",),
        idempotency_key=f"{app_api_name}-enable",
        ctx=MCP_USER,
    )
    return app_id


def _machine_client(foundry: Any, app_id: str, client_id: str, scopes: tuple[str, ...]) -> tuple[str, str]:
    client = foundry.developer_console.create_osdk_application_client(
        app_id,
        client_id=client_id,
        redirect_uris=(),
        allowed_scopes=scopes,
        access_token_ttl_seconds=120,
        idempotency_key=f"{client_id}-create",
        ctx=MCP_USER,
    )
    rotated = foundry.developer_console.rotate_osdk_application_client_secret(
        app_id,
        str(client["id"]),
        reason="Ontology MCP integration test",
        idempotency_key=f"{client_id}-secret",
        ctx=MCP_USER,
    )
    return client_id, str(rotated["clientSecret"])


def _strict_service_provider(foundry: Any) -> JwtOidcAuthProvider:
    issuer = foundry._services.osdk_oauth_client_credentials.oauth_token_issuer
    return JwtOidcAuthProvider(
        JwtOidcAuthConfig(issuer=issuer.issuer, audience=issuer.audience, jwks=issuer.public_jwks())
    )


def _client_credentials_token(
    client: TestClient,
    client_id: str,
    client_secret: str,
    scopes: tuple[str, ...],
    *,
    resource: str | None = None,
) -> dict[str, Any]:
    payload = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": " ".join(scopes),
    }
    if resource is not None:
        payload["resource"] = resource
    response = client.post(
        "/api/auth/osdk/oauth/token",
        headers={"X-Tenant-ID": MCP_USER.tenant_id},
        data=payload,
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _ontology_resource(app_id: str) -> str:
    return f"http://testserver/mcp/ontology/{app_id}"


def _action_kwargs(arguments: dict[str, object]) -> dict[str, object]:
    return {
        "object_type": arguments["objectType"],
        "object_id": arguments["objectId"],
        "expected_object_version": arguments["expectedObjectVersion"],
        "params": arguments["params"],
    }


def test_ontology_mcp_link_traversal_needs_its_own_grant_beyond_object_read(
    foundry: Any,
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    """Reaching a neighbour is a separate grant from reading the object you started at.

    Search and filters can only ask about an object's own columns, so without traversal an
    external agent cannot answer "what is this connected to" at all. Exposing it re-uses the
    link's read scope rather than riding on `object:read`, otherwise granting one object type
    would silently hand over every relationship it participates in.
    """
    _prepare_v3_demo(foundry, tmp_path)
    app_id = _mcp_application(foundry)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)

    granted = _user_mcp_headers(
        foundry,
        monkeypatch,
        app_id,
        scopes=(*_mcp_scopes(), "osdk:link:OrderCustomer:read"),
        suffix="links-granted",
    )
    session = client.post(
        f"/mcp/ontology/{app_id}",
        headers=granted,
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _mcp_initialize_params()},
    )
    request_headers = {**granted, "Mcp-Session-Id": session.headers["Mcp-Session-Id"]}
    traversed = _call(
        client,
        app_id,
        request_headers,
        rpc_id="links-granted",
        name="object.Order.links",
        arguments={"objectId": "O-1001", "linkType": "OrderCustomer"},
    )

    assert "error" not in traversed, traversed
    assert traversed["linkType"] == "OrderCustomer"
    links = list(traversed["links"])  # type: ignore[call-overload]
    assert links, traversed
    assert {link["to"]["objectType"] for link in links} == {"Customer"}

    withheld = _user_mcp_headers(
        foundry,
        monkeypatch,
        app_id,
        scopes=("osdk:object:Order:read",),
        suffix="links-withheld",
    )
    denied_session = client.post(
        f"/mcp/ontology/{app_id}",
        headers=withheld,
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _mcp_initialize_params()},
    )
    denied_headers = {**withheld, "Mcp-Session-Id": denied_session.headers["Mcp-Session-Id"]}
    denied = client.post(
        f"/mcp/ontology/{app_id}",
        headers=denied_headers,
        json={
            "jsonrpc": "2.0",
            "id": "links-withheld",
            "method": "tools/call",
            "params": {
                "name": "object.Order.links",
                "arguments": {"objectId": "O-1001", "linkType": "OrderCustomer"},
            },
        },
    )

    body = denied.json()
    assert "PERMISSION_DENIED" in str(body), body


def test_ontology_mcp_search_around_lands_on_a_different_type_and_needs_every_hop_grant(
    foundry: Any,
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    """Set-to-set traversal returns a type the caller never named in the request.

    That is the point of `searchAround` — ask about Orders, get Customers back — and it is also
    why the grant check matters more here than on a single-object link read: the result set is
    made entirely of objects the caller did not select. Each hop is gated on its own link scope.
    """
    _prepare_v3_demo(foundry, tmp_path)
    app_id = _mcp_application(foundry)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)

    granted = _user_mcp_headers(
        foundry,
        monkeypatch,
        app_id,
        scopes=(*_mcp_scopes(), "osdk:link:OrderCustomer:read"),
        suffix="around-granted",
    )
    session = client.post(
        f"/mcp/ontology/{app_id}",
        headers=granted,
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _mcp_initialize_params()},
    )
    request_headers = {**granted, "Mcp-Session-Id": session.headers["Mcp-Session-Id"]}
    around = _call(
        client,
        app_id,
        request_headers,
        rpc_id="around-granted",
        name="object.Order.searchAround",
        arguments={"linkTypes": ["OrderCustomer"]},
    )

    assert "error" not in around, around
    assert around["objectType"] == "Customer"
    assert around["fromObjectType"] == "Order"
    assert around["objectIds"], around

    withheld = _user_mcp_headers(
        foundry,
        monkeypatch,
        app_id,
        scopes=("osdk:object:Order:read",),
        suffix="around-withheld",
    )
    denied_session = client.post(
        f"/mcp/ontology/{app_id}",
        headers=withheld,
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _mcp_initialize_params()},
    )
    denied_headers = {**withheld, "Mcp-Session-Id": denied_session.headers["Mcp-Session-Id"]}
    denied = client.post(
        f"/mcp/ontology/{app_id}",
        headers=denied_headers,
        json={
            "jsonrpc": "2.0",
            "id": "around-withheld",
            "method": "tools/call",
            "params": {
                "name": "object.Order.searchAround",
                "arguments": {"linkTypes": ["OrderCustomer"]},
            },
        },
    )

    assert "PERMISSION_DENIED" in str(denied.json()), denied.json()
