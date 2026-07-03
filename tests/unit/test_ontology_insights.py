"""Ontology resource usage and dependents read models (Ontology Manager parity)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.main import app
from pytest import MonkeyPatch, raises

from tests.conftest import prepare_indexed_demo

ADMIN_HEADERS = {
    "X-Tenant-ID": "tenant-demo",
    "X-User-ID": "user-demo-admin",
    "X-Roles": "admin,data_engineer,ops_manager",
}


def _approve_order(foundry: FoundryLite, ctx: RequestContext, order_id: str, idempotency_key: str) -> None:
    order = foundry.objects.get("Order", order_id, ctx=ctx)
    foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id=order_id,
        expected_object_version=order["objectVersion"],
        params={"reason": "Inventory confirmed"},
        idempotency_key=idempotency_key,
        ctx=ctx,
    )


def _register_insights_app(
    foundry: FoundryLite,
    ctx: RequestContext,
    *,
    app_api_name: str = "OrdersInsightsApp",
    resources: tuple[dict[str, object], ...] = (),
) -> None:
    foundry.developer_console.create_osdk_application(
        app_api_name=app_api_name,
        display_name=app_api_name,
        client_id=f"{app_api_name.lower()}-client",
        resources=resources,
        idempotency_key=f"{app_api_name}-create",
        ctx=ctx,
    )


def test_action_type_usage_counts_succeeded_runs_with_actor(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)
    _approve_order(foundry, ctx, "O-1001", "usage-approve-1")

    usage = foundry.ontology.resource_usage("action_type", "ApproveOrder", ctx=ctx)

    assert usage["resourceType"] == "action_type"
    assert usage["apiName"] == "ApproveOrder"
    assert usage["windowDays"] == 30
    assert usage["actionRuns"]["statusCounts"].get("succeeded", 0) >= 1
    assert usage["actionRuns"]["totalRuns"] >= 1
    assert usage["actionRuns"]["distinctActorCount"] >= 1
    assert usage["actionRuns"]["lastRunAt"] is not None
    # Action types are not indexed, so the index slice stays declared-empty.
    assert usage["indexRuns"]["totalRuns"] == 0


def test_object_type_usage_reports_index_runs_and_targeting_actions(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)
    _approve_order(foundry, ctx, "O-1001", "usage-approve-2")

    usage = foundry.ontology.resource_usage("object_type", "Order", window_days=7, ctx=ctx)

    assert usage["resourceType"] == "object_type"
    assert usage["windowDays"] == 7
    assert usage["indexRuns"]["statusCounts"].get("succeeded", 0) >= 1
    assert usage["indexRuns"]["lastRunAt"] is not None
    assert usage["indexRuns"]["lastSucceededAt"] is not None
    assert usage["actionRuns"]["statusCounts"].get("succeeded", 0) >= 1
    assert usage["actionRuns"]["distinctActorCount"] >= 1


def test_link_type_usage_returns_declared_zero_counts_with_note(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)

    usage = foundry.ontology.resource_usage("link_type", "OrderCustomer", ctx=ctx)

    assert usage["resourceType"] == "link_type"
    assert usage["actionRuns"]["totalRuns"] == 0
    assert usage["indexRuns"]["totalRuns"] == 0
    assert any("no dedicated runtime run ledger" in note for note in usage["notes"])


def test_object_type_dependents_lists_backing_actions_links_materialization(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)

    dependents = foundry.ontology.resource_dependents("object_type", "Order", ctx=ctx)

    assert dependents["backingDatasetRef"] == "clean.orders"
    assert dependents["cdcDatasetRef"] is None
    materialization = dependents["materialization"]
    assert materialization is not None
    assert materialization["dataset"] == "ops.order_current"
    assert dependents["titleProperty"] == "orderId"
    assert "margin" in dependents["classifiedProperties"]
    assert [item["apiName"] for item in dependents["actionTypes"]] == ["ApproveOrder"]
    links = {item["apiName"]: item for item in dependents["linkTypes"]}
    assert links["OrderCustomer"]["fromObjectType"] == "Order"
    assert links["OrderCustomer"]["toObjectType"] == "Customer"
    assert links["OrderCustomer"]["backingDatasetRef"] == "clean.orders"


def test_action_type_dependents_reports_target_writebacks_side_effects(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)

    dependents = foundry.ontology.resource_dependents("action_type", "ApproveOrder", ctx=ctx)

    assert dependents["targetObjectType"] == "Order"
    assert [item["apiName"] for item in dependents["writebacks"]] == ["erpApproveOrder"]
    assert dependents["sideEffectTopics"] == ["ops.order.approved"]
    assert dependents["backingDatasetRef"] is None


def test_link_type_dependents_reports_endpoints_and_backing(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)

    dependents = foundry.ontology.resource_dependents("link_type", "OrderCustomer", ctx=ctx)

    assert dependents["fromObjectType"] == "Order"
    assert dependents["toObjectType"] == "Customer"
    assert dependents["backingDatasetRef"] == "clean.orders"


def test_osdk_application_scopes_appear_in_dependents(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)
    _register_insights_app(
        foundry,
        ctx,
        app_api_name="OrdersInsightsApp",
        resources=(
            {"resourceType": "object", "resourceApiName": "Order", "scopes": ["osdk:object:Order:read"]},
            {
                "resourceType": "action",
                "resourceApiName": "ApproveOrder",
                "scopes": ["osdk:action:ApproveOrder:execute"],
            },
        ),
    )
    _register_insights_app(
        foundry,
        ctx,
        app_api_name="ValidateOnlyApp",
        resources=(
            {
                "resourceType": "action",
                "resourceApiName": "ApproveOrder",
                "scopes": ["osdk:action:ApproveOrder:validate"],
            },
        ),
    )

    object_dependents = foundry.ontology.resource_dependents("object_type", "Order", ctx=ctx)
    action_dependents = foundry.ontology.resource_dependents("action_type", "ApproveOrder", ctx=ctx)

    object_apps = {item["appApiName"]: item for item in object_dependents["osdkApplications"]}
    assert object_apps["OrdersInsightsApp"]["scopes"] == ["osdk:object:Order:read"]
    # The action view only reports apps that can actually execute the action.
    assert [item["appApiName"] for item in action_dependents["osdkApplications"]] == ["OrdersInsightsApp"]
    assert action_dependents["osdkApplications"][0]["scopes"] == ["osdk:action:ApproveOrder:execute"]


def test_unknown_api_name_raises_not_found(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)

    with raises(NotFound):
        foundry.ontology.resource_usage("object_type", "Nope", ctx=ctx)
    with raises(NotFound):
        foundry.ontology.resource_usage("link_type", "Nope", ctx=ctx)
    with raises(NotFound):
        foundry.ontology.resource_dependents("action_type", "Nope", ctx=ctx)


def test_invalid_resource_type_and_window_bounds_raise_validation_failed(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)

    with raises(ValidationFailed):
        foundry.ontology.resource_usage("dataset", "Order", ctx=ctx)
    with raises(ValidationFailed):
        foundry.ontology.resource_dependents("dataset", "Order", ctx=ctx)
    with raises(ValidationFailed):
        foundry.ontology.resource_usage("object_type", "Order", window_days=0, ctx=ctx)
    with raises(ValidationFailed):
        foundry.ontology.resource_usage("object_type", "Order", window_days=366, ctx=ctx)


def test_api_resource_usage_and_dependents_happy_paths(foundry: FoundryLite, monkeypatch: MonkeyPatch) -> None:
    ctx = prepare_indexed_demo(foundry)
    _approve_order(foundry, ctx, "O-1001", "usage-approve-http")
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)

    usage = client.get(
        "/api/ontology/resources/object_type/Order/usage",
        params={"windowDays": 30},
        headers=ADMIN_HEADERS,
    )
    dependents = client.get(
        "/api/ontology/resources/action_type/ApproveOrder/dependents",
        headers=ADMIN_HEADERS,
    )

    assert usage.status_code == 200
    usage_payload = usage.json()
    assert usage_payload["resourceType"] == "object_type"
    assert usage_payload["indexRuns"]["statusCounts"].get("succeeded", 0) >= 1
    assert usage_payload["actionRuns"]["totalRuns"] >= 1
    assert dependents.status_code == 200
    dependents_payload = dependents.json()
    assert dependents_payload["targetObjectType"] == "Order"
    assert [item["apiName"] for item in dependents_payload["writebacks"]] == ["erpApproveOrder"]


def test_api_rejects_bad_inputs_and_missing_resources(foundry: FoundryLite, monkeypatch: MonkeyPatch) -> None:
    ctx = prepare_indexed_demo(foundry)
    del ctx
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)

    bad_resource_type = client.get("/api/ontology/resources/dataset/Order/usage", headers=ADMIN_HEADERS)
    bad_window = client.get(
        "/api/ontology/resources/object_type/Order/usage",
        params={"windowDays": 366},
        headers=ADMIN_HEADERS,
    )
    missing = client.get("/api/ontology/resources/object_type/Missing/usage", headers=ADMIN_HEADERS)
    denied = client.get(
        "/api/ontology/resources/object_type/Order/usage",
        headers={"X-Tenant-ID": "tenant-demo", "X-User-ID": "outsider", "X-Roles": "external"},
    )

    assert bad_resource_type.status_code == 422
    assert bad_window.status_code == 422
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "NOT_FOUND"
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "PERMISSION_DENIED"
