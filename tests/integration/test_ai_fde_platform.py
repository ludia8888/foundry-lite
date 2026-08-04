"""Cross-domain FDE, lazy discovery, structured operations, and Builder MCP proof."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from fastapi.testclient import TestClient
from foundry_lite.application.ports.language_model import ModelRequest, ModelResponse, ModelToolCall
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.auth import JwtOidcAuthConfig, JwtOidcAuthProvider
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.main import app
from sqlalchemy import select

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
    headers = {
        "X-Tenant-ID": "tenant-demo",
        "X-User-ID": "fde-platform-user",
        "X-Roles": "data_engineer",
        "X-Foundry-Lite-App-ID": app_id,
        "X-Foundry-Lite-Client-ID": "client-fde-mcp-docs",
        "X-Foundry-Lite-Scopes": scope,
        "X-Request-ID": "req-fde-mcp",
    }

    initialized = client.post(
        f"/mcp/builder/{app_id}",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
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
    authorized = client.get(
        "/api/auth/osdk/oauth/authorize",
        params={
            "clientId": "fde-mcp-oauth-client",
            "redirectUri": "https://chat.example.test/oauth/callback",
            "codeChallenge": _s256(verifier_text),
            "scope": scope,
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
        },
    )
    issuer = foundry._services.osdk_oauth_sessions.oauth_token_issuer
    provider = JwtOidcAuthProvider(
        JwtOidcAuthConfig(issuer=issuer.issuer, audience=issuer.audience, jwks=issuer.public_jwks())
    )
    monkeypatch.setattr(api_runtime, "get_auth_provider", lambda: provider)
    listed = client.post(
        f"/mcp/builder/{app_id}",
        headers={"Authorization": f"Bearer {token.json()['accessToken']}", "X-Request-ID": "mcp-oauth"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )

    assert authorized.status_code == 200
    assert token.status_code == 200
    assert listed.status_code == 200
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    assert "platform.docs.search" in names
    assert "ontology.branch.apply_patch" not in names


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


def test_builder_mcp_requires_out_of_band_confirmation_and_rejects_untrusted_origin(
    foundry: Any, monkeypatch: Any, tmp_path: Any
) -> None:
    csv_path = tmp_path / "mcp-restaurants.csv"
    csv_path.write_text("id,name\nR-1,Seoul Table\n", encoding="utf-8")
    foundry.datasets.ensure("clean.restaurants", primary_key=["id"], ctx=FDE_USER)
    foundry.datasets.upload_csv("clean.restaurants", str(csv_path), ctx=FDE_USER)
    foundry.ontology.apply_text("objectTypes: []\nactionTypes: []\nlinkTypes: []\n", ctx=FDE_USER)
    branch = foundry.ontology.create_branch(name="mcp-write", idempotency_key="mcp-write-branch", ctx=FDE_USER)
    app_id, headers = _builder_mcp_application(foundry, "ontology_editing")
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    call = _mcp_patch_call(str(branch["id"]), "write-denied")

    denied = client.post(f"/mcp/builder/{app_id}", headers=headers, json=call)
    approved = client.post(
        f"/mcp/builder/{app_id}",
        headers={**headers, "X-FDE-Confirm-Tool": "ontology.branch.apply_patch"},
        json={**call, "id": "write-approved"},
    )
    rejected_origin = client.post(
        f"/mcp/builder/{app_id}",
        headers={**headers, "Origin": "https://attacker.invalid"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )

    assert denied.status_code == 200
    assert denied.json()["error"]["data"]["type"] == "PERMISSION_DENIED"
    assert approved.status_code == 200
    assert approved.json()["result"]["structuredContent"]["changeSummary"] == "Add Restaurant"
    assert rejected_origin.status_code == 400
    assert len(foundry.ontology.branch_diff(str(branch["id"]), ctx=FDE_USER)["resources"]) == 1


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


def _api_headers() -> dict[str, str]:
    return {
        "X-Tenant-ID": "tenant-demo",
        "X-User-ID": "fde-platform-user",
        "X-Roles": "data_engineer",
        "X-Request-ID": "req-fde-platform-api",
    }


def _builder_mcp_application(foundry: Any, mode: str) -> tuple[str, dict[str, str]]:
    scope = f"osdk:connector:fde_{mode}:execute"
    application = foundry.developer_console.create_osdk_application(
        app_api_name="FdeMcpWriter",
        display_name="FDE MCP Writer",
        client_id="client-fde-mcp-writer",
        resources=[{"resourceType": "connector", "resourceApiName": f"fde_{mode}", "scopes": [scope]}],
        idempotency_key="fde-mcp-writer-app",
        ctx=FDE_USER,
    )
    app_id = str(application["application"]["id"])
    return app_id, {
        **_api_headers(),
        "X-Foundry-Lite-App-ID": app_id,
        "X-Foundry-Lite-Client-ID": "client-fde-mcp-writer",
        "X-Foundry-Lite-Scopes": scope,
    }


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
