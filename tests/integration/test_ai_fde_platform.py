"""Cross-domain FDE, lazy discovery, structured operations, and Builder MCP proof."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import jwt
from fastapi.testclient import TestClient
from foundry_lite.application.ports.language_model import ModelRequest, ModelResponse, ModelToolCall
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.auth import HeaderTrustAuthProvider, JwtOidcAuthConfig, JwtOidcAuthProvider
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.main import app
from foundry_lite_api.routers import builder_mcp as builder_mcp_router
from sqlalchemy import func, select

FDE_USER = RequestContext(
    tenant_id="tenant-demo",
    actor_user_id="fde-platform-user",
    roles=("data_engineer",),
    request_id="req-fde-platform",
)


class _LazyDocsModel:
    profile_name = "fde-lazy-docs"

    def __init__(self) -> None:
        self.offered: list[tuple[str, ...]] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.offered.append(request.tools)
        if request.model_call_attempt == 1:
            return _tool("fde.tools.search", {"query": "platform docs", "maxResults": 5})
        if request.model_call_attempt == 2:
            return _tool("platform.docs.search", {"query": "quality gate", "maxResults": 5})
        if request.model_call_attempt == 3:
            return _tool(
                "fde.plan.present",
                {
                    "objective": "Explain the release proof path",
                    "steps": ["Read the current status", "Run focused gates", "Run live gates"],
                    "assumptions": [],
                    "risks": ["Live infrastructure can be unavailable"],
                    "requiredApprovals": [],
                },
            )
        return ModelResponse(
            provider="fake",
            resolved_model_id="",
            resolved_model_revision="",
            content="The structured release plan is ready.",
            finish_reason="stop",
            input_tokens=5,
            output_tokens=6,
            normalized_tool_calls=(),
            provider_request_id="lazy-docs-final",
        )


class _PipelineInspectModel:
    profile_name = "fde-pipeline-inspect"

    def complete(self, request: ModelRequest) -> ModelResponse:
        if request.model_call_attempt == 1:
            return _tool("pipeline.branch.inspect", {})
        return ModelResponse(
            provider="fake",
            resolved_model_id="",
            resolved_model_revision="",
            content="The Pipeline branch and its diff were inspected.",
            finish_reason="stop",
            input_tokens=5,
            output_tokens=6,
            normalized_tool_calls=(),
            provider_request_id="pipeline-final",
        )


def test_ai_fde_lazy_tool_search_activates_docs_and_persists_structured_plan(foundry: Any) -> None:
    model = _LazyDocsModel()
    foundry._services.model_gateway.language_model_adapter = model

    outcome = foundry.aip.run_fde_payload(
        payload={
            "userMessage": "Find the release proof docs and make a plan.",
            "workspaceRef": "tenant:tenant-demo",
            "mode": "platform_qa",
            "toolDiscovery": "lazy",
            "agentRunId": "fde-lazy-docs-run",
            "maxToolCalls": 4,
        },
        ctx=FDE_USER,
    )

    assert outcome.result.run_status == "succeeded"
    assert "platform.docs.search@v1" not in model.offered[0]
    assert "platform.docs.search@v1" in model.offered[1]
    rows = _tool_rows(foundry, outcome.result.ai_run_id or "")
    assert [row["tool_id"] for row in rows] == [
        "fde.tools.search",
        "platform.docs.search",
        "fde.plan.present",
    ]
    assert rows[-1]["result_json"]["operationType"] == "plan"


def test_ai_fde_data_integration_mode_uses_native_pipeline_branch(foundry: Any) -> None:
    branch = foundry.pipelines.create_branch(
        pipeline_id="ai-fde-pipeline",
        name="AI FDE pipeline",
        idempotency_key="ai-fde-pipeline-branch",
        ctx=FDE_USER,
    )
    foundry._services.model_gateway.language_model_adapter = _PipelineInspectModel()

    outcome = foundry.aip.run_fde_payload(
        payload={
            "userMessage": "Inspect this pipeline branch.",
            "workspaceRef": f"pipeline-branch:{branch['id']}",
            "mode": "data_integration",
            "agentRunId": "fde-pipeline-run",
        },
        ctx=FDE_USER,
    )

    assert outcome.result.run_status == "succeeded"
    assert _tool_rows(foundry, outcome.result.ai_run_id or "")[0]["tool_id"] == "pipeline.branch.inspect"


def test_builder_mcp_is_oauth_app_restricted_and_idempotent(foundry: Any, monkeypatch: Any) -> None:
    scope = "osdk:connector:fde_platform_qa:execute"
    application = foundry.developer_console.create_osdk_application(
        app_api_name="fdeMcpDocs",
        display_name="FDE MCP Docs",
        client_id="client-fde-mcp-docs",
        resources=[
            {
                "resourceType": "connector",
                "resourceApiName": "fde_platform_qa",
                "scopes": [scope],
            }
        ],
        idempotency_key="fde-mcp-docs-app",
        ctx=FDE_USER,
    )
    app_id = str(application["application"]["id"])
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    headers = _builder_user_oauth_headers(foundry, monkeypatch, app_id, (scope,), "docs")

    initialized = client.post(
        f"/mcp/builder/{app_id}",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _mcp_initialize_params()},
    )
    session_id = initialized.headers["Mcp-Session-Id"]
    listed = client.post(
        f"/mcp/builder/{app_id}",
        headers={**headers, "Mcp-Session-Id": session_id},
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    assert "platform.docs.search" in names
    assert "ontology.branch.apply_patch" not in names

    call = {
        "jsonrpc": "2.0",
        "id": "docs-1",
        "method": "tools/call",
        "params": {
            "name": "platform.docs.search",
            "arguments": {
                "mode": "platform_qa",
                "workspaceRef": "tenant:tenant-demo",
                "arguments": {"query": "Action Types", "maxResults": 5},
            },
        },
    }
    first = client.post(f"/mcp/builder/{app_id}", headers={**headers, "Mcp-Session-Id": session_id}, json=call)
    replay = client.post(f"/mcp/builder/{app_id}", headers={**headers, "Mcp-Session-Id": session_id}, json=call)

    assert first.status_code == 200
    assert first.json()["result"]["structuredContent"]["count"] >= 1
    assert replay.json()["result"]["isReplayed"] is True
    assert replay.json()["result"]["aiRunId"] == first.json()["result"]["aiRunId"]


def test_builder_mcp_lazy_search_activates_only_scoped_tools_for_one_session(foundry: Any, monkeypatch: Any) -> None:
    app_id, headers = _builder_mcp_application(foundry, monkeypatch, "platform_qa")
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    initialized = client.post(
        f"/mcp/builder/{app_id}",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _mcp_initialize_params()},
    )
    session_id = initialized.headers["Mcp-Session-Id"]
    session_headers = {**headers, "Mcp-Session-Id": session_id}
    resource = f"http://testserver/mcp/builder/{app_id}"
    principal = api_runtime.get_auth_provider().authenticate_for_audience(headers, resource)
    pure_read = foundry.aip.fde_mcp_tools(
        app_id,
        session_id=session_id,
        discovery_mode="lazy",
        ctx=principal,
    )
    with foundry.engine.begin() as transaction:
        activations_before_negotiation = foundry.osdk_application_repository.mcp_tool_activations(
            transaction=transaction,
            tenant_id=principal.tenant_id,
            app_id=app_id,
            session_id=session_id,
            client_id=principal.client_id or "",
            actor_user_id=principal.actor_user_id,
        )
    before = client.post(
        f"/mcp/builder/{app_id}",
        headers=session_headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"discoveryMode": "lazy"}},
    )
    call = {
        "jsonrpc": "2.0",
        "id": "search-doc-tools",
        "method": "tools/call",
        "params": {
            "name": "search_tools",
            "arguments": {
                "mode": "platform_qa",
                "workspaceRef": "tenant:tenant-demo",
                "arguments": {"query": "documentation search", "maxResults": 5},
            },
        },
    }
    searched = client.post(f"/mcp/builder/{app_id}", headers=session_headers, json=call)
    replay = client.post(f"/mcp/builder/{app_id}", headers=session_headers, json=call)
    after = client.post(
        f"/mcp/builder/{app_id}",
        headers=session_headers,
        json={"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {"discoveryMode": "lazy"}},
    )
    new_session_headers = _builder_session_headers(
        client,
        app_id,
        {**headers, "X-Request-ID": "req-new-builder-session"},
    )
    new_session = client.post(
        f"/mcp/builder/{app_id}",
        headers=new_session_headers,
        json={"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {"discoveryMode": "lazy"}},
    )
    events = client.get(
        f"/mcp/builder/{app_id}?discoveryMode=lazy",
        headers=session_headers,
    )

    assert {tool["name"] for tool in pure_read["tools"]} == {"search_tools"}
    assert activations_before_negotiation == []
    assert {tool["name"] for tool in before.json()["result"]["tools"]} == {"search_tools"}
    activated = searched.json()["result"]["structuredContent"]["activatedTools"]
    assert "platform.docs.search" in {tool["toolId"] for tool in activated}
    assert "ontology.branch.apply_patch" not in {tool["toolId"] for tool in activated}
    assert searched.json()["result"]["structuredContent"]["toolsListChanged"] is True
    assert replay.json()["result"]["isReplayed"] is True
    assert {tool["name"] for tool in after.json()["result"]["tools"]} == {
        "get_custom_widget_documentation",
        "get_documentation_summaries",
        "get_ml_documentation",
        "search_tools",
        "search_foundry_documentation",
        "platform.docs.search",
    }
    assert {tool["name"] for tool in new_session.json()["result"]["tools"]} == {"search_tools"}
    assert "notifications/tools/list_changed" in events.text


def test_builder_mcp_accepts_pkce_oauth_bearer_with_resource_scope(foundry: Any, monkeypatch: Any) -> None:
    scope = "osdk:connector:fde_platform_qa:execute"
    application = foundry.developer_console.create_osdk_application(
        app_api_name="FdeMcpOAuth",
        display_name="FDE MCP OAuth",
        resources=[{"resourceType": "connector", "resourceApiName": "fde_platform_qa", "scopes": [scope]}],
        idempotency_key="fde-mcp-oauth-app",
        ctx=FDE_USER,
    )
    app_id = str(application["application"]["id"])
    foundry.developer_console.create_osdk_application_client(
        app_id,
        client_id="fde-mcp-oauth-client",
        redirect_uris=("https://chat.example.test/oauth/callback",),
        allowed_scopes=(scope,),
        idempotency_key="fde-mcp-oauth-client",
        ctx=FDE_USER,
    )
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    verifier_text = "builder-mcp-oauth-verifier"
    resource = f"http://testserver/mcp/builder/{app_id}"
    authorized = client.get(
        "/api/auth/osdk/oauth/authorize",
        params={
            "clientId": "fde-mcp-oauth-client",
            "redirectUri": "https://chat.example.test/oauth/callback",
            "codeChallenge": _s256(verifier_text),
            "scope": scope,
            "resource": resource,
        },
        headers=_api_headers(),
    )
    token = client.post(
        "/api/auth/osdk/oauth/token",
        headers=_api_headers(),
        json={
            "clientId": "fde-mcp-oauth-client",
            "code": authorized.json()["code"],
            "redirectUri": "https://chat.example.test/oauth/callback",
            "codeVerifier": verifier_text,
            "resource": resource,
        },
    )
    issuer = foundry._services.osdk_oauth_sessions.oauth_token_issuer
    provider = JwtOidcAuthProvider(
        JwtOidcAuthConfig(issuer=issuer.issuer, audience=issuer.audience, jwks=issuer.public_jwks())
    )
    monkeypatch.setattr(api_runtime, "get_auth_provider", lambda: provider)
    oauth_headers = {
        "Authorization": f"Bearer {token.json()['accessToken']}",
        "MCP-Protocol-Version": "2025-06-18",
        "X-Request-ID": "mcp-oauth",
    }
    initialized = client.post(
        f"/mcp/builder/{app_id}",
        headers=oauth_headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _mcp_initialize_params()},
    )
    listed = client.post(
        f"/mcp/builder/{app_id}",
        headers={**oauth_headers, "Mcp-Session-Id": initialized.headers["Mcp-Session-Id"]},
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )

    assert authorized.status_code == 200
    assert token.status_code == 200
    assert listed.status_code == 200
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    assert "platform.docs.search" in names
    assert "ontology.branch.apply_patch" not in names


def test_http_mcp_rejects_header_trust_even_with_dummy_bearer(monkeypatch: Any) -> None:
    monkeypatch.setattr(api_runtime, "get_auth_provider", lambda: HeaderTrustAuthProvider())
    client = TestClient(app)
    headers = {
        "Authorization": "Bearer dummy",
        "X-Tenant-ID": "tenant-demo",
        "X-User-ID": "attacker",
        "X-Foundry-Lite-App-ID": "app-under-test",
        "X-Foundry-Lite-Client-ID": "client-under-test",
        "X-Foundry-Lite-Scopes": "osdk:connector:fde_platform_qa:execute",
    }
    payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _mcp_initialize_params()}

    builder = client.post("/mcp/builder/app-under-test", headers=headers, json=payload)
    ontology = client.post("/mcp/ontology/app-under-test", headers=headers, json=payload)

    assert builder.status_code == 401
    assert ontology.status_code == 401
    assert "resource_metadata=" in builder.headers["WWW-Authenticate"]
    assert "resource_metadata=" in ontology.headers["WWW-Authenticate"]


def test_standard_oauth_code_resource_redirect_refresh_and_cross_plane_denial(
    foundry: Any,
    monkeypatch: Any,
) -> None:
    scope = "osdk:connector:fde_platform_qa:execute"
    created = foundry.developer_console.create_osdk_application(
        app_api_name="StandardBuilderOAuth",
        display_name="Standard Builder OAuth",
        client_id="standard-builder-bootstrap",
        resources=[{"resourceType": "connector", "resourceApiName": "fde_platform_qa", "scopes": [scope]}],
        idempotency_key="standard-builder-oauth-app",
        ctx=FDE_USER,
    )
    app_id = str(created["application"]["id"])
    redirect_uri = "https://chat.example.test/oauth/standard-builder"
    client_id = "standard-builder-public"
    foundry.developer_console.create_osdk_application_client(
        app_id,
        client_id=client_id,
        redirect_uris=(redirect_uri,),
        allowed_scopes=(scope,),
        idempotency_key="standard-builder-public-client",
        ctx=FDE_USER,
    )
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    resource = f"http://testserver/mcp/builder/{app_id}"
    wrong_resource = f"http://testserver/mcp/ontology/{app_id}"
    verifier = "standard-builder-oauth-verifier"
    authorized = client.get(
        "/api/auth/osdk/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": _s256(verifier),
            "code_challenge_method": "S256",
            "scope": scope,
            "state": "opaque-state",
            "resource": resource,
        },
        headers=_api_headers(),
        follow_redirects=False,
    )
    code = parse_qs(urlsplit(authorized.headers["location"]).query)["code"][0]
    token_form = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }
    wrong_plane = client.post("/api/auth/osdk/oauth/token", data={**token_form, "resource": wrong_resource})
    assert wrong_plane.status_code == 403, wrong_plane.text
    token = client.post("/api/auth/osdk/oauth/token", data={**token_form, "resource": resource})
    assert token.status_code == 200, token.text
    wrong_refresh = client.post(
        "/api/auth/osdk/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": token.json()["refresh_token"],
            "resource": wrong_resource,
        },
    )
    refreshed = client.post(
        "/api/auth/osdk/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": token.json()["refresh_token"],
            "resource": resource,
        },
    )
    metadata = client.get("/.well-known/oauth-authorization-server").json()
    protected = client.get(f"/.well-known/oauth-protected-resource/mcp/builder/{app_id}").json()
    claims = jwt.decode(refreshed.json()["access_token"], options={"verify_signature": False})
    issuer = foundry._services.osdk_oauth_sessions.oauth_token_issuer
    provider = JwtOidcAuthProvider(
        JwtOidcAuthConfig(issuer=issuer.issuer, audience=issuer.audience, jwks=issuer.public_jwks())
    )
    monkeypatch.setattr(api_runtime, "get_auth_provider", lambda: provider)
    cross_plane = client.post(
        f"/mcp/ontology/{app_id}",
        headers={"Authorization": f"Bearer {refreshed.json()['access_token']}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _mcp_initialize_params()},
    )

    assert authorized.status_code == 302
    assert parse_qs(urlsplit(authorized.headers["location"]).query)["state"] == ["opaque-state"]
    assert wrong_plane.status_code == 403
    assert wrong_refresh.status_code == 403
    assert token.status_code == 200
    assert refreshed.status_code == 200
    assert refreshed.json()["refresh_token"] != token.json()["refresh_token"]
    assert claims["aud"] == resource
    assert claims["iss"] == metadata["issuer"] == protected["authorization_servers"][0]
    assert protected["resource"] == resource
    assert protected["scopes_supported"] == [scope]
    assert cross_plane.status_code == 401
    assert "resource_metadata=" in cross_plane.headers["WWW-Authenticate"]


def test_builder_mcp_replay_schema_lazy_and_legacy_header_fail_closed(foundry: Any, monkeypatch: Any) -> None:
    app_id, headers = _builder_mcp_application(foundry, monkeypatch, "platform_qa")
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    session_headers = _builder_session_headers(client, app_id, headers)
    original = _mcp_tool_call_payload(
        "bound-request", "platform_qa", "tenant:tenant-demo", "platform.docs.search", {"query": "Action Types"}
    )
    first = client.post(f"/mcp/builder/{app_id}", headers=session_headers, json=original)

    changed_arguments = _mcp_tool_call_payload(
        "bound-request", "platform_qa", "tenant:tenant-demo", "platform.docs.search", {"query": "Datasets"}
    )
    changed_workspace = _mcp_tool_call_payload(
        "bound-request", "platform_qa", "docs:other", "platform.docs.search", {"query": "Action Types"}
    )
    changed_tool = _mcp_tool_call_payload(
        "bound-request", "platform_qa", "tenant:tenant-demo", "get_documentation_summaries", {}
    )
    conflicts = [
        client.post(f"/mcp/builder/{app_id}", headers=session_headers, json=payload).json()
        for payload in (changed_arguments, changed_workspace, changed_tool)
    ]
    replay_outer_extra = _mcp_tool_call_payload(
        "bound-request", "platform_qa", "tenant:tenant-demo", "platform.docs.search", {"query": "Action Types"}
    )
    replay_outer_extra["params"]["arguments"]["unexpected"] = True
    outer_extra = _mcp_tool_call_payload(
        "schema-outer", "platform_qa", "tenant:tenant-demo", "platform.docs.search", {"query": "docs"}
    )
    outer_extra["params"]["arguments"]["unexpected"] = True
    wrong_nested = _mcp_tool_call_payload(
        "schema-type",
        "platform_qa",
        "tenant:tenant-demo",
        "platform.docs.search",
        {"query": "docs", "maxResults": "five"},
    )
    schema_errors = [
        client.post(f"/mcp/builder/{app_id}", headers=session_headers, json=payload).json()
        for payload in (outer_extra, wrong_nested, replay_outer_extra)
    ]

    lazy_headers = _builder_session_headers(client, app_id, {**headers, "X-Request-ID": "lazy-direct-deny"})
    lazy_list = client.post(
        f"/mcp/builder/{app_id}",
        headers=lazy_headers,
        json={"jsonrpc": "2.0", "id": "lazy-list", "method": "tools/list", "params": {"discoveryMode": "lazy"}},
    )
    lazy_denied_response = client.post(
        f"/mcp/builder/{app_id}",
        headers=lazy_headers,
        json=_mcp_tool_call_payload(
            "lazy-direct", "platform_qa", "tenant:tenant-demo", "platform.docs.search", {"query": "docs"}
        ),
    )
    lazy_denied = lazy_denied_response.json()

    governance_app, governance_headers = _builder_mcp_application(foundry, monkeypatch, "governance")
    governance_session = _builder_session_headers(client, governance_app, governance_headers)
    governance_tools = client.post(
        f"/mcp/builder/{governance_app}",
        headers=governance_session,
        json={"jsonrpc": "2.0", "id": "schema-list", "method": "tools/list", "params": {}},
    ).json()["result"]["tools"]
    legacy_header = client.post(
        f"/mcp/builder/{governance_app}",
        headers={**governance_session, "X-FDE-Confirm-Tool": "create_foundry_project"},
        json=_mcp_tool_call_payload(
            "legacy-header",
            "governance",
            "tenant:tenant-demo",
            "create_foundry_project",
            {"displayName": "Must Not Exist", "idempotencyKey": "legacy-header-project"},
        ),
    ).json()

    assert first.json()["result"]["structuredContent"]["count"] >= 1
    assert lazy_headers["Mcp-Session-Id"] != session_headers["Mcp-Session-Id"]
    assert lazy_list.status_code == 200, lazy_list.text
    assert all(payload["error"]["data"]["type"] == "CONFLICT" and "result" not in payload for payload in conflicts)
    assert all(payload["error"]["data"]["type"] == "VALIDATION_FAILED" for payload in schema_errors)
    assert lazy_denied_response.status_code == 403
    assert lazy_denied["detail"]["details"]["reason"] == "tool_not_activated"
    assert legacy_header["result"]["structuredContent"]["status"] == "approval_required"
    schemas = {tool["name"]: tool["inputSchema"] for tool in governance_tools}
    write_schema = schemas["create_foundry_project"]
    assert "confirmationReceipt" in write_schema["properties"]
    assert "confirmationReceipt" not in write_schema["properties"]["arguments"]["properties"]
    assert "confirmationReceipt" not in schemas["search_foundry_projects"]["properties"]


def test_builder_mcp_durable_session_protocol_and_sse_contract(foundry: Any, monkeypatch: Any) -> None:
    app_id, headers = _builder_mcp_application(foundry, monkeypatch, "platform_qa")
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    invalid_protocol = client.post(
        f"/mcp/builder/{app_id}",
        headers={**headers, "MCP-Protocol-Version": "2024-01-01"},
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _mcp_initialize_params()},
    )
    initialized = client.post(
        f"/mcp/builder/{app_id}",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": _mcp_initialize_params()},
    )
    session_id = initialized.headers["Mcp-Session-Id"]
    session_headers = {**headers, "Mcp-Session-Id": session_id}
    accepted_notification = client.post(
        f"/mcp/builder/{app_id}",
        headers=session_headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
    )
    ping = client.post(
        f"/mcp/builder/{app_id}",
        headers=session_headers,
        json={"jsonrpc": "2.0", "id": "ping", "method": "ping", "params": {}},
    )
    rejected_notification = client.post(
        f"/mcp/builder/{app_id}",
        headers=session_headers,
        json={"jsonrpc": "2.0", "method": "notifications/unsupported", "params": {}},
    )
    client.post(
        f"/mcp/builder/{app_id}",
        headers=session_headers,
        json={"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {"discoveryMode": "lazy"}},
    )
    client.post(
        f"/mcp/builder/{app_id}",
        headers=session_headers,
        json=_mcp_tool_call_payload(
            "session-search", "platform_qa", "tenant:tenant-demo", "search_tools", {"query": "documentation"}
        ),
    )
    resource = f"http://testserver/mcp/builder/{app_id}"
    stream_ctx = api_runtime.get_auth_provider().authenticate_for_audience(headers, resource)
    active_lease = foundry.aip.claim_fde_mcp_session_stream(app_id, session_id, ctx=stream_ctx)
    concurrent_stream = client.get(f"/mcp/builder/{app_id}", headers=session_headers)
    assert foundry.aip.release_fde_mcp_session_stream(app_id, session_id, active_lease.lease_id, ctx=stream_ctx)
    events = client.get(f"/mcp/builder/{app_id}", headers=session_headers)
    released_lease = foundry.aip.claim_fde_mcp_session_stream(app_id, session_id, ctx=stream_ctx)
    assert foundry.aip.release_fde_mcp_session_stream(app_id, session_id, released_lease.lease_id, ctx=stream_ctx)
    original_session_events = foundry.aip.fde_mcp_session_events

    def fail_session_events(*args: object, **kwargs: object) -> list[object]:
        raise ValidationFailed("forced event read failure")

    monkeypatch.setattr(foundry.aip, "fde_mcp_session_events", fail_session_events)
    failed_event_read = client.get(f"/mcp/builder/{app_id}", headers=session_headers)
    monkeypatch.setattr(foundry.aip, "fde_mcp_session_events", original_session_events)
    guard_lease = foundry.aip.claim_fde_mcp_session_stream(app_id, session_id, ctx=stream_ctx)
    assert foundry.aip.release_fde_mcp_session_stream(app_id, session_id, guard_lease.lease_id, ctx=stream_ctx)
    response_class = builder_mcp_router.StreamingResponse

    def fail_streaming_response(*args: object, **kwargs: object) -> object:
        raise RuntimeError("forced response setup failure")

    monkeypatch.setattr(builder_mcp_router, "StreamingResponse", fail_streaming_response)
    failed_response_setup = TestClient(app, raise_server_exceptions=False).get(
        f"/mcp/builder/{app_id}", headers=session_headers
    )
    monkeypatch.setattr(builder_mcp_router, "StreamingResponse", response_class)
    response_guard_lease = foundry.aip.claim_fde_mcp_session_stream(app_id, session_id, ctx=stream_ctx)
    assert foundry.aip.release_fde_mcp_session_stream(app_id, session_id, response_guard_lease.lease_id, ctx=stream_ctx)
    resumed = client.get(
        f"/mcp/builder/{app_id}",
        headers={**session_headers, "Last-Event-ID": f"{session_id}:1"},
    )
    wrong_owner_ctx = RequestContext(
        tenant_id=FDE_USER.tenant_id,
        actor_user_id="different-builder-user",
        roles=FDE_USER.roles,
        request_id="different-builder-user",
    )
    wrong_owner_token = _builder_oauth_token(
        foundry,
        app_id=app_id,
        client_id="builder-user-platform-qa",
        redirect_uri="https://chat.example.test/oauth/platform-qa",
        scopes=("osdk:connector:fde_platform_qa:execute",),
        verifier="different-builder-user-verifier",
        resource=f"http://testserver/mcp/builder/{app_id}",
        ctx=wrong_owner_ctx,
    )
    wrong_owner = client.post(
        f"/mcp/builder/{app_id}",
        headers={**session_headers, "Authorization": f"Bearer {wrong_owner_token['accessToken']}"},
        json={"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {}},
    )
    wrong_owner_get = client.get(
        f"/mcp/builder/{app_id}",
        headers={**session_headers, "Authorization": f"Bearer {wrong_owner_token['accessToken']}"},
    )
    missing = client.post(
        f"/mcp/builder/{app_id}",
        headers={**headers, "Mcp-Session-Id": "mcp-session-that-does-not-exist"},
        json={"jsonrpc": "2.0", "id": 5, "method": "tools/list", "params": {}},
    )
    closed = client.delete(f"/mcp/builder/{app_id}", headers=session_headers)
    post_close = client.post(
        f"/mcp/builder/{app_id}",
        headers=session_headers,
        json={"jsonrpc": "2.0", "id": 6, "method": "tools/list", "params": {}},
    )
    get_close = client.get(f"/mcp/builder/{app_id}", headers=session_headers)

    assert invalid_protocol.status_code == 400
    assert accepted_notification.status_code == 202 and not accepted_notification.content
    assert ping.status_code == 200
    assert ping.json() == {"jsonrpc": "2.0", "id": "ping", "result": {}}
    assert rejected_notification.status_code == 400
    assert "jsonrpc" not in rejected_notification.text
    assert initialized.json()["result"]["capabilities"] == {"tools": {"listChanged": True}}
    assert concurrent_stream.status_code == 409
    assert concurrent_stream.json()["detail"]["code"] == "CONFLICT"
    assert concurrent_stream.json()["detail"]["details"]["resource"] == "mcp_session_stream"
    assert failed_event_read.status_code == 400
    assert failed_response_setup.status_code == 500
    assert "notifications/tools/list_changed" in events.text
    assert "notifications/foundry-lite/session_ready" in events.text
    assert "notifications/message" not in events.text
    assert f"id: {session_id}:1" not in resumed.text
    assert f"id: {session_id}:2" in resumed.text
    assert wrong_owner.status_code == wrong_owner_get.status_code == missing.status_code == 404
    assert closed.status_code == 204
    assert post_close.status_code == get_close.status_code == 404


def test_builder_mcp_enforces_initialize_and_json_rpc_wire_lifecycle(foundry: Any, monkeypatch: Any) -> None:
    app_id, headers = _builder_mcp_application(foundry, monkeypatch, "platform_qa")
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    path = f"/mcp/builder/{app_id}"

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
        json={"jsonrpc": "2.0", "id": "unknown", "method": "resources/list", "params": {}},
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
    assert missing_protocol.status_code == 400
    assert overlong_session.status_code == 400
    assert overlong_session.json()["detail"]["details"] == {"resource": "mcp_session"}

    original_dispatch = builder_mcp_router._dispatch

    def explode(*args: object, **kwargs: object) -> dict[str, object]:
        raise RuntimeError("sensitive downstream failure")

    monkeypatch.setattr(builder_mcp_router, "_dispatch", explode)
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
    monkeypatch.setattr(builder_mcp_router, "_dispatch", original_dispatch)

    assert internal_request.json() == {
        "jsonrpc": "2.0",
        "id": "internal",
        "error": {
            "code": -32603,
            "message": "Internal error",
            "data": {"requestId": "builder-platform-qa"},
        },
    }
    assert internal_notification.status_code == 500 and internal_notification.content == b""


def test_builder_mcp_confirmation_receipt_is_human_idempotent_and_one_time(foundry: Any, monkeypatch: Any) -> None:
    app_id, headers = _builder_mcp_application(foundry, monkeypatch, "governance")
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    session_headers = _builder_session_headers(client, app_id, headers)
    payload = _mcp_tool_call_payload(
        "receipt-once",
        "governance",
        "tenant:tenant-demo",
        "create_foundry_project",
        {"displayName": "Receipt Once", "idempotencyKey": "receipt-once-project"},
    )

    first = client.post(f"/mcp/builder/{app_id}", headers=session_headers, json=payload).json()["result"]
    repeated = client.post(f"/mcp/builder/{app_id}", headers=session_headers, json=payload).json()["result"]
    challenge_id = str(first["structuredContent"]["challengeId"])
    machine_approval = client.post(
        f"/api/aip/fde/mcp/{app_id}/confirmations/{challenge_id}/approve",
        headers=session_headers,
    )
    approver_headers = _control_headers(headers)
    approval_one = client.post(
        f"/api/aip/fde/mcp/{app_id}/confirmations/{challenge_id}/approve",
        headers=approver_headers,
    )
    approval_two = client.post(
        f"/api/aip/fde/mcp/{app_id}/confirmations/{challenge_id}/approve",
        headers=approver_headers,
    )
    receipt = str(approval_one.json()["confirmationReceipt"])
    payload["params"]["arguments"]["confirmationReceipt"] = receipt
    completed = client.post(f"/mcp/builder/{app_id}", headers=session_headers, json=payload).json()
    exact_replay = client.post(f"/mcp/builder/{app_id}", headers=session_headers, json=payload).json()
    reuse_payload = _mcp_tool_call_payload(
        "receipt-reuse",
        "governance",
        "tenant:tenant-demo",
        "create_foundry_project",
        {"displayName": "Receipt Once", "idempotencyKey": "receipt-once-project"},
    )
    reuse_payload["params"]["arguments"]["confirmationReceipt"] = receipt
    receipt_reuse = client.post(f"/mcp/builder/{app_id}", headers=session_headers, json=reuse_payload).json()
    approval_after_consumption = client.post(
        f"/api/aip/fde/mcp/{app_id}/confirmations/{challenge_id}/approve",
        headers=approver_headers,
    )
    events = client.get(f"/mcp/builder/{app_id}", headers=session_headers).text

    assert first["structuredContent"]["status"] == "approval_required"
    assert repeated["structuredContent"]["challengeId"] == challenge_id
    assert repeated["isReplayed"] is True
    assert machine_approval.status_code == 403
    assert approval_one.status_code == approval_two.status_code == 200
    assert approval_two.json()["confirmationReceipt"] == receipt
    assert completed["result"]["structuredContent"]["project"]["displayName"] == "Receipt Once"
    assert exact_replay["result"]["isReplayed"] is True
    assert receipt_reuse["error"]["data"]["type"] == "CONFLICT"
    assert receipt_reuse["error"]["data"]["reason"] == "receipt_already_consumed"
    assert approval_after_consumption.status_code == 409
    assert len(_tool_rows(foundry, completed["result"]["aiRunId"])) == 1
    assert events.count("notifications/foundry-lite/approval_required") == 1
    assert events.count("notifications/foundry-lite/tool_completed") == 1


def test_builder_mcp_receipt_consumption_rolls_back_with_run_claim(foundry: Any, monkeypatch: Any) -> None:
    app_id, headers = _builder_mcp_application(foundry, monkeypatch, "governance")
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    session_headers = _builder_session_headers(client, app_id, headers)
    payload = _mcp_tool_call_payload(
        "receipt-atomic-claim",
        "governance",
        "tenant:tenant-demo",
        "create_foundry_project",
        {"displayName": "Atomic Receipt", "idempotencyKey": "atomic-receipt-project"},
    )
    challenged = client.post(f"/mcp/builder/{app_id}", headers=session_headers, json=payload).json()
    challenge_id = str(challenged["result"]["structuredContent"]["challengeId"])
    approved = client.post(
        f"/api/aip/fde/mcp/{app_id}/confirmations/{challenge_id}/approve",
        headers=_control_headers(headers),
    ).json()
    payload["params"]["arguments"]["confirmationReceipt"] = approved["confirmationReceipt"]

    repository = foundry.aip._fde_mcp.ai_run_repository
    append_event = repository.append_execution_event
    injected = False

    def fail_after_receipt_consumption(*, transaction: Any, record: Any) -> bool:
        nonlocal injected
        if record.event_type == "mcp_tool_running" and not injected:
            injected = True
            raise RuntimeError("injected run-claim failure")
        return append_event(transaction=transaction, record=record)

    monkeypatch.setattr(repository, "append_execution_event", fail_after_receipt_consumption)
    failed = TestClient(app, raise_server_exceptions=False).post(
        f"/mcp/builder/{app_id}", headers=session_headers, json=payload
    )
    monkeypatch.setattr(repository, "append_execution_event", append_event)
    retried = client.post(f"/mcp/builder/{app_id}", headers=session_headers, json=payload).json()

    assert failed.status_code == 200
    assert failed.json() == {
        "jsonrpc": "2.0",
        "id": "receipt-atomic-claim",
        "error": {
            "code": -32603,
            "message": "Internal error",
            "data": {"requestId": "builder-governance"},
        },
    }
    assert injected is True
    assert retried["result"]["structuredContent"]["project"]["displayName"] == "Atomic Receipt"
    assert len(_tool_rows(foundry, retried["result"]["aiRunId"])) == 1


def test_builder_mcp_concurrent_initial_calls_have_one_durable_winner(foundry: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    write_app, write_headers = _builder_mcp_application(foundry, monkeypatch, "governance")
    write_session = _builder_session_headers(client, write_app, write_headers)
    challenge_payload = _mcp_tool_call_payload(
        "concurrent-challenge",
        "governance",
        "tenant:tenant-demo",
        "create_foundry_project",
        {"displayName": "Concurrent Challenge", "idempotencyKey": "concurrent-challenge"},
    )
    challenge_responses = _concurrent_mcp_posts(client, write_app, write_session, challenge_payload)
    challenge_bodies = [response.json() for response in challenge_responses]
    challenge_ids = {body["result"]["structuredContent"]["challengeId"] for body in challenge_bodies}
    write_events = client.get(f"/mcp/builder/{write_app}", headers=write_session).text

    read_app, read_headers = _builder_mcp_application(foundry, monkeypatch, "platform_qa")
    read_session = _builder_session_headers(client, read_app, read_headers)
    read_payload = _mcp_tool_call_payload(
        "concurrent-read",
        "platform_qa",
        "tenant:tenant-demo",
        "platform.docs.search",
        {"query": "quality gate"},
    )
    read_responses = _concurrent_mcp_posts(client, read_app, read_session, read_payload)
    read_run_id = _assert_concurrent_result_or_conflict(read_responses)
    read_replay = client.post(f"/mcp/builder/{read_app}", headers=read_session, json=read_payload).json()

    search_payload = _mcp_tool_call_payload(
        "concurrent-search",
        "platform_qa",
        "tenant:tenant-demo",
        "search_tools",
        {"query": "documentation", "maxResults": 5},
    )
    search_responses = _concurrent_mcp_posts(client, read_app, read_session, search_payload)
    search_run_id = _assert_concurrent_result_or_conflict(search_responses)
    search_replay = client.post(f"/mcp/builder/{read_app}", headers=read_session, json=search_payload).json()

    assert all(response.status_code == 200 for response in challenge_responses)
    assert len(challenge_ids) == 1
    assert sum(body["result"]["isReplayed"] is True for body in challenge_bodies) == 1
    assert write_events.count("notifications/foundry-lite/approval_required") == 1
    assert read_replay["result"]["isReplayed"] is True
    assert search_replay["result"]["isReplayed"] is True
    assert len(_tool_rows(foundry, read_run_id)) == 1
    assert len(_tool_rows(foundry, search_run_id)) == 1


def test_builder_mcp_executes_previously_uncovered_ontology_mutations(
    foundry: Any, monkeypatch: Any, tmp_path: Any
) -> None:
    orders_path = tmp_path / "mcp-mutation-orders.csv"
    customers_path = tmp_path / "mcp-mutation-customers.csv"
    orders_path.write_text("order_id,customer_id,status\nO-1,C-1,PENDING\n", encoding="utf-8")
    customers_path.write_text("customer_id,name\nC-1,Seoul Table\n", encoding="utf-8")
    foundry.datasets.ensure("clean.mcp_mutation_orders", primary_key=["order_id"], ctx=FDE_USER)
    foundry.datasets.ensure("clean.mcp_mutation_customers", primary_key=["customer_id"], ctx=FDE_USER)
    foundry.datasets.upload_csv("clean.mcp_mutation_orders", str(orders_path), ctx=FDE_USER)
    foundry.datasets.upload_csv("clean.mcp_mutation_customers", str(customers_path), ctx=FDE_USER)
    foundry.ontology.apply_text(_mcp_mutation_base_ontology(), ctx=FDE_USER)
    branch = foundry.ontology.create_branch(
        name="mcp-uncovered-ontology", idempotency_key="mcp-uncovered-ontology", ctx=FDE_USER
    )
    branch_id = str(branch["id"])
    app_id, headers = _builder_mcp_application(foundry, monkeypatch, "ontology_editing")
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    workspace = f"ontology-branch:{branch_id}"

    created_link = _mcp_native_call(
        client,
        app_id,
        headers,
        "uncovered-create-link",
        "ontology_editing",
        workspace,
        "create_or_update_foundry_link_type",
        {"definition": _mcp_link_definition(), "changeSummary": "Create OrderCustomer"},
    )
    created_action = _mcp_native_call(
        client,
        app_id,
        headers,
        "uncovered-create-action",
        "ontology_editing",
        workspace,
        "create_or_update_foundry_action_type",
        {"definition": _mcp_action_definition(), "changeSummary": "Create ApproveOrder"},
    )
    deleted_link = _mcp_native_call(
        client,
        app_id,
        headers,
        "uncovered-delete-link",
        "ontology_editing",
        workspace,
        "delete_foundry_link_type",
        {"apiName": "OrderCustomer", "changeSummary": "Delete OrderCustomer"},
    )
    deleted_action = _mcp_native_call(
        client,
        app_id,
        headers,
        "uncovered-delete-action",
        "ontology_editing",
        workspace,
        "delete_foundry_action_type",
        {"apiName": "ApproveOrder", "changeSummary": "Delete ApproveOrder"},
    )
    _mcp_native_call(
        client,
        app_id,
        headers,
        "uncovered-marker-object",
        "ontology_editing",
        workspace,
        "create_or_update_foundry_object_type",
        {"definition": _mcp_marker_object_definition(), "changeSummary": "Leave reviewable branch diff"},
    )
    proposed = _mcp_native_call(
        client,
        app_id,
        headers,
        "uncovered-ontology-propose",
        "ontology_editing",
        workspace,
        "ontology.branch.propose",
        {
            "title": "Uncovered Ontology mutations",
            "description": "Direct Builder MCP regression proof.",
            "idempotencyKey": "uncovered-ontology-proposal",
        },
    )

    assert created_link["validation"]["status"] == "valid"
    assert created_action["validation"]["status"] == "valid"
    assert deleted_link["changeSummary"] == "Delete OrderCustomer"
    assert deleted_action["changeSummary"] == "Delete ApproveOrder"
    assert proposed["status"] == "open"
    assert not _active_object_type_exists(foundry, "McpMutationMarker")


def test_builder_mcp_executes_uncovered_pipeline_source_osdk_and_pilot_mutations(
    foundry: Any, monkeypatch: Any, tmp_path: Any
) -> None:
    foundry.ontology.apply_text("objectTypes: []\nactionTypes: []\nlinkTypes: []\n", ctx=FDE_USER)
    raw_path = tmp_path / "mcp-pipeline-orders.csv"
    raw_path.write_text("order_id,amount\nO-1,10\n", encoding="utf-8")
    foundry.datasets.ensure("raw.mcp_pipeline_orders", primary_key=["order_id"], ctx=FDE_USER)
    foundry.datasets.upload_csv("raw.mcp_pipeline_orders", str(raw_path), ctx=FDE_USER)
    pipeline_branch = foundry.pipelines.create_branch(
        pipeline_id="mcp-uncovered-pipeline",
        name="mcp-uncovered-pipeline",
        idempotency_key="mcp-uncovered-pipeline",
        ctx=FDE_USER,
    )
    pipeline_id = str(pipeline_branch["id"])
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    pipeline_app, pipeline_headers = _builder_mcp_application(foundry, monkeypatch, "data_integration")
    pipeline_workspace = f"pipeline-branch:{pipeline_id}"
    updated = _mcp_native_call(
        client,
        pipeline_app,
        pipeline_headers,
        "uncovered-pipeline-update",
        "data_integration",
        pipeline_workspace,
        "pipeline.branch.update_graph",
        {
            "graph": _mcp_pipeline_graph(),
            "expectedFingerprint": pipeline_branch["graphFingerprint"],
        },
    )
    tested = _mcp_native_call(
        client,
        pipeline_app,
        pipeline_headers,
        "uncovered-pipeline-test",
        "data_integration",
        pipeline_workspace,
        "pipeline.branch.run_tests",
        {},
    )
    pipeline_proposal = _mcp_native_call(
        client,
        pipeline_app,
        pipeline_headers,
        "uncovered-pipeline-propose",
        "data_integration",
        pipeline_workspace,
        "pipeline.branch.propose",
        {
            "title": "Uncovered Pipeline mutations",
            "description": "Direct Builder MCP regression proof.",
            "idempotencyKey": "uncovered-pipeline-proposal",
        },
    )

    foundry.datasets.ensure("raw.mcp_probe_events", primary_key=["id"], ctx=FDE_USER)
    source = foundry.sources.create_webhook_listener(
        source_name="mcp_probe_source",
        display_name="MCP probe Source",
        dataset_ref="raw.mcp_probe_events",
        connector_name="mcp_probe_connector",
        resource_name="events",
        signing_secret_ref="MCP_PROBE_SIGNING_SECRET",
        inbound_url="https://foundry-lite.example.test/hooks/mcp-probe",
        idempotency_key="mcp-probe-source",
        ctx=FDE_USER,
    )
    source_row = source["source"]
    source_calls: list[tuple[str, str, str]] = []

    def fake_source_test(
        source_name: str,
        *,
        expected_config_fingerprint: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        source_calls.append((source_name, expected_config_fingerprint, idempotency_key))
        return {"sourceName": source_name, "status": "succeeded", "connectionTestId": "mcp-probe-test"}

    monkeypatch.setattr(
        foundry._services.fde_platform_tools.source_connection_test_service,
        "test_source_connection",
        fake_source_test,
    )
    source_app, source_headers = _builder_mcp_application(foundry, monkeypatch, "data_connection")
    source_test = _mcp_native_call(
        client,
        source_app,
        source_headers,
        "uncovered-source-test",
        "data_connection",
        "source:mcp_probe_source",
        "source.test_connection",
        {
            "expectedConfigFingerprint": source_row["configFingerprint"],
            "idempotencyKey": "uncovered-source-test",
        },
    )

    osdk_app, osdk_headers = _builder_mcp_application(foundry, monkeypatch, "osdk_react")
    osdk_scope = "osdk:connector:fde_osdk_react:execute"
    osdk_update = _mcp_native_call(
        client,
        osdk_app,
        osdk_headers,
        "uncovered-osdk-update",
        "osdk_react",
        f"osdk-app:{osdk_app}",
        "osdk.application.update_resources",
        {
            "resources": [
                {
                    "resourceType": "connector",
                    "resourceApiName": "fde_osdk_react",
                    "scopes": [osdk_scope],
                }
            ],
            "idempotencyKey": "uncovered-osdk-update",
        },
    )

    pilot_plan = _mcp_native_call(
        client,
        osdk_app,
        osdk_headers,
        "uncovered-pilot-plan",
        "osdk_react",
        f"osdk-app:{osdk_app}",
        "pilot.application.plan",
        {"applicationName": "MCP Pilot", "domainDescription": "Direct mutation regression"},
    )
    pilot = _mcp_native_call(
        client,
        osdk_app,
        osdk_headers,
        "uncovered-pilot-generate",
        "osdk_react",
        f"osdk-app:{osdk_app}",
        "pilot.application.generate",
        {"plan": pilot_plan, "idempotencyKey": "uncovered-pilot-generate"},
    )

    assert updated["graphFingerprint"] != pipeline_branch["graphFingerprint"]
    assert tested["proofKind"] == "static_graph_output_contract"
    assert pipeline_proposal["status"] == "submitted"
    assert source_test["connectionTestId"] == "mcp-probe-test"
    assert source_calls == [("mcp_probe_source", source_row["configFingerprint"], "uncovered-source-test")]
    assert osdk_update["application"]["id"] == osdk_app
    assert pilot["status"] == "generated_on_branch"


def test_official_palantir_mcp_names_execute_native_compass_object_docs_and_osdk_services(
    foundry: Any, monkeypatch: Any, tmp_path: Any
) -> None:
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    governance_app, governance_headers = _builder_mcp_application(foundry, monkeypatch, "governance")
    created = _mcp_native_call(
        client,
        governance_app,
        governance_headers,
        "create-project",
        "governance",
        "tenant:tenant-demo",
        "create_foundry_project",
        {"displayName": "Official MCP Project", "idempotencyKey": "official-mcp-project"},
    )
    project_id = created["project"]["id"]
    searched = _mcp_native_call(
        client,
        governance_app,
        governance_headers,
        "search-project",
        "governance",
        "tenant:tenant-demo",
        "search_foundry_projects",
        {"query": "Official MCP"},
    )
    imports = _mcp_native_call(
        client,
        governance_app,
        governance_headers,
        "project-imports",
        "governance",
        f"project:{project_id}",
        "get_project_imports",
        {"projectId": project_id},
    )
    _seed_official_mcp_objects(foundry, tmp_path)
    exploration_app, exploration_headers = _builder_mcp_application(
        foundry,
        monkeypatch,
        "exploration",
        additional_resources=(
            {
                "resourceType": "object",
                "resourceApiName": "Order",
                "scopes": ["osdk:object:Order:read"],
            },
        ),
    )
    queried = _mcp_native_call(
        client,
        exploration_app,
        exploration_headers,
        "query-orders",
        "exploration",
        "tenant:tenant-demo",
        "query_ontology_objects",
        {"objectType": "Order", "limit": 10},
    )
    aggregated = _mcp_native_call(
        client,
        exploration_app,
        exploration_headers,
        "aggregate-orders",
        "exploration",
        "tenant:tenant-demo",
        "aggregate_ontology_objects",
        {"objectType": "Order", "groupBy": ["status"], "select": [{"function": "count", "name": "orders"}]},
    )
    dataset_schema = _mcp_native_call(
        client,
        exploration_app,
        exploration_headers,
        "dataset-schema",
        "exploration",
        "dataset:clean.official_mcp_orders",
        "get_foundry_dataset_schema",
        {"datasetRef": "clean.official_mcp_orders"},
    )
    dataset_files = _mcp_native_call(
        client,
        exploration_app,
        exploration_headers,
        "dataset-files",
        "exploration",
        "dataset:clean.official_mcp_orders",
        "list_dataset_files",
        {"datasetRef": "clean.official_mcp_orders"},
    )
    dataset_stats = _mcp_native_call(
        client,
        exploration_app,
        exploration_headers,
        "dataset-stats",
        "exploration",
        "dataset:clean.official_mcp_orders",
        "get_dataset_stats",
        {"datasetRef": "clean.official_mcp_orders"},
    )
    lineage = _mcp_native_call(
        client,
        exploration_app,
        exploration_headers,
        "dataset-lineage",
        "exploration",
        "tenant:tenant-demo",
        "get_resource_graph",
        {"resourceId": dataset_schema["version"]["id"], "maxDepth": 3},
    )
    docs_app, docs_headers = _builder_mcp_application(foundry, monkeypatch, "platform_qa")
    summaries = _mcp_native_call(
        client,
        docs_app,
        docs_headers,
        "docs-summaries",
        "platform_qa",
        "tenant:tenant-demo",
        "get_documentation_summaries",
        {},
    )
    page = _mcp_native_call(
        client,
        docs_app,
        docs_headers,
        "docs-page",
        "platform_qa",
        "tenant:tenant-demo",
        "load_foundry_documentation_page",
        {"documentId": "action-types"},
    )
    sdk_apis = _mcp_native_call(
        client,
        docs_app,
        docs_headers,
        "platform-sdk-list",
        "platform_qa",
        "tenant:tenant-demo",
        "list_platform_sdk_apis",
        {"product": "dataset", "maxResults": 10},
    )
    sdk_reference = _mcp_native_call(
        client,
        docs_app,
        docs_headers,
        "platform-sdk-reference",
        "platform_qa",
        "tenant:tenant-demo",
        "get_platform_sdk_api_reference",
        {"apiId": "datasets.inspect"},
    )
    transform_docs = _mcp_native_call(
        client,
        docs_app,
        docs_headers,
        "transform-docs",
        "platform_qa",
        "tenant:tenant-demo",
        "get_python_transforms_documentation",
        {"topic": "transactions"},
    )
    osdk_app, osdk_headers = _builder_mcp_application(foundry, monkeypatch, "osdk_react")
    definition = _mcp_native_call(
        client,
        osdk_app,
        osdk_headers,
        "view-osdk",
        "osdk_react",
        f"osdk-app:{osdk_app}",
        "view_osdk_definition",
        {},
    )
    osdk_context = _mcp_native_call(
        client,
        osdk_app,
        osdk_headers,
        "osdk-context",
        "osdk_react",
        f"osdk-app:{osdk_app}",
        "get_ontology_sdk_context",
        {"topic": "ObjectSet"},
    )
    generated = _mcp_native_call(
        client,
        osdk_app,
        osdk_headers,
        "generate-osdk",
        "osdk_react",
        f"osdk-app:{osdk_app}",
        "generate_new_ontology_sdk_version",
        {"language": "typescript", "idempotencyKey": "official-mcp-sdk-version"},
    )
    install = _mcp_native_call(
        client,
        osdk_app,
        osdk_headers,
        "install-osdk",
        "osdk_react",
        f"osdk-app:{osdk_app}",
        "install_sdk_package",
        {},
    )

    assert project_id in {item["id"] for item in searched["items"]}
    assert imports == {"projectId": project_id, "items": [], "count": 0, "nextCursor": None}
    assert [item["objectId"] for item in queried["items"]] == ["MCP-ORDER-1", "MCP-ORDER-2"]
    assert aggregated["groups"][0]["metrics"]["orders"] == 2
    assert dataset_schema["schema"]["columns"][0]["name"] == "order_id"
    assert dataset_files["count"] == 1
    assert dataset_files["isManifestBounded"] is True
    assert dataset_stats["rowCount"] == 2
    assert dataset_stats["fileCount"] == 1
    assert lineage["rootResourceId"] == dataset_schema["version"]["id"]
    assert lineage["maxDepth"] == 3
    assert summaries["count"] >= 6
    assert "Foundry-lite Action Types v3 parity matrix" in page["content"]
    assert "datasets.inspect" in {item["id"] for item in sdk_apis["items"]}
    assert sdk_reference["api"]["route"].endswith("/inspect")
    assert transform_docs["isFoundryLiteDocumentation"] is True
    assert definition["definition"]["application"]["id"] == osdk_app
    assert osdk_context["contextType"] == "ontology_sdk"
    assert generated["sdkVersion"]["language"] == "typescript"
    assert install["sdkVersions"][0]["language"] == "typescript"


def test_pilot_generates_replay_safe_seed_ontology_osdk_and_retrievable_bundle(foundry: Any, monkeypatch: Any) -> None:
    foundry.ontology.apply_text("objectTypes: []\nactionTypes: []\nlinkTypes: []\n", ctx=FDE_USER)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    planned = client.post(
        "/api/aip/pilot/plan",
        headers=_api_headers(),
        json={"applicationName": "Dining Concierge", "domainDescription": "Foreign traveler booking operations"},
    )
    plan = planned.json()

    first_response = client.post(
        "/api/aip/pilot/applications",
        headers={**_api_headers(), "Idempotency-Key": "pilot-dining-1"},
        json={"plan": plan},
    )
    replay_response = client.post(
        "/api/aip/pilot/applications",
        headers={**_api_headers(), "Idempotency-Key": "pilot-dining-1"},
        json={"plan": plan},
    )
    first = first_response.json()
    replay = replay_response.json()

    resource = first["resource"]
    assert first["status"] == "generated_on_branch"
    assert len(first["ontologyBranch"]["diff"]["resources"]) == 1
    assert first["osdkApplication"]["application"]["app_api_name"] == "dining_concierge"
    assert len(foundry.datasets.list_versions(first["seed"]["datasetRef"], ctx=FDE_USER)) == 1
    assert replay["isReplayed"] is True
    assert replay["seed"]["versionId"] == first["seed"]["versionId"]

    response = client.get(f"/api/aip/pilot/applications/{resource['rid']}", headers=_api_headers())
    assert planned.status_code == 200
    assert first_response.status_code == 200
    assert replay_response.status_code == 200
    assert response.status_code == 200
    assert response.json()["reactFiles"]["src/App.tsx"]
    assert response.json()["applicationPath"].startswith("/projects/")


def test_builder_mcp_requires_human_confirmation_receipt_and_rejects_untrusted_origin(
    foundry: Any, monkeypatch: Any, tmp_path: Any
) -> None:
    csv_path = tmp_path / "mcp-restaurants.csv"
    csv_path.write_text("id,name\nR-1,Seoul Table\n", encoding="utf-8")
    foundry.datasets.ensure("clean.restaurants", primary_key=["id"], ctx=FDE_USER)
    foundry.datasets.upload_csv("clean.restaurants", str(csv_path), ctx=FDE_USER)
    foundry.ontology.apply_text("objectTypes: []\nactionTypes: []\nlinkTypes: []\n", ctx=FDE_USER)
    branch = foundry.ontology.create_branch(name="mcp-write", idempotency_key="mcp-write-branch", ctx=FDE_USER)
    app_id, headers = _builder_mcp_application(foundry, monkeypatch, "ontology_editing")
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    call = _mcp_patch_call(str(branch["id"]), "write-denied")

    session_headers = _builder_session_headers(client, app_id, headers)
    denied = client.post(f"/mcp/builder/{app_id}", headers=session_headers, json=call)
    challenge = denied.json()["result"]["structuredContent"]
    receipt = _approve_mcp_challenge(client, app_id, str(challenge["challengeId"]), headers)
    approved_call = _mcp_patch_call(str(branch["id"]), "write-denied")
    approved_call["params"]["arguments"]["confirmationReceipt"] = receipt
    approved = client.post(f"/mcp/builder/{app_id}", headers=session_headers, json=approved_call)
    rejected_origin = client.post(
        f"/mcp/builder/{app_id}",
        headers={**headers, "Origin": "https://attacker.invalid"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )

    assert denied.status_code == 200
    assert challenge["status"] == "approval_required"
    assert approved.status_code == 200
    assert approved.json()["result"]["structuredContent"]["changeSummary"] == "Add Restaurant"
    assert rejected_origin.status_code == 400
    assert len(foundry.ontology.branch_diff(str(branch["id"]), ctx=FDE_USER)["resources"]) == 1


def test_official_palantir_ontology_tools_are_branch_only_and_confirmation_gated(
    foundry: Any, monkeypatch: Any, tmp_path: Any
) -> None:
    csv_path = tmp_path / "official-mcp-restaurants.csv"
    csv_path.write_text("id,name\nR-1,Seoul Table\n", encoding="utf-8")
    foundry.datasets.ensure("clean.restaurants", primary_key=["id"], ctx=FDE_USER)
    foundry.datasets.upload_csv("clean.restaurants", str(csv_path), ctx=FDE_USER)
    foundry.ontology.apply_text("objectTypes: []\nactionTypes: []\nlinkTypes: []\n", ctx=FDE_USER)
    branch = foundry.ontology.create_branch(
        name="official-ontology-mcp",
        idempotency_key="official-ontology-mcp-branch",
        ctx=FDE_USER,
    )
    branch_id = str(branch["id"])
    app_id, headers = _builder_mcp_application(foundry, monkeypatch, "ontology_editing")
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    definition = {
        "apiName": "Restaurant",
        "primaryKey": "id",
        "backing": {"dataset": "clean.restaurants"},
        "properties": [{"apiName": "id", "column": "id", "type": "string", "nullable": False}],
    }
    denied = _raw_mcp_native_call(
        client,
        app_id,
        headers,
        "denied-create",
        branch_id,
        "create_or_update_foundry_object_type",
        {"definition": definition, "changeSummary": "Add Restaurant"},
    )
    created = _mcp_native_call(
        client,
        app_id,
        headers,
        "approved-create",
        "ontology_editing",
        f"ontology-branch:{branch_id}",
        "create_or_update_foundry_object_type",
        {"definition": definition, "changeSummary": "Add Restaurant"},
    )
    viewed = _mcp_native_call(
        client,
        app_id,
        headers,
        "view-object",
        "ontology_editing",
        f"ontology-branch:{branch_id}",
        "view_foundry_object_type",
        {"apiName": "Restaurant"},
    )
    searched = _mcp_native_call(
        client,
        app_id,
        headers,
        "search-ontology",
        "ontology_editing",
        f"ontology-branch:{branch_id}",
        "search_foundry_ontology",
        {"query": "Restaurant"},
    )
    identity = _mcp_native_call(
        client,
        app_id,
        headers,
        "ontology-rid",
        "ontology_editing",
        f"ontology-branch:{branch_id}",
        "get_foundry_ontology_rid",
        {},
    )

    assert denied["result"]["structuredContent"]["status"] == "approval_required"
    assert created["changeSummary"] == "Add Restaurant"
    assert viewed["definition"]["apiName"] == "Restaurant"
    assert searched["items"][0]["apiName"] == "Restaurant"
    assert identity["branchId"] == branch_id
    assert not _active_object_type_exists(foundry, "Restaurant")

    deleted = _mcp_native_call(
        client,
        app_id,
        headers,
        "delete-object",
        "ontology_editing",
        f"ontology-branch:{branch_id}",
        "delete_foundry_object_type",
        {"apiName": "Restaurant", "changeSummary": "Remove Restaurant"},
    )
    assert deleted["changeSummary"] == "Remove Restaurant"
    assert foundry.ontology.branch_diff(branch_id, ctx=FDE_USER)["resources"] == []


def test_official_palantir_data_connection_tools_use_native_governed_sources(foundry: Any, monkeypatch: Any) -> None:
    app_id, headers = _builder_mcp_application(foundry, monkeypatch, "data_connection")
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    rest_source = _mcp_native_call(
        client,
        app_id,
        headers,
        "create-rest-source",
        "data_connection",
        "tenant:tenant-demo",
        "create_foundry_rest_api_data_source",
        {
            "sourceName": "travel_api",
            "displayName": "Travel API",
            "baseUrl": "https://travel.example.test",
            "auth": {"mode": "none"},
            "resourceName": "restaurants",
            "resourcePath": "/restaurants",
            "datasetRef": "raw.travel_restaurants",
            "primaryKey": ["id"],
            "idempotencyKey": "official-rest-source",
        },
    )
    policy = _mcp_native_call(
        client,
        app_id,
        headers,
        "create-egress-policy",
        "data_connection",
        "tenant:tenant-demo",
        "get_or_create_network_egress_policy",
        {
            "policyName": "travel_api_egress",
            "displayName": "Travel API egress",
            "mode": "direct",
            "allowedHosts": ["travel.example.test:443"],
            "idempotencyKey": "official-egress-policy",
        },
    )
    webhook = _mcp_native_call(
        client,
        app_id,
        headers,
        "create-webhook",
        "data_connection",
        "tenant:tenant-demo",
        "create_foundry_rest_api_data_source_webhook",
        {
            "sourceName": "travel_booking_events",
            "displayName": "Travel booking events",
            "datasetRef": "raw.travel_booking_events",
            "connectorName": "travel_api",
            "resourceName": "booking_events",
            "signingSecretRef": "TRAVEL_BOOKING_WEBHOOK_SECRET",
            "inboundUrl": "https://foundry-lite.example.test/hooks/travel",
            "idempotencyKey": "official-webhook-source",
        },
    )
    viewed = _mcp_native_call(
        client,
        app_id,
        headers,
        "view-webhook",
        "data_connection",
        "source:travel_booking_events",
        "view_foundry_rest_api_data_source_webhook",
        {"sourceName": "travel_booking_events"},
    )

    assert rest_source["connection"]["connectorName"] == "travel_api"
    assert rest_source["resource"]["datasetRef"] == "raw.travel_restaurants"
    assert policy["allowedHosts"]["hosts"] == ["travel.example.test:443"]
    assert webhook["source"]["kind"] == "webhook_listener"
    assert viewed["kind"] == "webhook_listener"
    assert viewed["configFingerprint"] == webhook["source"]["configFingerprint"]


def _mcp_native_call(
    client: TestClient,
    app_id: str,
    headers: dict[str, str],
    rpc_id: str,
    mode: str,
    workspace_ref: str,
    tool_name: str,
    arguments: dict[str, object],
) -> dict[str, Any]:
    session_headers = _builder_session_headers(client, app_id, headers)
    payload = _mcp_tool_call_payload(rpc_id, mode, workspace_ref, tool_name, arguments)
    response = client.post(f"/mcp/builder/{app_id}", headers=session_headers, json=payload)
    response_payload = response.json()
    structured = response_payload.get("result", {}).get("structuredContent", {})
    if structured.get("status") == "approval_required":
        receipt = _approve_mcp_challenge(client, app_id, str(structured["challengeId"]), headers)
        payload["params"]["arguments"]["confirmationReceipt"] = receipt
        response = client.post(f"/mcp/builder/{app_id}", headers=session_headers, json=payload)
        response_payload = response.json()
    assert response.status_code == 200
    assert "error" not in response_payload, response_payload
    return dict(response_payload["result"]["structuredContent"])


def _concurrent_mcp_posts(
    client: TestClient,
    app_id: str,
    headers: dict[str, str],
    payload: dict[str, Any],
) -> list[Any]:
    def post_once(_: int) -> Any:
        return client.post(f"/mcp/builder/{app_id}", headers=headers, json=payload)

    with ThreadPoolExecutor(max_workers=2) as executor:
        return list(executor.map(post_once, range(2)))


def _assert_concurrent_result_or_conflict(responses: list[Any]) -> str:
    assert all(response.status_code == 200 for response in responses)
    bodies = [response.json() for response in responses]
    results = [body["result"] for body in bodies if "result" in body]
    assert results
    for body in bodies:
        if "error" in body:
            assert body["error"]["data"]["type"] == "CONFLICT"
    return str(results[0]["aiRunId"])


def _builder_session_headers(client: TestClient, app_id: str, headers: dict[str, str]) -> dict[str, str]:
    clean_headers = {
        key: value
        for key, value in headers.items()
        if key.lower() != "x-fde-confirm-tool" and not key.lower().startswith("x-test-")
    }
    initialized = client.post(
        f"/mcp/builder/{app_id}",
        headers=clean_headers,
        json={
            "jsonrpc": "2.0",
            "id": "initialize-helper",
            "method": "initialize",
            "params": _mcp_initialize_params(),
        },
    )
    assert initialized.status_code == 200
    return {**clean_headers, "Mcp-Session-Id": initialized.headers["Mcp-Session-Id"]}


def _mcp_initialize_params(protocol_version: str = "2025-06-18") -> dict[str, object]:
    return {
        "protocolVersion": protocol_version,
        "capabilities": {},
        "clientInfo": {"name": "foundry-lite-builder-test", "version": "1.0.0"},
    }


def _mcp_tool_call_payload(
    rpc_id: str,
    mode: str,
    workspace_ref: str,
    tool_name: str,
    arguments: dict[str, object],
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": {"mode": mode, "workspaceRef": workspace_ref, "arguments": arguments},
        },
    }


def _approve_mcp_challenge(
    client: TestClient,
    app_id: str,
    challenge_id: str,
    headers: dict[str, str],
) -> str:
    response = client.post(
        f"/api/aip/fde/mcp/{app_id}/confirmations/{challenge_id}/approve",
        headers=_control_headers(headers),
    )
    assert response.status_code == 200, response.text
    return str(response.json()["confirmationReceipt"])


def _control_headers(headers: dict[str, str]) -> dict[str, str]:
    authorization = headers.get("X-Test-Control-Authorization")
    assert authorization is not None
    return {"Authorization": authorization, "X-Request-ID": "builder-human-control"}


def _raw_mcp_native_call(
    client: TestClient,
    app_id: str,
    headers: dict[str, str],
    rpc_id: str,
    branch_id: str,
    tool_name: str,
    arguments: dict[str, object],
) -> dict[str, Any]:
    session_headers = _builder_session_headers(client, app_id, headers)
    response = client.post(
        f"/mcp/builder/{app_id}",
        headers=session_headers,
        json={
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": {
                    "mode": "ontology_editing",
                    "workspaceRef": f"ontology-branch:{branch_id}",
                    "arguments": arguments,
                },
            },
        },
    )
    assert response.status_code == 200
    return dict(response.json())


def _seed_official_mcp_objects(foundry: Any, tmp_path: Any) -> None:
    csv_path = tmp_path / "official-mcp-orders.csv"
    csv_path.write_text("order_id,status\nMCP-ORDER-1,PENDING\nMCP-ORDER-2,PENDING\n", encoding="utf-8")
    foundry.datasets.ensure("clean.official_mcp_orders", primary_key=["order_id"], ctx=FDE_USER)
    foundry.datasets.upload_csv("clean.official_mcp_orders", str(csv_path), ctx=FDE_USER)
    foundry.ontology.apply_text(
        """
objectTypes:
  - apiName: Order
    primaryKey: orderId
    backing:
      dataset: clean.official_mcp_orders
      mode: snapshot
      primaryKeyColumns: [order_id]
    properties:
      - {apiName: orderId, column: order_id, type: string, nullable: false}
      - {apiName: status, column: status, type: string}
actionTypes: []
linkTypes: []
""",
        ctx=FDE_USER,
    )
    foundry.objects.reindex("Order", ctx=FDE_USER)


def _mcp_mutation_base_ontology() -> str:
    return """
objectTypes:
  - apiName: Order
    primaryKey: orderId
    backing: {dataset: clean.mcp_mutation_orders, mode: snapshot, primaryKeyColumns: [order_id]}
    properties:
      - {apiName: orderId, column: order_id, type: string, nullable: false}
      - {apiName: customerId, column: customer_id, type: string}
      - {apiName: status, column: status, type: string, editable: true}
  - apiName: Customer
    primaryKey: customerId
    backing: {dataset: clean.mcp_mutation_customers, mode: snapshot, primaryKeyColumns: [customer_id]}
    properties:
      - {apiName: customerId, column: customer_id, type: string, nullable: false}
      - {apiName: name, column: name, type: string}
linkTypes: []
actionTypes: []
"""


def _mcp_link_definition() -> dict[str, object]:
    return {
        "apiName": "OrderCustomer",
        "from": "Order",
        "to": "Customer",
        "cardinality": "many_to_one",
        "backing": {
            "dataset": "clean.mcp_mutation_orders",
            "fromKey": "order_id",
            "toKey": "customer_id",
        },
    }


def _mcp_action_definition() -> dict[str, object]:
    return {
        "apiName": "ApproveOrder",
        "target": "Order",
        "parameters": [{"apiName": "reason", "type": "string", "required": True}],
        "permissions": {"allowedRoles": ["data_engineer"]},
        "mutations": [{"type": "setProperty", "property": "status", "value": "APPROVED"}],
    }


def _mcp_marker_object_definition() -> dict[str, object]:
    return {
        "apiName": "McpMutationMarker",
        "primaryKey": "id",
        "backing": {
            "dataset": "clean.mcp_mutation_orders",
            "mode": "snapshot",
            "primaryKeyColumns": ["order_id"],
        },
        "properties": [{"apiName": "id", "column": "order_id", "type": "string", "nullable": False}],
    }


def _mcp_pipeline_graph() -> dict[str, object]:
    columns = [
        {"name": "order_id", "type": "string", "nullable": False},
        {"name": "amount", "type": "int", "nullable": True},
    ]
    return {
        "nodes": [
            {
                "id": "raw_orders",
                "type": "dataset",
                "config": {"datasetRef": "raw.mcp_pipeline_orders", "schema": columns},
            },
            {
                "id": "clean_sql",
                "type": "sql",
                "config": {
                    "sql": "select order_id, amount from {{ input('raw.mcp_pipeline_orders') }}",
                    "outputDatasetRef": "work.mcp_pipeline_orders",
                    "schema": columns,
                },
            },
            {
                "id": "out",
                "type": "output_dataset",
                "config": {"outputDatasetRef": "clean.mcp_pipeline_orders"},
            },
        ],
        "edges": [
            {"source": "raw_orders", "target": "clean_sql"},
            {"source": "clean_sql", "target": "out"},
        ],
        "layout": {
            "raw_orders": {"x": 0, "y": 0},
            "clean_sql": {"x": 260, "y": 0},
            "out": {"x": 520, "y": 0},
        },
        "outputContract": {"columns": columns},
        "tests": [{"name": "schema contract", "expected": {"columns": columns}}],
        "schedule": {"kind": "manual"},
    }


def _tool(name: str, arguments: dict[str, object]) -> ModelResponse:
    return ModelResponse(
        provider="fake",
        resolved_model_id="",
        resolved_model_revision="",
        content="Use governed tool.",
        finish_reason="tool_calls",
        input_tokens=4,
        output_tokens=4,
        normalized_tool_calls=(ModelToolCall(tool_name=name, arguments_json=json.dumps(arguments)),),
        provider_request_id=f"call-{name}",
    )


def _tool_rows(foundry: Any, ai_run_id: str) -> list[dict[str, object]]:
    with foundry.engine.begin() as conn:
        rows = conn.execute(
            select(db.ai_tool_calls)
            .where(db.ai_tool_calls.c.ai_run_id == ai_run_id)
            .order_by(db.ai_tool_calls.c.sequence)
        ).mappings()
        return [dict(row) for row in rows]


def _active_object_type_exists(foundry: Any, api_name: str) -> bool:
    with foundry.engine.begin() as transaction:
        count = transaction.execute(
            select(func.count()).select_from(db.object_types).where(db.object_types.c.api_name == api_name)
        ).scalar_one()
    return int(count) > 0


def _api_headers() -> dict[str, str]:
    return {
        "X-Tenant-ID": "tenant-demo",
        "X-User-ID": "fde-platform-user",
        "X-Roles": "data_engineer",
        "X-Request-ID": "req-fde-platform-api",
    }


def _builder_user_oauth_headers(
    foundry: Any,
    monkeypatch: Any,
    app_id: str,
    scopes: tuple[str, ...],
    suffix: str,
) -> dict[str, str]:
    client_id = f"builder-user-{suffix}"
    redirect_uri = f"https://chat.example.test/oauth/{suffix}"
    foundry.developer_console.create_osdk_application_client(
        app_id,
        client_id=client_id,
        redirect_uris=(redirect_uri,),
        allowed_scopes=scopes,
        idempotency_key=f"{client_id}-public-client",
        ctx=FDE_USER,
    )
    resource = f"http://testserver/mcp/builder/{app_id}"
    token = _builder_oauth_token(
        foundry,
        app_id=app_id,
        client_id=client_id,
        redirect_uri=redirect_uri,
        scopes=scopes,
        verifier=f"foundry-lite-builder-user-{suffix}-verifier",
        resource=resource,
        ctx=FDE_USER,
    )
    issuer = foundry._services.osdk_oauth_sessions.oauth_token_issuer
    provider = JwtOidcAuthProvider(
        JwtOidcAuthConfig(issuer=issuer.issuer, audience=issuer.audience, jwks=issuer.public_jwks())
    )
    monkeypatch.setattr(api_runtime, "get_auth_provider", lambda: provider)
    return {
        "Authorization": f"Bearer {token['accessToken']}",
        "MCP-Protocol-Version": "2025-06-18",
        "X-Request-ID": f"builder-{suffix}",
        "X-Test-Control-Authorization": f"Bearer {_human_control_token(issuer)}",
    }


def _builder_oauth_token(
    foundry: Any,
    *,
    app_id: str,
    client_id: str,
    redirect_uri: str,
    scopes: tuple[str, ...],
    verifier: str,
    resource: str | None,
    ctx: RequestContext,
) -> dict[str, object]:
    authorization = foundry.auth.osdk_oauth_authorize(
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=_s256(verifier),
        scopes=scopes,
        resource=resource,
        resource_application_id=app_id,
        ctx=ctx,
    )
    return foundry.auth.osdk_oauth_token(
        client_id=client_id,
        code=str(authorization["code"]),
        redirect_uri=redirect_uri,
        code_verifier=verifier,
        resource=resource,
        resource_application_id=app_id,
        ctx=ctx,
    )


def _human_control_token(issuer: Any) -> str:
    issued_at = int(time.time())
    return jwt.encode(
        {
            "iss": issuer.issuer,
            "aud": issuer.audience,
            "iat": issued_at,
            "exp": issued_at + 300,
            "tenant_id": FDE_USER.tenant_id,
            "sub": "builder-human-approver",
            "roles": list(FDE_USER.roles),
            "jti": f"builder-human-{uuid4().hex}",
        },
        issuer.private_key,
        algorithm="RS256",
        headers={"kid": issuer.key_id},
    )


def _builder_mcp_application(
    foundry: Any,
    monkeypatch: Any,
    mode: str,
    additional_resources: tuple[dict[str, object], ...] = (),
) -> tuple[str, dict[str, str]]:
    scope = f"osdk:connector:fde_{mode}:execute"
    extra_scopes = [str(item) for resource in additional_resources for item in resource.get("scopes", [])]
    suffix = mode.replace("_", "-")
    application = foundry.developer_console.create_osdk_application(
        app_api_name=f"FdeMcp{mode.title().replace('_', '')}",
        display_name=f"FDE MCP {mode}",
        client_id=f"client-fde-mcp-{suffix}",
        resources=[
            {"resourceType": "connector", "resourceApiName": f"fde_{mode}", "scopes": [scope]},
            *additional_resources,
        ],
        idempotency_key=f"fde-mcp-{suffix}-app",
        ctx=FDE_USER,
    )
    app_id = str(application["application"]["id"])
    return app_id, _builder_user_oauth_headers(
        foundry,
        monkeypatch,
        app_id,
        tuple([scope, *extra_scopes]),
        suffix,
    )


def _mcp_patch_call(branch_id: str, rpc_id: str) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": "tools/call",
        "params": {
            "name": "ontology.branch.apply_patch",
            "arguments": {
                "mode": "ontology_editing",
                "workspaceRef": f"ontology-branch:{branch_id}",
                "arguments": {
                    "upsertResources": [
                        {
                            "kind": "objectType",
                            "definition": {
                                "apiName": "Restaurant",
                                "primaryKey": "id",
                                "backing": {"dataset": "clean.restaurants"},
                                "properties": [
                                    {
                                        "apiName": "id",
                                        "column": "id",
                                        "type": "string",
                                        "nullable": False,
                                        "indexed": True,
                                    },
                                    {"apiName": "name", "column": "name", "type": "string"},
                                ],
                            },
                        }
                    ],
                    "deleteResources": [],
                    "changeSummary": "Add Restaurant",
                },
            },
        },
    }


def _s256(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
