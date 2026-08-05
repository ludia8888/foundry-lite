"""Consumer Ontology MCP application restriction and Action policy proof."""

from __future__ import annotations

import base64
import hashlib
from typing import Any

import pytest
from fastapi.testclient import TestClient
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, PermissionDenied
from foundry_lite.infrastructure.auth import JwtOidcAuthConfig, JwtOidcAuthProvider
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.main import app

from tests.integration.test_action_contract_v3_apply import _prepare_v3_demo

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
    app_id, headers = _mcp_application(foundry)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)

    initialized = client.post(
        f"/mcp/ontology/{app_id}",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
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
    assert conflicting_replay.json()["error"]["data"]["type"] == "CONFLICT"
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


def test_ontology_mcp_rejects_untrusted_origin_and_application_mismatch(
    foundry: Any,
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    _prepare_v3_demo(foundry, tmp_path)
    app_id, headers = _mcp_application(foundry)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)

    origin_denied = client.post(
        f"/mcp/ontology/{app_id}",
        headers={**headers, "Origin": "https://attacker.invalid"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    mismatch = client.post(
        "/mcp/ontology/not-the-token-app",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )

    assert origin_denied.status_code == 400
    assert mismatch.json()["error"]["data"]["type"] == "PERMISSION_DENIED"


def test_external_mcp_approval_rechecks_current_application_scope_before_human_execution(
    foundry: Any,
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    _prepare_v3_demo(foundry, tmp_path)
    app_id, headers = _mcp_application(foundry)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    initialized = client.post(
        f"/mcp/ontology/{app_id}",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
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
    app_id, headers = _mcp_application(foundry)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    hub = client.get("/api/developer-console/mcp-servers", headers=_principal_headers())
    disable_payload = {
        "status": "disabled",
        "descriptionMarkdown": "Order operations paused by an operator.",
        "allowedOrigins": ["https://chat.example.test"],
    }
    disabled = client.put(
        f"/api/developer-console/osdk-applications/{app_id}/mcp-server",
        headers={**_principal_headers(), "Idempotency-Key": "disable-ontology-mcp"},
        json=disable_payload,
    )
    replay = client.put(
        f"/api/developer-console/osdk-applications/{app_id}/mcp-server",
        headers={**_principal_headers(), "Idempotency-Key": "disable-ontology-mcp"},
        json=disable_payload,
    )
    denied = client.post(
        f"/mcp/ontology/{app_id}",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )

    assert hub.status_code == 200
    assert hub.json()[0]["applicationId"] == app_id
    assert hub.json()[0]["resourceCount"] == 3
    assert set(hub.json()[0]["authModes"]) == {"authorization_code_pkce", "client_credentials"}
    assert disabled.json()["status"] == "disabled"
    assert replay.json()["updated_at"] == disabled.json()["updated_at"]
    assert denied.json()["error"]["data"]["type"] == "PERMISSION_DENIED"


def test_ontology_mcp_durable_session_reconnect_resumes_after_last_event_and_delete_terminates(
    foundry: Any,
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    _prepare_v3_demo(foundry, tmp_path)
    app_id, headers = _mcp_application(foundry)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    initialized = client.post(
        f"/mcp/ontology/{app_id}",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    session_id = initialized.headers["Mcp-Session-Id"]
    request_headers = {**headers, "Mcp-Session-Id": session_id}
    _call(
        client,
        app_id,
        request_headers,
        rpc_id="get-order-for-session-event",
        name="object.Order.get",
        arguments={"objectId": "O-1001"},
    )
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

    assert f"id: {session_id}:2" in resumed.text
    assert "event: tool.completed" in resumed.text
    assert "object.Order.get" in resumed.text
    assert "id:" not in caught_up.text
    assert ": heartbeat" in caught_up.text
    assert terminated.status_code == 204
    assert denied_reuse.json()["error"]["data"]["type"] == "PERMISSION_DENIED"


def test_ontology_mcp_accepts_pkce_bearer_and_preserves_application_restrictions(
    foundry: Any,
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    _prepare_v3_demo(foundry, tmp_path)
    app_id, app_headers = _mcp_application(foundry)
    scopes = tuple(app_headers["X-Foundry-Lite-Scopes"].split())
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
    authorized = client.get(
        "/api/auth/osdk/oauth/authorize",
        params={
            "client_id": "ontology-mcp-pkce-client",
            "redirect_uri": "https://chat.example.test/oauth/callback",
            "code_challenge": _s256(verifier),
            "scope": " ".join(scopes),
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
        },
    )
    issuer = foundry._services.osdk_oauth_sessions.oauth_token_issuer
    provider = JwtOidcAuthProvider(
        JwtOidcAuthConfig(issuer=issuer.issuer, audience=issuer.audience, jwks=issuer.public_jwks())
    )
    monkeypatch.setattr(api_runtime, "get_auth_provider", lambda: provider)
    listed = client.post(
        f"/mcp/ontology/{app_id}",
        headers={"Authorization": f"Bearer {token.json()['accessToken']}", "X-Request-ID": "mcp-pkce"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )

    assert authorized.status_code == 200
    assert token.status_code == 200
    assert token.json()["access_token"] == token.json()["accessToken"]
    assert token.json()["token_type"] == token.json()["tokenType"]
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
    app_id, app_headers = _mcp_application(foundry)
    scopes = tuple(app_headers["X-Foundry-Lite-Scopes"].split())
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

    metadata = client.get("/.well-known/oauth-authorization-server")
    denied = client.post(
        "/api/auth/osdk/oauth/token",
        headers=_principal_headers(),
        json={
            "grantType": "client_credentials",
            "clientId": client_id,
            "clientSecret": "wrong-secret",
            "scope": " ".join(scopes),
        },
    )
    token = client.post(
        "/api/auth/osdk/oauth/token",
        headers=_principal_headers(),
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": " ".join(scopes),
        },
    )
    issuer = foundry._services.osdk_oauth_client_credentials.oauth_token_issuer
    provider = JwtOidcAuthProvider(
        JwtOidcAuthConfig(issuer=issuer.issuer, audience=issuer.audience, jwks=issuer.public_jwks())
    )
    principal = provider.authenticate({"Authorization": f"Bearer {token.json()['accessToken']}"})
    monkeypatch.setattr(api_runtime, "get_auth_provider", lambda: provider)
    listed = client.post(
        f"/mcp/ontology/{app_id}",
        headers={"Authorization": f"Bearer {token.json()['accessToken']}", "X-Request-ID": "mcp-service"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
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
        json={"jsonrpc": "2.0", "id": "old-access", "method": "initialize", "params": {}},
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
        json={"jsonrpc": "2.0", "id": "revoked-access", "method": "initialize", "params": {}},
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
    assert token.status_code == 200
    assert token.json()["access_token"] == token.json()["accessToken"]
    assert token.json()["expires_in"] == token.json()["expiresIn"]
    assert token.json()["grantType"] == "client_credentials"
    assert "refreshToken" not in token.json()
    assert principal.actor_user_id == f"service-principal:{client_id}"
    assert principal.application_id == app_id
    assert set(principal.token_scopes) == set(scopes)
    assert principal.oauth_session_id == token.json()["sessionId"]
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    assert "object.Order.get" in names
    assert "action.ExpediteOrder.apply" in names
    assert "object.Customer.get" not in names
    assert replayed_rotation.status_code == 200
    assert replayed_rotation.json()["clientSecret"] is None
    assert replayed_rotation.json()["isReplayed"] is True
    assert second_rotation.status_code == 200
    assert second_rotation.json()["revokedSessionCount"] == 1
    assert rotated_old_access_denied.json()["error"]["data"]["type"] == "PERMISSION_DENIED"
    assert old_secret_denied.status_code == 403
    assert rotated_token.status_code == 200
    assert [item["status"] for item in history.json()] == ["active", "rotated"]
    assert client_secret not in history.text and second_secret not in history.text
    assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"
    assert revoked.json()["revokedSessionCount"] == 1
    assert revoked_access_denied.json()["error"]["data"]["type"] == "PERMISSION_DENIED"
    assert revoked_secret_denied.status_code == 403


def _mcp_application(foundry: Any) -> tuple[str, dict[str, str]]:
    resources = []
    scopes = []
    for resource_type, name, operations in (
        ("object", "Order", ("read",)),
        ("action", "ExpediteOrder", ("validate", "execute")),
        ("action", "ApproveOrder", ("validate", "execute")),
    ):
        resource_scopes = [f"osdk:{resource_type}:{name}:{operation}" for operation in operations]
        resources.append({"resourceType": resource_type, "resourceApiName": name, "scopes": resource_scopes})
        scopes.extend(resource_scopes)
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
    return app_id, {
        "X-Tenant-ID": MCP_USER.tenant_id,
        "X-User-ID": MCP_USER.actor_user_id,
        "X-Roles": ",".join(MCP_USER.roles),
        "X-Foundry-Lite-App-ID": app_id,
        "X-Foundry-Lite-Client-ID": "ontology-mcp-client",
        "X-Foundry-Lite-Scopes": " ".join(scopes),
        "X-Request-ID": "ontology-mcp-test",
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
