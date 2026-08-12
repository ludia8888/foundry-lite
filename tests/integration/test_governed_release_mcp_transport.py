"""Governed Release MCP authorization, app resource, and tool wire contract."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from foundry_lite.application.services.aip.governed_release_authorization import GOVERNED_RELEASE_SCOPE
from foundry_lite.application.services.aip.governed_release_catalog import GOVERNED_RELEASE_UI_RESOURCE_URI
from foundry_lite.application.services.aip.governed_release_mcp_types import GovernedReleaseMcpToolCall
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied, ValidationFailed
from foundry_lite.infrastructure.auth import JwtOidcAuthConfig, JwtOidcAuthProvider
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.main import app
from foundry_lite_api.mcp_authorization import mcp_resource_scopes
from foundry_lite_api.mcp_authorization_config import McpAuthorizationConfig
from foundry_lite_api.routers import release_mcp as release_mcp_router
from jwt.algorithms import RSAAlgorithm

RELEASE_USER = RequestContext(
    tenant_id="tenant-demo",
    actor_user_id="governed-release-user",
    roles=("admin", "data_engineer"),
    request_id="req-governed-release-transport",
)
_RELEASE_SCOPE = GOVERNED_RELEASE_SCOPE


def test_governed_release_widget_uri_is_content_addressed() -> None:
    html_sha256 = hashlib.sha256(release_mcp_router._RELEASE_CONSOLE_PATH.read_bytes()).hexdigest()

    assert release_mcp_router.RELEASE_CONSOLE_RESOURCE_URI == GOVERNED_RELEASE_UI_RESOURCE_URI
    assert release_mcp_router.RELEASE_CONSOLE_RESOURCE_URI.endswith(f"-{html_sha256[:12]}.html")
    assert "ui://foundry-lite/governed-release-v8-04c14f7f069c.html" in (
        release_mcp_router.LEGACY_RELEASE_CONSOLE_RESOURCE_URIS
    )


def test_release_mcp_rejects_foreign_session_namespaces_on_every_transport_method(
    foundry: Any,
    monkeypatch: Any,
) -> None:
    app_id, headers, _builder_headers = _release_application(foundry, monkeypatch, suffix="namespace")
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    path = f"/mcp/release/{app_id}"

    for session_id in ("mcp-builder-foreign-0001", "ontology-mcp-foreign-0001"):
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
            "release_session_namespace_required"
        }


def test_governed_release_mcp_binds_oauth_resource_and_serves_embedded_app(
    foundry: Any,
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    assert release_mcp_router._RELEASE_CONSOLE_PATH.name == "index.html"
    assert release_mcp_router._RELEASE_CONSOLE_PATH.is_file()
    app_id, release_headers, builder_headers = _release_application(foundry, monkeypatch)
    calls: list[dict[str, object]] = []
    sessions = _install_release_transport_gateway(foundry, monkeypatch, calls)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)

    metadata = client.get(f"/.well-known/oauth-protected-resource/mcp/release/{app_id}")
    unauthorized = client.post(
        f"/mcp/release/{app_id}",
        headers={"MCP-Protocol-Version": "2025-06-18"},
        json=_initialize_payload("unauthorized"),
    )
    wrong_audience = client.post(
        f"/mcp/release/{app_id}",
        headers=builder_headers,
        json=_initialize_payload("wrong-audience"),
    )
    wrong_application = client.post(
        "/mcp/release/not-the-token-application",
        headers=release_headers,
        json=_initialize_payload("wrong-application"),
    )
    initialized = client.post(
        f"/mcp/release/{app_id}",
        headers=release_headers,
        json=_initialize_payload("release-initialize"),
    )

    assert metadata.status_code == 200
    assert metadata.json() == {
        "resource": f"http://testserver/mcp/release/{app_id}",
        "authorization_servers": [foundry.auth.osdk_oauth_issuer()],
        "bearer_methods_supported": ["header"],
        "scopes_supported": [_RELEASE_SCOPE],
    }
    assert mcp_resource_scopes(app_id, "builder") == (_RELEASE_SCOPE,)
    assert mcp_resource_scopes(app_id, "release") == (_RELEASE_SCOPE,)
    assert unauthorized.status_code == 401
    assert f"/mcp/release/{app_id}" in unauthorized.headers["WWW-Authenticate"]
    assert wrong_audience.status_code == 401
    assert wrong_application.status_code == 401
    assert initialized.status_code == 200, initialized.text
    assert initialized.json()["result"]["capabilities"] == {
        "tools": {"listChanged": True},
        "resources": {"subscribe": False, "listChanged": False},
    }
    session_id = initialized.headers["Mcp-Session-Id"]
    assert session_id in sessions
    session_headers = {**release_headers, "Mcp-Session-Id": session_id}
    acknowledged = client.post(
        f"/mcp/release/{app_id}",
        headers=session_headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
    )
    pinged = client.post(
        f"/mcp/release/{app_id}",
        headers=session_headers,
        json={"jsonrpc": "2.0", "id": "ping", "method": "ping", "params": {}},
    )
    assert acknowledged.status_code == 202 and not acknowledged.content
    assert pinged.status_code == 200 and pinged.json()["result"] == {}

    listed_tools = client.post(
        f"/mcp/release/{app_id}",
        headers=session_headers,
        json={"jsonrpc": "2.0", "id": "tools", "method": "tools/list", "params": {}},
    )
    listed_resources = client.post(
        f"/mcp/release/{app_id}",
        headers=session_headers,
        json={"jsonrpc": "2.0", "id": "resources", "method": "resources/list", "params": {}},
    )

    assert listed_tools.status_code == 200, listed_tools.text
    tool = listed_tools.json()["result"]["tools"][0]
    assert tool["name"] == "release.prepare"
    assert tool["_meta"]["ui"]["resourceUri"] == release_mcp_router.RELEASE_CONSOLE_RESOURCE_URI
    assert tool["_meta"]["openai/outputTemplate"] == release_mcp_router.RELEASE_CONSOLE_RESOURCE_URI
    assert tool["_meta"]["openai/widgetAccessible"] is True
    assert tool["_meta"]["ui"]["visibility"] == ["app"]
    assert tool["_meta"]["openai/visibility"] == "private"
    expected_security = [{"type": "oauth2", "scopes": [_RELEASE_SCOPE]}]
    assert tool["securitySchemes"] == expected_security
    assert tool["_meta"]["securitySchemes"] == expected_security
    assert listed_resources.status_code == 200, listed_resources.text
    resource = listed_resources.json()["result"]["resources"][0]
    assert resource["uri"] == release_mcp_router.RELEASE_CONSOLE_RESOURCE_URI
    assert resource["mimeType"] == "text/html;profile=mcp-app"
    assert resource["_meta"]["ui"]["csp"] == {"connectDomains": [], "resourceDomains": []}

    asset = tmp_path / "release-console.html"
    asset.write_text("<!doctype html><title>Governed Release Console</title>", encoding="utf-8")
    monkeypatch.setattr(release_mcp_router, "_RELEASE_CONSOLE_PATH", asset)
    read = client.post(
        f"/mcp/release/{app_id}",
        headers=session_headers,
        json={
            "jsonrpc": "2.0",
            "id": "resource-read",
            "method": "resources/read",
            "params": {"uri": release_mcp_router.RELEASE_CONSOLE_RESOURCE_URI},
        },
    )
    legacy_uris = [
        *(f"ui://foundry-lite/governed-release-{version}.html" for version in ("v2", "v3", "v4")),
        "ui://foundry-lite/governed-release-v5-25a98896119d.html",
        "ui://foundry-lite/governed-release-v6-f2bef02fe8ee.html",
        "ui://foundry-lite/governed-release-v7-dcca665d29a3.html",
        "ui://foundry-lite/governed-release-v8-04c14f7f069c.html",
    ]
    legacy_reads = [
        client.post(
            f"/mcp/release/{app_id}",
            headers=session_headers,
            json={
                "jsonrpc": "2.0",
                "id": f"legacy-resource-read-{index}",
                "method": "resources/read",
                "params": {"uri": uri},
            },
        )
        for index, uri in enumerate(legacy_uris, start=1)
    ]
    called = client.post(
        f"/mcp/release/{app_id}",
        headers=session_headers,
        json={
            "jsonrpc": "2.0",
            "id": "prepare-release",
            "method": "tools/call",
            "params": {
                "name": "release.prepare",
                "arguments": {
                    "releaseKind": "pipeline",
                    "proposalId": "pipeline-proposal-1",
                    "widgetConfirmationToken": "widget-secret-token",
                },
            },
        },
    )

    assert read.status_code == 200, read.text
    content = read.json()["result"]["contents"][0]
    assert content["text"].startswith("<!doctype html>")
    assert content["mimeType"] == "text/html;profile=mcp-app"
    for legacy_read in legacy_reads:
        assert legacy_read.status_code == 200, legacy_read.text
        assert legacy_read.json()["result"]["contents"][0]["text"] == content["text"]
    assert called.status_code == 200, called.text
    assert called.json()["result"]["structuredContent"]["release"]["status"] == "prepared"
    assert calls == [
        {
            "application_id": app_id,
            "session_id": session_id,
            "json_rpc_id": "prepare-release",
            "tool_name": "release.prepare",
            "arguments": {"releaseKind": "pipeline", "proposalId": "pipeline-proposal-1"},
            "widget_confirmation_token": "widget-secret-token",
            "origin": None,
        }
    ]

    streamed = client.get(f"/mcp/release/{app_id}", headers=session_headers)
    assert streamed.status_code == 200
    assert streamed.headers["content-type"].startswith("text/event-stream")
    assert streamed.text == ": heartbeat\n\n"

    closed = client.delete(f"/mcp/release/{app_id}", headers=session_headers)
    assert closed.status_code == 204
    assert session_id not in sessions


def test_status_tool_and_authenticated_endpoint_expose_fail_closed_live_readiness(
    foundry: Any,
    monkeypatch: Any,
) -> None:
    app_id, headers, _builder_headers = _release_application(foundry, monkeypatch, suffix="readiness")
    calls: list[dict[str, object]] = []
    _install_release_transport_gateway(foundry, monkeypatch, calls)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    readiness = {
        "schema_version": "governed-release-live-readiness/v2",
        "application_id": app_id,
        "status": "ready_for_live_run",
        "is_ready_for_live_run": True,
        "is_live_verified": False,
        "checks": [],
        "blockers": ["authentic_live_collector_required"],
    }
    monkeypatch.setattr(
        foundry.release,
        "release_live_readiness",
        lambda _application_id, ctx=None: readiness,
    )
    client = TestClient(app)
    endpoint = f"/mcp/release/{app_id}"
    initialized = client.post(endpoint, headers=headers, json=_initialize_payload("readiness-initialize"))
    session_headers = {**headers, "Mcp-Session-Id": initialized.headers["Mcp-Session-Id"]}

    status = client.post(
        endpoint,
        headers=session_headers,
        json={
            "jsonrpc": "2.0",
            "id": "readiness-status",
            "method": "tools/call",
            "params": {
                "name": "get_release_status",
                "arguments": {"releaseKind": "pipeline", "proposalId": "pipeline-proposal-1"},
            },
        },
    )
    unauthorized = client.get(f"{endpoint}/live-readiness")
    direct = client.get(f"{endpoint}/live-readiness", headers=headers)

    assert status.status_code == 200
    embedded = status.json()["result"]["structuredContent"]["release"]["liveReadiness"]
    assert embedded == readiness
    assert "authentic_live_collector_required" in status.json()["result"]["content"][0]["text"]
    assert unauthorized.status_code == 401
    assert direct.status_code == 200
    assert direct.headers["cache-control"] == "no-store"
    assert direct.json() == readiness


def test_governed_release_transport_accepts_external_oidc_human_and_public_https_audience(
    foundry: Any,
    monkeypatch: Any,
) -> None:
    app_id, _local_headers, _builder_headers = _release_application(foundry, monkeypatch, suffix="external")
    calls: list[dict[str, object]] = []
    sessions = _install_release_transport_gateway(foundry, monkeypatch, calls)
    captured_contexts: list[RequestContext] = []
    public_base = "https://foundry.example.test"
    issuer = "https://identity.example.test/tenant"
    provider, token = _external_oidc_provider_and_token(
        issuer=issuer,
        audience=f"{public_base}/mcp/release/{app_id}",
    )

    def open_session(_application_id: str, *, session_id: str, ctx: RequestContext) -> None:
        captured_contexts.append(ctx)
        sessions.add(session_id)

    monkeypatch.setattr(api_runtime, "foundry", foundry)
    monkeypatch.setattr(api_runtime, "get_auth_provider", lambda: provider)
    monkeypatch.setattr(
        api_runtime,
        "get_mcp_authorization_config",
        lambda: McpAuthorizationConfig(
            external_authorization_server=issuer,
            public_base_url=public_base,
            governed_release_application_id=app_id,
        ),
    )
    monkeypatch.setattr(foundry.release, "open_release_mcp_session", open_session, raising=False)
    client = TestClient(app)
    headers = {
        "Authorization": f"Bearer {token}",
        "MCP-Protocol-Version": "2025-06-18",
    }

    metadata = client.get(f"/.well-known/oauth-protected-resource/mcp/release/{app_id}")
    initialized = client.post(
        f"/mcp/release/{app_id}",
        headers=headers,
        json=_initialize_payload("external-human"),
    )
    wrong_metadata = client.get("/.well-known/oauth-protected-resource/mcp/release/other-app")
    wrong_application = client.post(
        "/mcp/release/other-app",
        headers=headers,
        json=_initialize_payload("external-wrong-app"),
    )

    assert metadata.status_code == 200
    assert metadata.json()["resource"] == f"{public_base}/mcp/release/{app_id}"
    assert metadata.json()["authorization_servers"] == [issuer]
    assert initialized.status_code == 200, initialized.text
    assert wrong_metadata.status_code == 404
    assert wrong_application.status_code == 401
    assert wrong_application.headers["WWW-Authenticate"].startswith(
        f'Bearer resource_metadata="{public_base}/.well-known/oauth-protected-resource/'
    )
    assert len(captured_contexts) == 1
    context = captured_contexts[0]
    assert context.application_id == app_id
    assert context.client_id == "https://chatgpt.com/oauth/release/client.json"
    assert context.oauth_session_authority == "issuer"
    assert context.oauth_session_id is not None and context.oauth_session_id.startswith("issuer-session:")
    assert "external-raw-session-id" not in context.oauth_session_id
    assert context.oauth_session_hash is not None
    assert "external-raw-session-id" not in context.oauth_session_hash
    assert context.oauth_grant_type == "authorization_code"
    assert context.oauth_resource == f"{public_base}/mcp/release/{app_id}"
    assert isinstance(context.oauth_token_issued_at, int)
    assert isinstance(context.oauth_token_expires_at, int)
    assert context.is_human_oauth is True


def test_governed_release_tool_permission_failure_triggers_safe_oauth_relink(
    foundry: Any,
    monkeypatch: Any,
) -> None:
    app_id, release_headers, _builder_headers = _release_application(foundry, monkeypatch, suffix="relink")
    _install_release_transport_gateway(foundry, monkeypatch, [])
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    initialized = client.post(
        f"/mcp/release/{app_id}",
        headers=release_headers,
        json=_initialize_payload("initialize-relink"),
    )
    session_headers = {**release_headers, "Mcp-Session-Id": initialized.headers["Mcp-Session-Id"]}

    cases = (
        ("oauth_access_session", "invalid_token", "An active human OAuth session is required."),
        ("release_scope", "insufficient_scope", "The required governed release OAuth scope is missing."),
    )
    for index, (resource, expected_error, expected_description) in enumerate(cases):

        def deny_tool(
            _call: GovernedReleaseMcpToolCall,
            *,
            ctx: RequestContext,
            denied_resource: str = resource,
        ) -> dict[str, object]:
            del ctx
            raise PermissionDenied(
                "private-token-value must never reach the MCP client",
                details={"resource": denied_resource, "private": "private-error-detail"},
            )

        monkeypatch.setattr(foundry.release, "run_release_mcp_tool", deny_tool, raising=False)
        response = client.post(
            f"/mcp/release/{app_id}",
            headers=session_headers,
            json={
                "jsonrpc": "2.0",
                "id": f"relink-{index}",
                "method": "tools/call",
                "params": {"name": "release.prepare", "arguments": {}},
            },
        )

        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert result["isError"] is True
        assert result["content"] == [
            {"type": "text", "text": "Authentication is required before this release tool can run."}
        ]
        challenge = result["_meta"]["mcp/www_authenticate"][0]
        metadata = f"http://testserver/.well-known/oauth-protected-resource/mcp/release/{app_id}"
        assert f'resource_metadata="{metadata}"' in challenge
        assert f'error="{expected_error}"' in challenge
        assert f'error_description="{expected_description}"' in challenge
        assert "private-token-value" not in response.text
        assert "private-error-detail" not in response.text


def test_governed_release_mcp_resource_read_reports_missing_widget_clearly(
    foundry: Any,
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    app_id, release_headers, _builder_headers = _release_application(foundry, monkeypatch, suffix="missing")
    _install_release_transport_gateway(foundry, monkeypatch, [])
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    missing_path = tmp_path / "not-installed.html"
    monkeypatch.setattr(release_mcp_router, "_RELEASE_CONSOLE_PATH", missing_path)
    client = TestClient(app)
    initialized = client.post(
        f"/mcp/release/{app_id}",
        headers=release_headers,
        json=_initialize_payload("initialize-missing-widget"),
    )
    session_headers = {**release_headers, "Mcp-Session-Id": initialized.headers["Mcp-Session-Id"]}

    response = client.post(
        f"/mcp/release/{app_id}",
        headers=session_headers,
        json={
            "jsonrpc": "2.0",
            "id": "missing-widget",
            "method": "resources/read",
            "params": {"uri": release_mcp_router.RELEASE_CONSOLE_RESOURCE_URI},
        },
    )

    assert response.status_code == 200
    error = response.json()["error"]
    assert error["code"] == -32001
    assert error["data"]["type"] == "NOT_FOUND"
    assert error["data"]["expectedAsset"] == "apps/chatgpt-release-widget/index.html"


def test_governed_release_oauth_rejects_application_without_dedicated_scope(
    foundry: Any,
    monkeypatch: Any,
) -> None:
    unrelated_scope = "osdk:connector:unrelated_release:execute"
    client_id = "unrelated-release-client"
    redirect_uri = "https://chat.example.test/unrelated-release"
    created = foundry.developer_console.create_osdk_application(
        app_api_name="UnrelatedReleaseScope",
        display_name="Unrelated release scope",
        resources=[
            {
                "resourceType": "connector",
                "resourceApiName": "unrelated_release",
                "scopes": [unrelated_scope],
            }
        ],
        idempotency_key="unrelated-release-application",
        ctx=RELEASE_USER,
    )
    app_id = str(created["application"]["id"])
    foundry.developer_console.create_osdk_application_client(
        app_id,
        client_id=client_id,
        redirect_uris=(redirect_uri,),
        allowed_scopes=(unrelated_scope,),
        idempotency_key="unrelated-release-client",
        ctx=RELEASE_USER,
    )
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    monkeypatch.setattr(api_runtime, "get_auth_provider", lambda: _strict_provider(foundry))
    client = TestClient(app)

    protected = client.get(f"/.well-known/oauth-protected-resource/mcp/release/{app_id}")
    authorization = client.get(
        "/api/auth/osdk/oauth/authorize",
        headers=_human_headers(foundry),
        params={
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": _s256("unrelated-release-verifier"),
            "code_challenge_method": "S256",
            "response_type": "code",
            "resource": f"http://testserver/mcp/release/{app_id}",
            "scope": unrelated_scope,
        },
        follow_redirects=False,
    )

    assert mcp_resource_scopes(app_id, "builder") == (unrelated_scope,)
    assert mcp_resource_scopes(app_id, "release") == ()
    assert protected.status_code == 404
    assert authorization.status_code == 403
    assert authorization.json()["detail"]["message"] == (
        "OSDK OAuth application has no scopes for the requested MCP resource"
    )


def test_governed_release_mcp_real_gateway_lists_and_prepares_widget_action(
    foundry: Any,
    monkeypatch: Any,
) -> None:
    app_id, release_headers, _builder_headers = _release_application(foundry, monkeypatch, suffix="real")
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    initialized = client.post(
        f"/mcp/release/{app_id}",
        headers=release_headers,
        json=_initialize_payload("real-release-initialize"),
    )
    assert initialized.status_code == 200, initialized.text
    assert "error" not in initialized.json(), initialized.text
    session_headers = {**release_headers, "Mcp-Session-Id": initialized.headers["Mcp-Session-Id"]}

    listed = client.post(
        f"/mcp/release/{app_id}",
        headers=session_headers,
        json={"jsonrpc": "2.0", "id": "real-tools", "method": "tools/list", "params": {}},
    )
    resource_read = client.post(
        f"/mcp/release/{app_id}",
        headers=session_headers,
        json={
            "jsonrpc": "2.0",
            "id": "real-resource",
            "method": "resources/read",
            "params": {"uri": release_mcp_router.RELEASE_CONSOLE_RESOURCE_URI},
        },
    )
    prepared = client.post(
        f"/mcp/release/{app_id}",
        headers=session_headers,
        json={
            "jsonrpc": "2.0",
            "id": "real-prepare",
            "method": "tools/call",
            "params": {
                "name": "prepare_release_action",
                "arguments": {
                    "targetTool": "submit_release_decision",
                    "arguments": {
                        "releaseKind": "ontology",
                        "proposalId": "ontology-proposal-for-binding-only",
                        "decision": "approve",
                        "idempotencyKey": "real-prepare-release-decision",
                    },
                },
            },
        },
    )

    assert listed.status_code == 200, listed.text
    tools = {tool["name"]: tool for tool in listed.json()["result"]["tools"]}
    assert set(tools) == {
        "open_release_workspace",
        "create_release_branch",
        "publish_release_candidate",
        "list_release_inbox",
        "assign_release_reviewer",
        "get_release_candidate",
        "prepare_release_action",
        "submit_release_decision",
        "execute_approved_release",
        "deploy_release",
        "get_release_status",
        "rollback_release",
        "verify_release_completion",
    }
    assert tools["submit_release_decision"]["_meta"]["ui"]["visibility"] == ["app"]
    expected_security = [{"type": "oauth2", "scopes": [_RELEASE_SCOPE]}]
    assert all(tool["securitySchemes"] == expected_security for tool in tools.values())
    assert all(tool["_meta"]["securitySchemes"] == expected_security for tool in tools.values())
    decision_schema = tools["submit_release_decision"]["inputSchema"]
    assert "widgetConfirmationToken" in decision_schema["properties"]
    assert "widgetConfirmationToken" not in decision_schema["required"]
    assert tools["submit_release_decision"]["annotations"]["idempotentHint"] is True
    assert tools["prepare_release_action"]["annotations"]["idempotentHint"] is False
    verification_schema = tools["verify_release_completion"]["inputSchema"]
    assert verification_schema["additionalProperties"] is False
    assert verification_schema["required"] == [
        "ontologyWorkflowRunId",
        "pipelineWorkflowRunId",
        "idempotencyKey",
    ]
    assert set(verification_schema["properties"]) == {
        "ontologyWorkflowRunId",
        "pipelineWorkflowRunId",
        "idempotencyKey",
        "widgetConfirmationToken",
    }
    assert tools["verify_release_completion"]["_meta"]["ui"]["visibility"] == ["app"]
    assert resource_read.status_code == 200, resource_read.text
    widget_html = resource_read.json()["result"]["contents"][0]["text"]
    assert "globalThis.openai" in widget_html
    assert "prepare_release_action" in widget_html
    assert prepared.status_code == 200, prepared.text
    result = prepared.json()["result"]
    assert result["structuredContent"]["release"]["status"] == "prepared"
    assert result["structuredContent"]["release"]["targetTool"] == "submit_release_decision"
    assert result["_meta"]["widgetConfirmationToken"]
    assert "widgetConfirmationToken" not in str(result["structuredContent"])
    assert "widgetConfirmationToken" not in str(result["content"])


def test_governed_release_mcp_confirms_and_replays_across_rotated_transport_sessions(
    foundry: Any,
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    source = tmp_path / "host-rotated-transport-session.csv"
    source.write_text("order_id,status\nO-1,PENDING\n", encoding="utf-8")
    foundry.datasets.ensure("clean.host_rotated_orders", ctx=RELEASE_USER, primary_key=["order_id"])
    foundry.datasets.upload_csv("clean.host_rotated_orders", source, ctx=RELEASE_USER)
    foundry.ontology.apply_text(
        """
objectTypes:
  - apiName: HostRotatedOrder
    primaryKey: orderId
    backing:
      dataset: clean.host_rotated_orders
    properties:
      - apiName: orderId
        column: order_id
        type: string
        nullable: false
      - apiName: status
        column: status
        type: string
""",
        ctx=RELEASE_USER,
    )
    app_id, release_headers, _builder_headers = _release_application(foundry, monkeypatch, suffix="rotation")
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    endpoint = f"/mcp/release/{app_id}"

    def initialize(rpc_id: str) -> dict[str, str]:
        response = client.post(endpoint, headers=release_headers, json=_initialize_payload(rpc_id))
        assert response.status_code == 200, response.text
        return {**release_headers, "Mcp-Session-Id": response.headers["Mcp-Session-Id"]}

    prepare_headers = initialize("rotation-prepare-session")
    arguments = {
        "releaseKind": "ontology",
        "branchName": "host-rotated-transport-session",
        "idempotencyKey": "host-rotated-transport-session",
    }
    prepared = client.post(
        endpoint,
        headers=prepare_headers,
        json={
            "jsonrpc": "2.0",
            "id": "rotation-prepare",
            "method": "tools/call",
            "params": {
                "name": "prepare_release_action",
                "arguments": {"targetTool": "create_release_branch", "arguments": arguments},
            },
        },
    )
    assert prepared.status_code == 200, prepared.text
    token = prepared.json()["result"]["_meta"]["widgetConfirmationToken"]
    assert isinstance(token, str) and token

    action_headers = initialize("rotation-action-session")
    assert action_headers["Mcp-Session-Id"] != prepare_headers["Mcp-Session-Id"]
    created = client.post(
        endpoint,
        headers=action_headers,
        json={
            "jsonrpc": "2.0",
            "id": "rotation-create",
            "method": "tools/call",
            "params": {
                "name": "create_release_branch",
                "arguments": {**arguments, "widgetConfirmationToken": token},
            },
        },
    )
    assert created.status_code == 200, created.text
    created_result = created.json()["result"]
    assert created_result["isError"] is False, created_result
    assert created_result["structuredContent"]["release"]["stage"] == "branch_created"

    replay_headers = initialize("rotation-replay-session")
    assert replay_headers["Mcp-Session-Id"] not in {
        prepare_headers["Mcp-Session-Id"],
        action_headers["Mcp-Session-Id"],
    }
    replayed = client.post(
        endpoint,
        headers=replay_headers,
        json={
            "jsonrpc": "2.0",
            "id": "rotation-replay",
            "method": "tools/call",
            "params": {"name": "create_release_branch", "arguments": arguments},
        },
    )
    assert replayed.status_code == 200, replayed.text
    replayed_result = replayed.json()["result"]
    assert replayed_result["isError"] is False, replayed_result
    assert replayed_result["isReplayed"] is True
    assert replayed_result["aiRunId"] == created_result["aiRunId"]
    assert replayed_result["structuredContent"] == created_result["structuredContent"]


def _release_application(
    foundry: Any,
    monkeypatch: Any,
    *,
    suffix: str = "default",
) -> tuple[str, dict[str, str], dict[str, str]]:
    app_api_name = f"GovernedRelease{suffix.title()}"
    client_id = f"governed-release-{suffix}-client"
    redirect_uri = f"https://chat.example.test/governed-release/{suffix}"
    created = foundry.developer_console.create_osdk_application(
        app_api_name=app_api_name,
        display_name=f"Governed release {suffix}",
        resources=[
            {
                "resourceType": "connector",
                "resourceApiName": "governed_release",
                "scopes": [_RELEASE_SCOPE],
            }
        ],
        idempotency_key=f"governed-release-{suffix}-application",
        ctx=RELEASE_USER,
    )
    app_id = str(created["application"]["id"])
    foundry.developer_console.create_osdk_application_client(
        app_id,
        client_id=client_id,
        redirect_uris=(redirect_uri,),
        allowed_scopes=(_RELEASE_SCOPE,),
        idempotency_key=f"governed-release-{suffix}-client",
        ctx=RELEASE_USER,
    )
    provider = _strict_provider(foundry)
    monkeypatch.setattr(api_runtime, "get_auth_provider", lambda: provider)
    return (
        app_id,
        _oauth_headers(foundry, app_id, client_id, redirect_uri, "release", suffix),
        _oauth_headers(foundry, app_id, client_id, redirect_uri, "builder", suffix),
    )


def _oauth_headers(
    foundry: Any,
    app_id: str,
    client_id: str,
    redirect_uri: str,
    plane: str,
    suffix: str,
) -> dict[str, str]:
    verifier = f"governed-release-{suffix}-{plane}-verifier"
    resource = f"http://testserver/mcp/{plane}/{app_id}"
    authorization = foundry.auth.osdk_oauth_authorize(
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=_s256(verifier),
        scopes=(_RELEASE_SCOPE,),
        resource=resource,
        resource_application_id=app_id,
        ctx=RELEASE_USER,
    )
    token = foundry.auth.osdk_oauth_token(
        client_id=client_id,
        code=str(authorization["code"]),
        redirect_uri=redirect_uri,
        code_verifier=verifier,
        resource=resource,
        resource_application_id=app_id,
        ctx=RELEASE_USER,
    )
    return {
        "Authorization": f"Bearer {token['accessToken']}",
        "MCP-Protocol-Version": "2025-06-18",
        "X-Request-ID": f"governed-release-{suffix}-{plane}",
    }


def _install_release_transport_gateway(
    foundry: Any,
    monkeypatch: Any,
    calls: list[dict[str, object]],
) -> set[str]:
    sessions: set[str] = set()

    def open_session(
        _application_id: str,
        *,
        session_id: str,
        ctx: RequestContext,
    ) -> None:
        del ctx
        sessions.add(session_id)

    def session_events(
        _application_id: str,
        session_id: str,
        *,
        after_sequence: int,
        ctx: RequestContext,
    ) -> list[Mapping[str, object]]:
        del after_sequence, ctx
        if session_id not in sessions:
            raise ValidationFailed("Release MCP session was not found", details={"resource": "mcp_session"})
        return []

    def close_session(
        _application_id: str,
        session_id: str,
        *,
        ctx: RequestContext,
    ) -> None:
        del ctx
        sessions.discard(session_id)

    def run_tool(call: GovernedReleaseMcpToolCall, *, ctx: RequestContext) -> dict[str, object]:
        del ctx
        calls.append(
            {
                "application_id": call.application_id,
                "session_id": call.session_id,
                "json_rpc_id": call.json_rpc_id,
                "tool_name": call.tool_name,
                "arguments": dict(call.arguments),
                "widget_confirmation_token": call.widget_confirmation_token,
                "origin": call.origin,
            }
        )
        return {
            "content": [{"type": "text", "text": "Release evidence prepared."}],
            "structuredContent": {"release": {"status": "prepared"}},
            "isError": False,
        }

    monkeypatch.setattr(
        foundry.release,
        "consume_release_mcp_endpoint_rate_limit",
        lambda _application_id, *, ctx: None,
        raising=False,
    )
    monkeypatch.setattr(foundry.release, "open_release_mcp_session", open_session, raising=False)
    monkeypatch.setattr(foundry.release, "release_mcp_session_events", session_events, raising=False)
    monkeypatch.setattr(
        foundry.release,
        "claim_release_mcp_session_stream",
        lambda _application_id, _session_id, *, ctx: SimpleNamespace(lease_id="release-stream-lease"),
        raising=False,
    )
    monkeypatch.setattr(
        foundry.release,
        "release_release_mcp_session_stream",
        lambda _application_id, _session_id, _lease_id, *, ctx: True,
        raising=False,
    )
    monkeypatch.setattr(foundry.release, "close_release_mcp_session", close_session, raising=False)
    monkeypatch.setattr(
        foundry.release,
        "release_mcp_tools",
        lambda _application_id, *, session_id, ctx: {
            "tools": [
                {
                    "name": "release.prepare",
                    "description": "Prepare governed release evidence.",
                    "inputSchema": {"type": "object", "additionalProperties": False},
                    "_meta": {"ui": {"visibility": ["app"]}, "openai/visibility": "private"},
                }
            ]
        },
        raising=False,
    )
    monkeypatch.setattr(foundry.release, "run_release_mcp_tool", run_tool, raising=False)
    return sessions


def _strict_provider(foundry: Any) -> JwtOidcAuthProvider:
    issuer = foundry._services.osdk_oauth_sessions.oauth_token_issuer
    return JwtOidcAuthProvider(
        JwtOidcAuthConfig(
            issuer=issuer.issuer,
            audience=issuer.audience,
            jwks=issuer.public_jwks(),
            grant_type_claim="gty",
            grant_type_value="authorization_code",
        )
    )


def _human_headers(foundry: Any) -> dict[str, str]:
    issuer = foundry._services.osdk_oauth_sessions.oauth_token_issuer
    token = issuer.issue_access_token(
        {
            "tenant_id": RELEASE_USER.tenant_id,
            "actor_user_id": RELEASE_USER.actor_user_id,
            "roles": list(RELEASE_USER.roles),
            "application_id": "",
            "client_id": "",
            "scopes": [],
            "session_id": "governed-release-human-session",
        },
        ttl_seconds=300,
    )["accessToken"]
    return {"Authorization": f"Bearer {token}", "X-Request-ID": "governed-release-human"}


def _external_oidc_provider_and_token(*, issuer: str, audience: str) -> tuple[JwtOidcAuthProvider, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    jwk.update({"kid": "external-release-key", "alg": "RS256", "use": "sig"})
    provider = JwtOidcAuthProvider(
        JwtOidcAuthConfig(
            issuer=issuer,
            audience="unused-default-audience",
            jwks={"keys": [jwk]},
            client_id_claim="azp",
            session_claim="sid",
            oauth_session_authority="issuer",
            human_grant_claim="gty",
            human_grant_value="authorization_code",
            grant_type_claim="gty",
            grant_type_value="authorization_code",
        )
    )
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "iss": issuer,
            "aud": audience,
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "tenant_id": RELEASE_USER.tenant_id,
            "sub": RELEASE_USER.actor_user_id,
            "roles": list(RELEASE_USER.roles),
            "azp": "https://chatgpt.com/oauth/release/client.json",
            "scope": _RELEASE_SCOPE,
            "sid": "external-raw-session-id",
            "gty": "authorization_code",
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "external-release-key"},
    )
    return provider, token


def _initialize_payload(rpc_id: str) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "governed-release-test", "version": "1.0.0"},
        },
    }


def _s256(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
