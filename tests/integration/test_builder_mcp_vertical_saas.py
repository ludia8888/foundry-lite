"""Real Builder MCP proof for four business-language SaaS verticals."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from fastapi.testclient import TestClient
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.main import app

from tests.integration.test_ai_fde_platform import (
    FDE_USER,
    _approve_mcp_challenge,
    _builder_mcp_application,
    _builder_session_headers,
    _mcp_tool_call_payload,
)
from tests.unit.test_fde_domain_os_vertical_matrix import VERTICAL_SPECS

VERTICAL_PRODUCTS = {
    "hospital-operations": ("CareFlow", "CareVisit", "CompleteOrderedTest"),
    "tax-accounting": ("LedgerFlow", "FilingCase", "ApproveFiling"),
    "crm-operations": ("RevenueFlow", "SalesOpportunity", "RequestContractReview"),
    "hr-operations": ("PeopleFlow", "EmployeeJourney", "CloseOffboarding"),
}


def test_builder_mcp_generates_four_distinct_commercial_saas_products(
    foundry: Any,
    monkeypatch: Any,
) -> None:
    """Generate hospital, accounting, CRM, and HR products through JSON-RPC only."""

    foundry.ontology.apply_text("objectTypes: []\nactionTypes: []\nlinkTypes: []\n", ctx=FDE_USER)
    app_id, headers = _builder_mcp_application(foundry, monkeypatch, "osdk_react")
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    session_headers = _builder_session_headers(client, app_id, headers)
    workspace_ref = f"osdk-app:{app_id}"
    listed = client.post(
        f"/mcp/builder/{app_id}",
        headers=session_headers,
        json={"jsonrpc": "2.0", "id": "vertical-tools", "method": "tools/list", "params": {}},
    )
    tool_names = {item["name"] for item in listed.json()["result"]["tools"]}
    assert {"pilot.application.plan", "pilot.application.generate"}.issubset(tool_names)

    generated_products = []
    for spec in _target_specs():
        product_name, primary_record, approval_action = VERTICAL_PRODUCTS[str(spec["id"])]
        plan = _call_plan(client, app_id, session_headers, workspace_ref, spec, product_name)
        generated = _call_generate(client, app_id, session_headers, headers, workspace_ref, spec, plan)
        bundle = foundry.aip.get_pilot_application(str(generated["resource"]["rid"]), ctx=FDE_USER)
        _assert_commercial_bundle(bundle, product_name, primary_record, approval_action)
        generated_products.append(
            {
                "name": product_name,
                "resourceRid": generated["resource"]["rid"],
                "projectId": bundle["project"]["id"],
                "applicationId": bundle["osdkApplication"]["application"]["id"],
                "ontologyBranchId": bundle["ontologyBranch"]["id"],
                "operatingPath": bundle["operatingPath"],
            }
        )

    assert len(generated_products) == 4
    for key in ("resourceRid", "projectId", "applicationId", "ontologyBranchId", "operatingPath"):
        assert len({str(product[key]) for product in generated_products}) == 4


def _target_specs() -> list[Mapping[str, object]]:
    return [spec for spec in VERTICAL_SPECS if str(spec["id"]) in VERTICAL_PRODUCTS]


def _call_plan(
    client: TestClient,
    app_id: str,
    session_headers: dict[str, str],
    workspace_ref: str,
    spec: Mapping[str, object],
    product_name: str,
) -> dict[str, Any]:
    response = client.post(
        f"/mcp/builder/{app_id}",
        headers=session_headers,
        json=_mcp_tool_call_payload(
            f"plan-{spec['id']}",
            "osdk_react",
            workspace_ref,
            "pilot.application.plan",
            {
                "applicationName": product_name,
                "domainDescription": spec["description"],
                "domainBrief": spec["brief"],
            },
        ),
    )
    body = response.json()
    assert response.status_code == 200 and "error" not in body, body
    plan = cast(dict[str, Any], body["result"]["structuredContent"])
    assert plan["domainOsBlueprint"]["readiness"]["isReady"] is True
    assert plan["mcpExecution"] == {"mode": "osdk_react", "workspaceRef": workspace_ref}
    return plan


def _call_generate(
    client: TestClient,
    app_id: str,
    session_headers: dict[str, str],
    headers: dict[str, str],
    workspace_ref: str,
    spec: Mapping[str, object],
    plan: dict[str, Any],
) -> dict[str, Any]:
    payload = _mcp_tool_call_payload(
        f"generate-{spec['id']}",
        "osdk_react",
        workspace_ref,
        "pilot.application.generate",
        {"plan": plan, "idempotencyKey": f"mcp-saas-{spec['id']}"},
    )
    challenge_response = client.post(f"/mcp/builder/{app_id}", headers=session_headers, json=payload)
    challenge = challenge_response.json()["result"]["structuredContent"]
    assert challenge_response.status_code == 200
    assert challenge["status"] == "approval_required"
    receipt = _approve_mcp_challenge(client, app_id, str(challenge["challengeId"]), headers)
    payload["params"]["arguments"]["confirmationReceipt"] = receipt
    response = client.post(f"/mcp/builder/{app_id}", headers=session_headers, json=payload)
    body = response.json()
    assert response.status_code == 200 and "error" not in body, body
    generated = cast(dict[str, Any], body["result"]["structuredContent"])
    assert generated.get("status") == "generated_on_branch", generated
    assert generated["generatedFiles"]["delivery"] == "governed_resource"
    return generated


def _assert_commercial_bundle(
    bundle: Mapping[str, Any],
    product_name: str,
    primary_record: str,
    approval_action: str,
) -> None:
    definition = bundle["businessSystemDefinition"]
    workshop = definition["experience"]["workshopApp"]
    product = workshop["product"]
    source = bundle["reactFiles"]["src/App.tsx"]
    package_name = bundle["consumerOsdk"]["packageName"]
    records = {item["apiName"] for item in definition["businessModel"]["records"]}
    actions = {item["apiName"] for item in definition["businessModel"]["actions"]}

    assert definition["schemaVersion"] == "foundry-lite-business-system-definition/v3"
    assert workshop["version"] == 1
    assert product["schemaVersion"] == "foundry-lite-commercial-product/v1"
    assert product["name"] == product_name
    assert product["audiences"]
    assert {group["id"] for group in product["capabilityGroups"]} >= {"operations", "insights", "governance"}
    assert product["onboarding"][-1]["status"] == "blocked"
    assert primary_record in records
    assert approval_action in actions
    assert bundle["status"] == "generated_on_branch"
    assert bundle["deploymentPlan"]["status"] == "awaiting_ontology_review"
    assert bundle["deploymentPlan"]["workshopPath"].startswith("/workshop/")
    assert bundle["operatingPath"].startswith("/apps/")
    assert f"{package_name}/react" in source
    assert "@foundry-lite/sdk" not in source
