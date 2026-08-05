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


def test_builder_mcp_lazy_search_activates_only_scoped_tools_for_one_session(foundry: Any, monkeypatch: Any) -> None:
    app_id, headers = _builder_mcp_application(foundry, "platform_qa")
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    initialized = client.post(
        f"/mcp/builder/{app_id}",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    session_id = initialized.headers["Mcp-Session-Id"]
    session_headers = {**headers, "Mcp-Session-Id": session_id}
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
    new_session = client.post(
        f"/mcp/builder/{app_id}",
        headers={**headers, "X-Request-ID": "req-new-builder-session"},
        json={"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {"discoveryMode": "lazy"}},
    )
    events = client.get(
        f"/mcp/builder/{app_id}?discoveryMode=lazy",
        headers=session_headers,
    )

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


def test_official_palantir_mcp_names_execute_native_compass_object_docs_and_osdk_services(
    foundry: Any, monkeypatch: Any, tmp_path: Any
) -> None:
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    governance_app, governance_headers = _builder_mcp_application(foundry, "governance")
    created = _mcp_native_call(
        client,
        governance_app,
        {**governance_headers, "X-FDE-Confirm-Tool": "create_foundry_project"},
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
    docs_app, docs_headers = _builder_mcp_application(foundry, "platform_qa")
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
    osdk_app, osdk_headers = _builder_mcp_application(foundry, "osdk_react")
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
        {**osdk_headers, "X-FDE-Confirm-Tool": "generate_new_ontology_sdk_version"},
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
    app_id, headers = _builder_mcp_application(foundry, "ontology_editing")
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
        {**headers, "X-FDE-Confirm-Tool": "create_or_update_foundry_object_type"},
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

    assert denied["error"]["data"]["type"] == "PERMISSION_DENIED"
    assert created["changeSummary"] == "Add Restaurant"
    assert viewed["definition"]["apiName"] == "Restaurant"
    assert searched["items"][0]["apiName"] == "Restaurant"
    assert identity["branchId"] == branch_id
    assert not _active_object_type_exists(foundry, "Restaurant")

    deleted = _mcp_native_call(
        client,
        app_id,
        {**headers, "X-FDE-Confirm-Tool": "delete_foundry_object_type"},
        "delete-object",
        "ontology_editing",
        f"ontology-branch:{branch_id}",
        "delete_foundry_object_type",
        {"apiName": "Restaurant", "changeSummary": "Remove Restaurant"},
    )
    assert deleted["changeSummary"] == "Remove Restaurant"
    assert foundry.ontology.branch_diff(branch_id, ctx=FDE_USER)["resources"] == []


def test_official_palantir_data_connection_tools_use_native_governed_sources(foundry: Any, monkeypatch: Any) -> None:
    app_id, headers = _builder_mcp_application(foundry, "data_connection")
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    rest_source = _mcp_native_call(
        client,
        app_id,
        {**headers, "X-FDE-Confirm-Tool": "create_foundry_rest_api_data_source"},
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
        {**headers, "X-FDE-Confirm-Tool": "get_or_create_network_egress_policy"},
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
        {**headers, "X-FDE-Confirm-Tool": "create_foundry_rest_api_data_source_webhook"},
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
    response = client.post(
        f"/mcp/builder/{app_id}",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": {"mode": mode, "workspaceRef": workspace_ref, "arguments": arguments},
            },
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert "error" not in payload, payload
    return dict(payload["result"]["structuredContent"])


def _raw_mcp_native_call(
    client: TestClient,
    app_id: str,
    headers: dict[str, str],
    rpc_id: str,
    branch_id: str,
    tool_name: str,
    arguments: dict[str, object],
) -> dict[str, Any]:
    response = client.post(
        f"/mcp/builder/{app_id}",
        headers=headers,
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


def _builder_mcp_application(
    foundry: Any,
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
    return app_id, {
        **_api_headers(),
        "X-Foundry-Lite-App-ID": app_id,
        "X-Foundry-Lite-Client-ID": f"client-fde-mcp-{suffix}",
        "X-Foundry-Lite-Scopes": " ".join([scope, *extra_scopes]),
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
