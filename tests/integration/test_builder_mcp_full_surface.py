"""Exhaustive real JSON-RPC proof for every Builder/FDE catalog tool."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from foundry_lite.application.ports import AiToolCallRecord
from foundry_lite.application.services.aip.fde_catalog import FDE_MODES, fde_tool_catalog
from foundry_lite.application.services.aip.fde_mcp_confirmation_contract import FdeMcpRequestBinding
from foundry_lite.application.services.aip.fde_mcp_run_ledger import FdeMcpRunLedger
from foundry_lite.application.services.aip.fde_mcp_security import FdeMcpSecurityLedger
from foundry_lite.application.services.aip.fde_mcp_types import FdeMcpToolCall
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters import KafkaSourceStreamAdapter
from foundry_lite.infrastructure.auth import JwtOidcAuthConfig, JwtOidcAuthProvider
from foundry_lite.infrastructure.repositories.ai_run_repository import SqlAlchemyAiRunRepository
from foundry_lite.security.policy import PolicyService
from foundry_lite.security.tenant_context import current_tenant_id
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.main import app
from sqlalchemy import create_engine, event, select

FDE_USER = RequestContext(
    tenant_id="tenant-demo",
    actor_user_id="fde-full-surface-user",
    roles=("data_engineer",),
    request_id="req-fde-full-surface",
)


def test_builder_execution_ledgers_bind_authenticated_tenant_before_transactions(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'builder-tenant-context.db'}", future=True)
    db.create_database(engine)
    observed_tenants: list[str | None] = []
    event.listen(engine, "begin", lambda _conn: observed_tenants.append(current_tenant_id()))
    repository = SqlAlchemyAiRunRepository(engine)
    security = FdeMcpSecurityLedger(engine, repository, PolicyService())
    runs = FdeMcpRunLedger(engine, repository, security)
    ctx = _builder_ledger_context()
    request = _builder_ledger_request()
    binding = _builder_ledger_binding(ctx, request)
    run_id = "builder-tenant-context-run"

    assert runs.seed(ctx, request, binding, run_id, ()) is True
    runs.complete(ctx, run_id, _builder_tool_record(ctx, run_id, binding))
    challenge = security.issue_challenge(ctx, run_id, binding)
    metadata = challenge.get("_meta")
    assert isinstance(metadata, Mapping)
    widget_token = metadata.get("widgetApprovalToken")
    assert isinstance(widget_token, str)
    structured = challenge.get("structuredContent")
    assert isinstance(structured, Mapping)
    security.approve_widget(
        ctx,
        request.application_id,
        request.session_id,
        str(structured["challengeId"]),
        widget_token,
        request.origin,
    )
    assert security.replay(ctx, run_id, binding) is not None

    assert observed_tenants == [ctx.tenant_id] * 5
    assert current_tenant_id() is None


def test_builder_mcp_executes_every_catalog_tool_through_real_json_rpc(
    foundry: Any,
    monkeypatch: Any,
    tmp_path: Any,
) -> None:
    """Ratchet the real Builder MCP surface to exactly the canonical 70 tools."""
    state = _prepare_surface_state(foundry, monkeypatch, tmp_path)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    issuer = foundry._services.osdk_oauth_sessions.oauth_token_issuer
    provider = JwtOidcAuthProvider(
        JwtOidcAuthConfig(issuer=issuer.issuer, audience=issuer.audience, jwks=issuer.public_jwks())
    )
    monkeypatch.setattr(api_runtime, "get_auth_provider", lambda: provider)
    human_token = issuer.issue_access_token(
        {
            "tenant_id": FDE_USER.tenant_id,
            "actor_user_id": FDE_USER.actor_user_id,
            "roles": list(FDE_USER.roles),
            "application_id": "",
            "client_id": "",
            "scopes": [],
            "session_id": "mcp-full-surface-human-session",
        },
        ttl_seconds=900,
    )["accessToken"]
    runner = _FullSurfaceRunner(
        foundry,
        TestClient(app),
        human_headers={"Authorization": f"Bearer {human_token}", "X-Request-ID": "mcp-full-surface-human"},
    )

    governance = _run_governance_tools(runner, foundry)
    _run_exploration_tools(runner, state, governance)
    _run_ontology_tools(runner, state)
    _run_pipeline_tools(runner, state)
    _run_data_connection_tools(runner, state)
    _run_function_tools(runner)
    _run_osdk_tools(runner)
    _run_documentation_tools(runner)
    runner.call("ml", "tenant:tenant-demo", "ml.catalog.inspect", {}, ("available", "items", "count"))

    runner.assert_complete()


class _FullSurfaceRunner:
    def __init__(self, foundry: Any, client: TestClient, *, human_headers: dict[str, str]) -> None:
        self.foundry = foundry
        self.client = client
        self.human_headers = human_headers
        self.catalog = _canonical_catalog()
        self.executed: set[str] = set()
        self.challenged: set[str] = set()
        self.receipts: set[str] = set()
        self._applications: dict[str, tuple[str, dict[str, str]]] = {}
        self.advertised_by_mode: dict[str, dict[str, dict[str, object]]] = {}

    def call(
        self,
        mode: str,
        workspace_ref: str,
        tool_id: str,
        arguments: Mapping[str, object],
        required_keys: tuple[str, ...],
        *,
        wire_name: str | None = None,
    ) -> dict[str, Any]:
        assert tool_id not in self.executed, f"duplicate exhaustive call for {tool_id}"
        spec = self.catalog[tool_id]
        assert tool_id in {tool.tool_id for tool in fde_tool_catalog(mode, ())}
        app_id, headers = self._application(mode)
        advertised = self.advertised_by_mode[mode][tool_id]
        selected_wire_name = wire_name or tool_id
        assert advertised["name"] == selected_wire_name
        assert isinstance(advertised["inputSchema"], Mapping)
        payload = _tool_call_payload(tool_id, mode, workspace_ref, selected_wire_name, arguments)
        response = self.client.post(f"/mcp/builder/{app_id}", headers=headers, json=payload)
        body = response.json()

        if spec.effect != "READ":
            body = self._approve_and_retry(app_id, headers, payload, response.status_code, body, tool_id)
        else:
            assert response.status_code == 200, body
            assert "error" not in body, body
            assert body["result"]["structuredContent"].get("status") != "approval_required"

        result = body["result"]
        structured = result["structuredContent"]
        assert isinstance(structured, dict) and structured, (tool_id, structured)
        assert all(key in structured for key in required_keys), (tool_id, required_keys, structured)
        _assert_single_native_tool_row(self.foundry, str(result["aiRunId"]), _ledger_tool_id(tool_id))
        self.executed.add(tool_id)
        return dict(structured)

    def _approve_and_retry(
        self,
        app_id: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        status_code: int,
        body: dict[str, Any],
        tool_id: str,
    ) -> dict[str, Any]:
        assert status_code == 200 and "error" not in body, body
        challenge = body["result"]["structuredContent"]
        assert challenge["status"] == "approval_required"
        assert challenge["toolId"] == tool_id
        _assert_short_lived(str(challenge["expiresAt"]))
        approval = self.client.post(
            f"/api/aip/fde/mcp/{app_id}/confirmations/{challenge['challengeId']}/approve",
            headers=self.human_headers,
        )
        approval_body = approval.json()
        assert approval.status_code == 200, approval_body
        assert approval_body["status"] == "approved"
        _assert_short_lived(str(approval_body["expiresAt"]))
        receipt = str(approval_body["confirmationReceipt"])
        assert receipt and receipt not in self.receipts
        self.receipts.add(receipt)
        payload["params"]["arguments"]["confirmationReceipt"] = receipt
        completed = self.client.post(f"/mcp/builder/{app_id}", headers=headers, json=payload)
        completed_body = completed.json()
        assert completed.status_code == 200 and "error" not in completed_body, completed_body
        self.challenged.add(tool_id)
        return dict(completed_body)

    def _application(self, mode: str) -> tuple[str, dict[str, str]]:
        existing = self._applications.get(mode)
        if existing is not None:
            return existing
        app_id, headers = _create_builder_application(self.foundry, mode)
        initialized = self.client.post(
            f"/mcp/builder/{app_id}",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": f"initialize-{mode}",
                "method": "initialize",
                "params": _initialize_params(),
            },
        )
        assert initialized.status_code == 200, initialized.text
        session = {**headers, "Mcp-Session-Id": initialized.headers["Mcp-Session-Id"]}
        acknowledged = self.client.post(
            f"/mcp/builder/{app_id}",
            headers=session,
            json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        )
        assert acknowledged.status_code == 202 and not acknowledged.content
        listed = self.client.post(
            f"/mcp/builder/{app_id}",
            headers=session,
            json={"jsonrpc": "2.0", "id": f"list-{mode}", "method": "tools/list", "params": {}},
        )
        assert listed.status_code == 200, listed.text
        result = listed.json()["result"]
        assert "nextCursor" not in result
        advertised = _advertised_tools(result["tools"])
        expected_mode = {tool.tool_id for tool in fde_tool_catalog(mode, ())}
        assert set(advertised) == expected_mode
        self.advertised_by_mode[mode] = advertised
        self._applications[mode] = (app_id, session)
        return app_id, session

    def assert_complete(self) -> None:
        expected = set(self.catalog)
        mutations = {tool_id for tool_id, spec in self.catalog.items() if spec.effect != "READ"}
        assert len(expected) == 71
        assert len(expected - mutations) == 51
        assert len(mutations) == 20
        assert self.executed == expected
        assert self.challenged == mutations
        assert len(self.receipts) == len(mutations)
        advertised_union = {tool_id for advertised in self.advertised_by_mode.values() for tool_id in advertised}
        assert set(self.advertised_by_mode) == {mode.mode_id for mode in FDE_MODES}
        assert advertised_union == expected


def _prepare_surface_state(foundry: Any, monkeypatch: Any, tmp_path: Any) -> dict[str, Any]:
    orders_path = tmp_path / "mcp-full-surface-orders.csv"
    customers_path = tmp_path / "mcp-full-surface-customers.csv"
    orders_path.write_text("order_id,customer_id,status,amount\nO-1,C-1,PENDING,10\n", encoding="utf-8")
    customers_path.write_text("customer_id,name\nC-1,Seoul Table\n", encoding="utf-8")
    foundry.datasets.ensure("clean.mcp_surface_orders", primary_key=["order_id"], ctx=FDE_USER)
    foundry.datasets.ensure("clean.mcp_surface_customers", primary_key=["customer_id"], ctx=FDE_USER)
    foundry.datasets.upload_csv("clean.mcp_surface_orders", str(orders_path), ctx=FDE_USER)
    foundry.datasets.upload_csv("clean.mcp_surface_customers", str(customers_path), ctx=FDE_USER)
    foundry.ontology.apply_text(_surface_ontology_yaml(), ctx=FDE_USER)
    foundry.objects.reindex("Order", ctx=FDE_USER)
    foundry.objects.reindex("Customer", ctx=FDE_USER)
    ontology_branch = foundry.ontology.create_branch(
        name="mcp-full-surface-ontology",
        idempotency_key="mcp-full-surface-ontology",
        ctx=FDE_USER,
    )
    pipeline_branch = _prepare_pipeline(foundry, tmp_path)
    source = _prepare_kafka_source(foundry, monkeypatch)
    return {
        "ontologyBranchId": str(ontology_branch["id"]),
        "pipelineBranch": pipeline_branch,
        "source": source,
        "datasetRef": "clean.mcp_surface_orders",
    }


def _prepare_pipeline(foundry: Any, tmp_path: Any) -> dict[str, Any]:
    raw_path = tmp_path / "mcp-full-surface-pipeline.csv"
    raw_path.write_text("order_id,amount\nO-1,10\n", encoding="utf-8")
    foundry.datasets.ensure("raw.mcp_surface_orders", primary_key=["order_id"], ctx=FDE_USER)
    foundry.datasets.upload_csv("raw.mcp_surface_orders", str(raw_path), ctx=FDE_USER)
    return dict(
        foundry.pipelines.create_branch(
            pipeline_id="mcp-full-surface-pipeline",
            name="mcp-full-surface-pipeline",
            idempotency_key="mcp-full-surface-pipeline",
            ctx=FDE_USER,
        )
    )


def _prepare_kafka_source(foundry: Any, monkeypatch: Any) -> dict[str, Any]:
    foundry.sources.create_managed_sync(
        sync_name="mcp_full_surface_sync",
        source_name="mcp_full_surface_kafka",
        display_name="MCP full surface Kafka",
        source_type="kafka",
        capability="streaming",
        mode="APPEND",
        target_dataset_ref="raw.mcp_surface_events",
        schedule={"mode": "manual"},
        config_summary={
            "bootstrapServers": "surface-redpanda:9092",
            "connectionMode": "direct",
            "topic": "surface.events",
            "partition": 0,
            "streamName": "surface-events",
            "consumerGroup": "foundry-mcp-full-surface",
            "batchLimit": 100,
        },
        idempotency_key="mcp-full-surface-sync",
        ctx=FDE_USER,
    )
    service = foundry._services.fde_platform_tools.source_connection_test_service
    adapter = KafkaSourceStreamAdapter(admin_factory=lambda _config: _SurfaceKafkaAdminClient())
    monkeypatch.setattr(service, "source_stream_adapter", adapter)
    return dict(foundry.sources.get_source("mcp_full_surface_kafka", ctx=FDE_USER))


def _run_governance_tools(runner: _FullSurfaceRunner, foundry: Any) -> dict[str, Any]:
    created = runner.call(
        "governance",
        "tenant:tenant-demo",
        "create_foundry_project",
        {
            "displayName": "MCP Full Surface",
            "description": "Real JSON-RPC exhaustive proof",
            "metadata": {"proof": "70-of-70"},
            "idempotencyKey": "mcp-full-surface-project",
        },
        ("project",),
    )
    project = created["project"]
    folder = foundry.resources.create_folder(
        project["id"],
        display_name="Proof datasets",
        idempotency_key="mcp-full-surface-folder",
        ctx=FDE_USER,
    )["folder"]
    resource = foundry.resources.register_resource(
        resource_type="dataset",
        display_name="clean.mcp_surface_orders",
        project_id=project["id"],
        folder_id=folder["id"],
        source_surface="dataset",
        source_ref="clean.mcp_surface_orders",
        idempotency_key="mcp-full-surface-resource",
        ctx=FDE_USER,
    )["resource"]
    inspected = runner.call(
        "governance",
        f"project:{project['id']}",
        "governance.project.inspect",
        {"projectId": project["id"]},
        ("project", "folders", "resources"),
    )
    assert inspected["project"]["id"] == project["id"]
    return {"project": project, "folder": folder, "resource": resource}


def _run_exploration_tools(
    runner: _FullSurfaceRunner,
    state: dict[str, Any],
    governance: dict[str, Any],
) -> None:
    tenant = "tenant:tenant-demo"
    runner.call(
        "exploration",
        tenant,
        "fde.tools.search",
        {"query": "dataset", "maxResults": 8},
        ("queryHash", "activatedTools", "toolsListChanged"),
        wire_name="search_tools",
    )
    runner.call(
        "exploration",
        tenant,
        "fde.plan.present",
        {
            "objective": "Prove every Builder MCP tool",
            "steps": ["Call every catalog entry"],
            "assumptions": ["Local fixture is isolated"],
            "risks": ["A catalog drift must fail this test"],
            "requiredApprovals": ["Every mutation"],
        },
        ("operationType", "status", "objective", "steps"),
    )
    runner.call(
        "exploration",
        tenant,
        "fde.clarification.request",
        {
            "question": "Continue exhaustive proof?",
            "options": ["yes", "no"],
            "reason": "Exercise structured clarification",
            "isBlocking": False,
        },
        ("operationType", "status", "question", "options"),
    )
    _run_compass_exploration(runner, governance)
    _run_object_and_dataset_exploration(runner, state)


def _run_compass_exploration(runner: _FullSurfaceRunner, governance: dict[str, Any]) -> None:
    project = governance["project"]
    folder = governance["folder"]
    resource = governance["resource"]
    cases = (
        ("resource.search", "tenant:tenant-demo", {"query": "mcp_surface_orders"}, ("items", "count")),
        ("resource.inspect", f"resource:{resource['rid']}", {"rid": resource["rid"]}, ("resource",)),
        (
            "list_resources_in_foundry_folder",
            f"project:{project['id']}",
            {"folderId": folder["id"], "projectId": project["id"]},
            ("items", "nextCursor"),
        ),
        (
            "get_project_imports",
            f"project:{project['id']}",
            {"projectId": project["id"]},
            ("projectId", "items", "count"),
        ),
        ("search_foundry_projects", "tenant:tenant-demo", {"query": "MCP Full"}, ("items", "count")),
    )
    for tool_id, workspace, arguments, keys in cases:
        runner.call("exploration", workspace, tool_id, arguments, keys)


def _run_object_and_dataset_exploration(runner: _FullSurfaceRunner, state: dict[str, Any]) -> None:
    runner.call(
        "exploration",
        "tenant:tenant-demo",
        "query_ontology_objects",
        {"objectType": "Order", "limit": 10},
        ("items", "nextCursor"),
    )
    runner.call(
        "exploration",
        "tenant:tenant-demo",
        "aggregate_ontology_objects",
        {"objectType": "Order", "groupBy": ["status"], "select": [{"function": "count", "name": "orders"}]},
        ("groups",),
    )
    runner.call(
        "exploration",
        "tenant:tenant-demo",
        "traverse_ontology_object_links",
        {"objectType": "Order", "objectId": "O-1", "linkType": "OrderCustomer"},
        ("objectType", "linkType", "items", "count"),
    )
    runner.call(
        "exploration",
        "tenant:tenant-demo",
        "search_around_ontology_objects",
        {"fromObjectType": "Order", "linkTypes": ["OrderCustomer"]},
        ("objectType", "fromObjectType", "linkTypes", "objectIds", "count"),
    )
    dataset_ref = state["datasetRef"]
    workspace = f"dataset:{dataset_ref}"
    schema = runner.call(
        "exploration",
        workspace,
        "get_foundry_dataset_schema",
        {"datasetRef": dataset_ref},
        ("datasetRef", "datasetId", "version", "schema"),
    )
    runner.call(
        "exploration",
        workspace,
        "list_dataset_files",
        {"datasetRef": dataset_ref},
        ("files", "count", "isManifestBounded"),
    )
    runner.call(
        "exploration",
        workspace,
        "get_dataset_stats",
        {"datasetRef": dataset_ref},
        ("datasetRef", "rowCount", "fileCount"),
    )
    runner.call(
        "exploration",
        "tenant:tenant-demo",
        "get_resource_graph",
        {"resourceId": schema["version"]["id"], "maxDepth": 3},
        ("rootResourceId", "maxDepth", "nodes", "edges"),
    )


def _run_ontology_tools(runner: _FullSurfaceRunner, state: dict[str, Any]) -> None:
    workspace = f"ontology-branch:{state['ontologyBranchId']}"
    reads = (
        ("ontology.branch.inspect", {}, ("branch", "resources", "diff")),
        ("get_foundry_ontology_rid", {}, ("ontologyRid", "branchRid", "branchId")),
        ("search_foundry_ontology", {"query": "Order"}, ("items", "count", "branchId")),
        ("search_foundry_functions", {"query": "echo"}, ("items", "count", "branchId")),
        ("view_foundry_object_type", {"apiName": "Order"}, ("kind", "apiName", "definition")),
        ("view_foundry_link_type", {"apiName": "OrderCustomer"}, ("kind", "apiName", "definition")),
        ("view_foundry_action_type", {"apiName": "ApproveOrder"}, ("kind", "apiName", "definition")),
    )
    for tool_id, arguments, keys in reads:
        runner.call("ontology_editing", workspace, tool_id, arguments, keys)
    _run_official_ontology_mutations(runner, workspace)
    runner.call(
        "ontology_editing",
        workspace,
        "ontology.branch.apply_patch",
        {
            "upsertResources": [{"kind": "objectType", "definition": _surface_marker_definition()}],
            "deleteResources": [],
            "changeSummary": "Leave one proposal-visible marker",
        },
        ("branch", "changeSummary", "validation", "diff"),
    )
    runner.call("ontology_editing", workspace, "ontology.branch.validate", {}, ("branch", "validation", "diff"))
    # Rebase only means something once main has moved on, so advance the active Ontology first and
    # then re-anchor the branch on it -- the stale-base path an author actually hits.
    runner.foundry.ontology.apply_text(_surface_ontology_yaml_after_main_moved(), ctx=FDE_USER)
    branch_id = workspace.removeprefix("ontology-branch:")
    stranded = runner.foundry.ontology.get_branch(branch_id, ctx=FDE_USER)
    assert stranded["baseStale"] is True
    runner.call(
        "ontology_editing",
        workspace,
        "ontology.branch.rebase",
        {
            "resolutions": [],
            "expectedFingerprint": stranded["contentFingerprint"],
        },
        ("id", "baseVersionId", "rebasedAt"),
    )
    runner.call(
        "ontology_editing",
        workspace,
        "ontology.branch.propose",
        {
            "title": "Builder MCP 70-tool ontology proof",
            "description": "Exhaustive JSON-RPC branch proposal",
            "idempotencyKey": "mcp-full-surface-ontology-proposal",
        },
        ("id", "name", "status"),
    )


def _run_official_ontology_mutations(runner: _FullSurfaceRunner, workspace: str) -> None:
    creates = (
        ("create_or_update_foundry_object_type", _surface_temp_object_definition()),
        ("create_or_update_foundry_link_type", _surface_temp_link_definition()),
        ("create_or_update_foundry_action_type", _surface_temp_action_definition()),
    )
    for tool_id, definition in creates:
        runner.call(
            "ontology_editing",
            workspace,
            tool_id,
            {"definition": definition, "changeSummary": f"Create {definition['apiName']}"},
            ("branch", "changeSummary", "validation", "diff"),
        )
    deletes = (
        ("delete_foundry_action_type", "SurfaceAction"),
        ("delete_foundry_link_type", "SurfaceLink"),
        ("delete_foundry_object_type", "SurfaceObject"),
    )
    for tool_id, api_name in deletes:
        runner.call(
            "ontology_editing",
            workspace,
            tool_id,
            {"apiName": api_name, "changeSummary": f"Delete {api_name}"},
            ("branch", "changeSummary", "validation", "diff"),
        )


def _run_pipeline_tools(runner: _FullSurfaceRunner, state: dict[str, Any]) -> None:
    branch = state["pipelineBranch"]
    workspace = f"pipeline-branch:{branch['id']}"
    runner.call("data_integration", workspace, "pipeline.branch.inspect", {}, ("branch", "diff"))
    runner.call(
        "data_integration",
        workspace,
        "pipeline.branch.update_graph",
        {"graph": _surface_pipeline_graph(), "expectedFingerprint": branch["graphFingerprint"]},
        ("id", "graph", "graphFingerprint"),
    )
    runner.call("data_integration", workspace, "pipeline.branch.validate", {}, ("valid", "errors", "warnings"))
    runner.call(
        "data_integration",
        workspace,
        "pipeline.branch.run_tests",
        {},
        ("status", "proofKind", "isDataExecution"),
    )
    runner.call(
        "data_integration",
        workspace,
        "pipeline.branch.propose",
        {
            "title": "Builder MCP 70-tool pipeline proof",
            "description": "Exhaustive JSON-RPC pipeline proposal",
            "idempotencyKey": "mcp-full-surface-pipeline-proposal",
        },
        ("id", "status", "branchId"),
    )


def _run_data_connection_tools(runner: _FullSurfaceRunner, state: dict[str, Any]) -> None:
    source = state["source"]
    source_workspace = "source:mcp_full_surface_kafka"
    runner.call("data_connection", source_workspace, "source.inspect", {}, ("sourceName", "kind", "configFingerprint"))
    tested = runner.call(
        "data_connection",
        source_workspace,
        "source.test_connection",
        {
            "expectedConfigFingerprint": source["configFingerprint"],
            "idempotencyKey": "mcp-full-surface-connection-test",
        },
        ("connectionTestId", "sourceName", "status", "checks"),
    )
    assert tested["status"] == "succeeded"
    history = runner.call(
        "data_connection",
        source_workspace,
        "source.connection_history",
        {"limit": 10},
        ("sourceName", "connectionTests", "egressAttempts"),
    )
    assert history["connectionTests"][0]["connectionTestId"] == tested["connectionTestId"]
    _run_data_connection_authoring_tools(runner)


def _run_data_connection_authoring_tools(runner: _FullSurfaceRunner) -> None:
    tenant = "tenant:tenant-demo"
    runner.call(
        "data_connection",
        tenant,
        "create_foundry_rest_api_data_source",
        {
            "sourceName": "mcp_surface_rest",
            "displayName": "MCP Surface REST",
            "baseUrl": "https://surface.example.test",
            "auth": {"mode": "none"},
            "resourceName": "orders",
            "resourcePath": "/orders",
            "datasetRef": "raw.mcp_surface_rest_orders",
            "primaryKey": ["id"],
            "idempotencyKey": "mcp-full-surface-rest",
        },
        ("connection", "resource", "isGovernedSource"),
    )
    runner.call(
        "data_connection",
        tenant,
        "get_or_create_network_egress_policy",
        {
            "policyName": "mcp_surface_egress",
            "displayName": "MCP Surface egress",
            "mode": "direct",
            "allowedHosts": ["surface.example.test:443"],
            "idempotencyKey": "mcp-full-surface-egress",
        },
        ("policyName", "mode", "allowedHosts"),
    )
    runner.call(
        "data_connection",
        tenant,
        "create_foundry_rest_api_data_source_webhook",
        {
            "sourceName": "mcp_surface_webhook",
            "displayName": "MCP Surface webhook",
            "datasetRef": "raw.mcp_surface_webhook",
            "connectorName": "mcp_surface_rest",
            "resourceName": "events",
            "signingSecretRef": "MCP_SURFACE_WEBHOOK_SECRET",
            "inboundUrl": "https://foundry-lite.example.test/hooks/mcp-surface",
            "idempotencyKey": "mcp-full-surface-webhook",
        },
        ("source", "commitResults", "operationsPath"),
    )
    runner.call(
        "data_connection",
        "source:mcp_surface_webhook",
        "view_foundry_rest_api_data_source_webhook",
        {"sourceName": "mcp_surface_webhook"},
        ("sourceName", "kind", "configFingerprint"),
    )


def _run_function_tools(runner: _FullSurfaceRunner) -> None:
    executed = runner.call(
        "functions_editing",
        "function:echoInputs",
        "function.execute",
        {"functionApiName": "echoInputs", "inputs": {"note": "real MCP call"}},
        ("functionApiName", "status", "output", "resultHash"),
    )
    assert executed["status"] == "succeeded"
    assert executed["output"] == {"value": {"note": "real MCP call"}}


def _run_osdk_tools(runner: _FullSurfaceRunner) -> None:
    app_id, _headers = runner._application("osdk_react")
    workspace = f"osdk-app:{app_id}"
    runner.call("osdk_react", workspace, "osdk.application.inspect", {}, ("application", "resources"))
    runner.call("osdk_react", workspace, "view_osdk_definition", {}, ("definition", "sdkVersions", "install"))
    runner.call(
        "osdk_react",
        workspace,
        "get_ontology_sdk_context",
        {"topic": "ObjectSet"},
        ("content", "contextType", "topic"),
    )
    runner.call(
        "osdk_react",
        workspace,
        "get_ontology_sdk_examples",
        {"topic": "ObjectSet", "language": "typescript"},
        ("content", "contextType", "language"),
    )
    _run_osdk_sdk_tools(runner, workspace)
    _run_osdk_mutation_tools(runner, workspace, app_id)


def _run_osdk_sdk_tools(runner: _FullSurfaceRunner, workspace: str) -> None:
    runner.call(
        "osdk_react",
        workspace,
        "list_platform_sdk_apis",
        {"product": "dataset", "maxResults": 10},
        ("items", "count", "isGeneratedRegistry"),
    )
    runner.call(
        "osdk_react",
        workspace,
        "get_platform_sdk_api_reference",
        {"apiId": "datasets.inspect"},
        ("api", "schemaVersion", "isGeneratedRegistry"),
    )
    runner.call(
        "osdk_react",
        workspace,
        "generate_new_ontology_sdk_version",
        {
            "language": "typescript",
            "packageName": "@foundry-lite/mcp-surface",
            "requestedBump": "patch",
            "idempotencyKey": "mcp-full-surface-sdk-version",
        },
        ("sdkVersion", "artifacts"),
    )
    runner.call(
        "osdk_react",
        workspace,
        "install_sdk_package",
        {},
        ("sdkVersions", "channels", "compatibilityWindows"),
    )


def _mcp_surface_domain_brief() -> dict[str, object]:
    return {
        "actors": ["operator"],
        "records": [
            {
                "name": "업무 건",
                "apiName": "WorkItem",
                "fields": [{"name": "담당 팀", "apiName": "team", "type": "string", "required": True}],
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
                "allowedActors": ["operator"],
            }
        ],
        "policies": [{"name": "완료 기록", "statement": "완료 메모를 남겨야 합니다.", "enforcement": "warning"}],
        "evidence": ["상태 변경 전후", "담당자"],
        "integrations": [],
        "successMeasures": ["미완료 누락 0건"],
    }


def _run_osdk_mutation_tools(runner: _FullSurfaceRunner, workspace: str, app_id: str) -> None:
    runner.call(
        "osdk_react",
        workspace,
        "osdk.application.update_resources",
        {
            "resources": [
                {
                    "resourceType": "connector",
                    "resourceApiName": "fde_osdk_react",
                    "scopes": ["osdk:connector:fde_osdk_react:execute"],
                }
            ],
            "idempotencyKey": "mcp-full-surface-osdk-resources",
        },
        ("application", "clients", "resources"),
    )
    plan = runner.call(
        "osdk_react",
        workspace,
        "pilot.application.plan",
        {
            "applicationName": "MCP Surface Pilot",
            "domainDescription": "업무 건을 접수하고 담당 팀이 완료 증거까지 남깁니다.",
            "domainBrief": _mcp_surface_domain_brief(),
        },
        ("operationType", "applicationName", "domainDescription", "slug"),
    )
    generated = runner.call(
        "osdk_react",
        workspace,
        "pilot.application.generate",
        {"plan": plan, "idempotencyKey": "mcp-full-surface-pilot"},
        ("status", "resource", "ontologyBranch", "osdkApplication", "consumerOsdk", "generatedFiles"),
    )
    assert generated["status"] == "generated_on_branch"
    assert generated["consumerOsdk"]["profile"] == "consumer_osdk_strict"
    assert generated["consumerOsdk"]["exceptions"] == []
    assert generated["generatedFiles"]["isContentIncluded"] is False
    assert generated["generatedFiles"]["delivery"] == "governed_resource"
    bundle = runner.foundry.aip.get_pilot_application(generated["resource"]["rid"], ctx=FDE_USER)
    assert "@foundry-lite/sdk" not in bundle["reactFiles"]["src/App.tsx"]
    assert "@foundry-lite/mcp-surface-pilot-osdk/react" in bundle["reactFiles"]["src/App.tsx"]
    assert "OsdkObjectType<WorkItem>" in bundle["reactFiles"]["packages/application-osdk/src/generated.ts"]
    assert app_id


def _run_documentation_tools(runner: _FullSurfaceRunner) -> None:
    workspace = "docs:tenant-demo"
    cases = (
        ("platform.docs.search", {"query": "quality gate", "maxResults": 5}, ("items", "count")),
        ("get_documentation_summaries", {}, ("items", "count", "catalogVersion")),
        ("search_foundry_documentation", {"query": "ontology", "maxResults": 5}, ("items", "count")),
        ("load_foundry_documentation_page", {"documentId": "action-types"}, ("id", "content")),
        ("get_python_transforms_documentation", {"topic": "transactions"}, ("toolId", "content")),
        ("get_typescript_v1_functions_documentation", {"topic": "functions"}, ("toolId", "content")),
        ("get_typescript_v2_functions_documentation", {"topic": "functions"}, ("toolId", "content")),
        ("get_custom_widget_documentation", {"topic": "widgets"}, ("toolId", "content")),
        ("get_ml_documentation", {"topic": "models"}, ("toolId", "content")),
        ("get_spark_profile_documentation", {"topic": "profiles"}, ("toolId", "content")),
        ("get_osdk_react_components_documentation", {"topic": "components"}, ("toolId", "content")),
    )
    for tool_id, arguments, keys in cases:
        runner.call("platform_qa", workspace, tool_id, arguments, keys)


def _builder_ledger_context() -> RequestContext:
    return RequestContext(
        tenant_id="tenant-builder-ledger",
        actor_user_id="builder-ledger-user",
        roles=("admin",),
        application_id="builder-ledger-app",
        client_id="builder-ledger-client",
        token_scopes=("osdk:connector:fde_exploration:execute",),
        oauth_session_id="builder-ledger-oauth-session",
        request_id="builder-ledger-request",
    )


def _builder_ledger_request() -> FdeMcpToolCall:
    return FdeMcpToolCall(
        application_id="builder-ledger-app",
        session_id="mcp-builder-ledger-session",
        json_rpc_id="builder-ledger-call",
        mode="exploration",
        workspace_ref="tenant:tenant-builder-ledger",
        tool_id="platform.docs.search",
        arguments={"query": "hospital"},
        origin="https://chatgpt.com",
    )


def _builder_ledger_binding(ctx: RequestContext, request: FdeMcpToolCall) -> FdeMcpRequestBinding:
    return FdeMcpRequestBinding(
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.actor_user_id,
        application_id=request.application_id,
        client_id=str(ctx.client_id),
        oauth_session_id=str(ctx.oauth_session_id),
        session_id=request.session_id,
        tool_id=request.tool_id,
        mode=request.mode,
        workspace_ref=request.workspace_ref,
        arguments_hash="sha256:builder-ledger-arguments",
        required_permission="ontology:write",
        origin=str(request.origin),
    )


def _builder_tool_record(
    ctx: RequestContext,
    run_id: str,
    binding: FdeMcpRequestBinding,
) -> AiToolCallRecord:
    return AiToolCallRecord(
        id=f"{run_id}-tool-1",
        tenant_id=ctx.tenant_id,
        ai_run_id=run_id,
        sequence=1,
        tool_id=binding.tool_id,
        tool_version="1",
        arguments_hash=binding.arguments_hash,
        effect="READ",
        authorization_decision="allowed",
        confirmation_policy="none",
        status="succeeded",
        result_hash="sha256:builder-ledger-result",
        linked_action_run_id=None,
        started_at="2026-08-31T00:00:00+00:00",
        completed_at="2026-08-31T00:00:01+00:00",
        error_json=None,
        result_json={"status": "ok"},
    )


def _canonical_catalog() -> dict[str, Any]:
    catalog: dict[str, Any] = {}
    for mode in FDE_MODES:
        for tool in fde_tool_catalog(mode.mode_id, ()):
            existing = catalog.setdefault(tool.tool_id, tool)
            assert existing.input_schema == tool.input_schema
            assert existing.effect == tool.effect
    return catalog


def _create_builder_application(foundry: Any, mode: str) -> tuple[str, dict[str, str]]:
    connector_scope = f"osdk:connector:fde_{mode}:execute"
    additional_resources = _mode_resources(mode)
    application_scopes = (
        connector_scope,
        *(str(scope) for resource in additional_resources for scope in resource["scopes"]),
    )
    client_id = f"client-full-surface-{mode.replace('_', '-')}"
    redirect_uri = f"https://builder.example.test/oauth/{mode}"
    application = foundry.developer_console.create_osdk_application(
        app_api_name=f"FullSurface{mode.title().replace('_', '')}",
        display_name=f"Full surface {mode}",
        resources=[
            {"resourceType": "connector", "resourceApiName": f"fde_{mode}", "scopes": [connector_scope]},
            *additional_resources,
        ],
        idempotency_key=f"mcp-full-surface-app-{mode}",
        ctx=FDE_USER,
    )
    app_id = str(application["application"]["id"])
    foundry.developer_console.create_osdk_application_client(
        app_id,
        client_id=client_id,
        redirect_uris=(redirect_uri,),
        allowed_scopes=application_scopes,
        idempotency_key=f"mcp-full-surface-client-{mode}",
        ctx=FDE_USER,
    )
    verifier = f"mcp-full-surface-verifier-{mode}"
    resource = f"http://testserver/mcp/builder/{app_id}"
    authorized = foundry.auth.osdk_oauth_authorize(
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=_s256(verifier),
        scopes=application_scopes,
        resource=resource,
        resource_application_id=app_id,
        ctx=FDE_USER,
    )
    token = foundry.auth.osdk_oauth_token(
        client_id=client_id,
        code=str(authorized["code"]),
        redirect_uri=redirect_uri,
        code_verifier=verifier,
        resource=resource,
        resource_application_id=app_id,
        ctx=FDE_USER,
    )
    return app_id, {
        "Authorization": f"Bearer {token['accessToken']}",
        "MCP-Protocol-Version": "2025-06-18",
        "X-Request-ID": f"mcp-full-surface-{mode}",
    }


def _initialize_params() -> dict[str, object]:
    return {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "builder-full-surface", "version": "1.0.0"},
    }


def _advertised_tools(tools: object) -> dict[str, dict[str, object]]:
    assert isinstance(tools, list)
    advertised: dict[str, dict[str, object]] = {}
    has_private_approval = False
    for raw_tool in tools:
        assert isinstance(raw_tool, Mapping)
        wire_name = raw_tool.get("name")
        input_schema = raw_tool.get("inputSchema")
        assert isinstance(wire_name, str) and isinstance(input_schema, Mapping)
        if wire_name == "approve_builder_mutation":
            metadata = raw_tool.get("_meta")
            assert isinstance(metadata, Mapping)
            assert metadata.get("openai/visibility") == "private"
            assert metadata.get("ui") == {"visibility": ["app"]}
            has_private_approval = True
            continue
        canonical_id = "fde.tools.search" if wire_name == "search_tools" else wire_name
        assert canonical_id not in advertised
        advertised[canonical_id] = {"name": wire_name, "inputSchema": dict(input_schema)}
    assert has_private_approval is True
    return advertised


def _mode_resources(mode: str) -> list[dict[str, Any]]:
    if mode == "exploration":
        return [
            {"resourceType": "object", "resourceApiName": "Order", "scopes": ["osdk:object:Order:read"]},
            # Traversal needs the link's own read scope on top of the object's: reaching a
            # neighbour is a separate grant from reading the object you started at.
            {
                "resourceType": "link",
                "resourceApiName": "OrderCustomer",
                "scopes": ["osdk:link:OrderCustomer:read"],
            },
        ]
    if mode == "functions_editing":
        return [
            {
                "resourceType": "function",
                "resourceApiName": "echoInputs",
                "scopes": ["osdk:function:echoInputs:execute"],
            }
        ]
    return []


def _tool_call_payload(
    rpc_id: str,
    mode: str,
    workspace_ref: str,
    wire_name: str,
    arguments: Mapping[str, object],
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": f"full-surface-{rpc_id}",
        "method": "tools/call",
        "params": {
            "name": wire_name,
            "arguments": {"mode": mode, "workspaceRef": workspace_ref, "arguments": dict(arguments)},
        },
    }


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _assert_short_lived(value: str) -> None:
    expires_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    remaining = (expires_at - datetime.now(UTC)).total_seconds()
    assert 0 < remaining <= 600


def _assert_single_native_tool_row(foundry: Any, ai_run_id: str, tool_id: str) -> None:
    with foundry.engine.begin() as connection:
        rows = list(
            connection.execute(select(db.ai_tool_calls).where(db.ai_tool_calls.c.ai_run_id == ai_run_id)).mappings()
        )
    assert len(rows) == 1, (tool_id, rows)
    assert rows[0]["tool_id"] == tool_id
    assert rows[0]["status"] == "succeeded"


def _ledger_tool_id(tool_id: str) -> str:
    return "search_tools" if tool_id == "fde.tools.search" else tool_id


class _SurfaceKafkaTopicMetadata:
    partitions: Mapping[int, object] = {0: object(), 1: object()}


class _SurfaceKafkaClusterMetadata:
    brokers: Mapping[int, object] = {1: object(), 2: object()}
    topics: Mapping[str, _SurfaceKafkaTopicMetadata] = {"surface.events": _SurfaceKafkaTopicMetadata()}


class _SurfaceKafkaAdminClient:
    def list_topics(self, *, timeout: float) -> Any:
        assert timeout == 10.0
        return _SurfaceKafkaClusterMetadata()


def _surface_ontology_yaml_after_main_moved() -> str:
    """The active Ontology with one extra object type, which strands the open branch on an old base."""

    extra = """  - apiName: SurfaceAudit
    primaryKey: auditId
    backing: {dataset: clean.mcp_surface_orders, mode: snapshot, primaryKeyColumns: [order_id]}
    properties:
      - {apiName: auditId, column: order_id, type: string, nullable: false}
linkTypes:"""
    return _surface_ontology_yaml().replace("linkTypes:", extra, 1)


def _surface_ontology_yaml() -> str:
    return """
objectTypes:
  - apiName: Order
    primaryKey: orderId
    backing: {dataset: clean.mcp_surface_orders, mode: snapshot, primaryKeyColumns: [order_id]}
    properties:
      - {apiName: orderId, column: order_id, type: string, nullable: false}
      - {apiName: customerId, column: customer_id, type: string}
      - {apiName: status, column: status, type: string, editable: true}
      - {apiName: amount, column: amount, type: integer}
  - apiName: Customer
    primaryKey: customerId
    backing: {dataset: clean.mcp_surface_customers, mode: snapshot, primaryKeyColumns: [customer_id]}
    properties:
      - {apiName: customerId, column: customer_id, type: string, nullable: false}
      - {apiName: name, column: name, type: string}
linkTypes:
  - apiName: OrderCustomer
    from: Order
    to: Customer
    cardinality: many_to_one
    backing: {dataset: clean.mcp_surface_orders, fromKey: order_id, toKey: customer_id}
actionTypes:
  - apiName: ApproveOrder
    target: Order
    parameters: [{apiName: reason, type: string, required: true}]
    permissions: {allowedRoles: [data_engineer]}
    mutations: [{type: setProperty, property: status, value: APPROVED}]
functionTypes:
  - apiName: echoInputs
    displayName: Echo inputs
    version: v1
    runtime: logic_dag
    inputs: [{apiName: note, type: string, required: true}]
    output: {type: string}
    permissions: {allowedRoles: [data_engineer]}
    definition:
      blocks:
        - {blockId: input, kind: Input}
        - blockId: output
          kind: Output
          dependsOn: [input]
          inputs: {fromBlock: input}
"""


def _surface_temp_object_definition() -> dict[str, object]:
    return {
        "apiName": "SurfaceObject",
        "primaryKey": "id",
        "backing": {
            "dataset": "clean.mcp_surface_orders",
            "mode": "snapshot",
            "primaryKeyColumns": ["order_id"],
        },
        "properties": [{"apiName": "id", "column": "order_id", "type": "string", "nullable": False}],
    }


def _surface_temp_link_definition() -> dict[str, object]:
    return {
        "apiName": "SurfaceLink",
        "from": "Order",
        "to": "Customer",
        "cardinality": "many_to_one",
        "backing": {
            "dataset": "clean.mcp_surface_orders",
            "fromKey": "order_id",
            "toKey": "customer_id",
        },
    }


def _surface_temp_action_definition() -> dict[str, object]:
    return {
        "apiName": "SurfaceAction",
        "target": "Order",
        "parameters": [{"apiName": "note", "type": "string", "required": True}],
        "permissions": {"allowedRoles": ["data_engineer"]},
        "mutations": [{"type": "setProperty", "property": "status", "value": "SURFACE"}],
    }


def _surface_marker_definition() -> dict[str, object]:
    return {
        "apiName": "SurfaceProposalMarker",
        "primaryKey": "id",
        "backing": {
            "dataset": "clean.mcp_surface_orders",
            "mode": "snapshot",
            "primaryKeyColumns": ["order_id"],
        },
        "properties": [{"apiName": "id", "column": "order_id", "type": "string", "nullable": False}],
    }


def _surface_pipeline_graph() -> dict[str, object]:
    columns = [
        {"name": "order_id", "type": "string", "nullable": False},
        {"name": "amount", "type": "int", "nullable": True},
    ]
    return {
        "nodes": [
            {
                "id": "raw_orders",
                "type": "dataset",
                "config": {"datasetRef": "raw.mcp_surface_orders", "schema": columns},
            },
            {
                "id": "clean_sql",
                "type": "sql",
                "config": {
                    "sql": "select order_id, amount from {{ input('raw.mcp_surface_orders') }}",
                    "outputDatasetRef": "work.mcp_surface_orders",
                    "schema": columns,
                },
            },
            {
                "id": "out",
                "type": "output_dataset",
                "config": {"outputDatasetRef": "clean.mcp_surface_pipeline_orders"},
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
