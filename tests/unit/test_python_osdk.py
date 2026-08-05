from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, cast

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.osdk import osdk_resource
from foundry_lite.application.ports.action_effect_executor import ActionEffectExecutionResult
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied, ValidationFailed
from foundry_lite.infrastructure.adapters.action_effect_executor import AllowlistedActionEffectExecutor
from foundry_lite.osdk import (
    ApproveOrder,
    Customer,
    Order,
    OsdkActionInvoker,
    OsdkLinkBinding,
    OsdkLinkSet,
    OsdkObject,
    action_type,
    assert_foundry_lite_sdk_fresh,
    object_type,
    sdk_ontology_drift_report,
    sdk_package_manifest,
)
from foundry_lite_sdk import GeneratedActionEffectClient, GeneratedActionNotificationPolicyClient

from tests.conftest import prepare_indexed_demo


def test_python_osdk_notification_policy_client_uses_governed_registry(foundry: FoundryLite) -> None:
    ctx = RequestContext(actor_user_id="admin-policy", roles=("admin", "data_engineer"))
    client = GeneratedActionNotificationPolicyClient(foundry, ctx)
    created = client.create(
        {
            "policyName": "pythonOps",
            "displayName": "Python operations",
            "deliveryMode": "strict",
            "recipients": [{"userId": "operator-1", "roles": ["ops_manager"]}],
        },
        idempotency_key="python-policy-create",
    )
    assert client.get("pythonOps") == created
    assert [item["policyName"] for item in cast(list[dict[str, object]], client.list()["items"])] == ["pythonOps"]
    updated = client.update(
        "pythonOps",
        {
            "displayName": "Python operations",
            "deliveryMode": "best_effort",
            "recipients": [{"userId": "operator-1", "roles": ["ops_manager"]}],
            "status": "active",
            "expectedFingerprint": str(created["configFingerprint"]),
        },
        idempotency_key="python-policy-update",
    )
    disabled = client.disable(
        "pythonOps",
        expected_fingerprint=str(updated["configFingerprint"]),
        idempotency_key="python-policy-disable",
    )
    assert disabled["status"] == "disabled"


def test_python_osdk_effect_client_preserves_operator_idempotency_and_evidence() -> None:
    calls: list[tuple[str, object]] = []

    class Actions:
        def list_effect_receipts(self, **kwargs):
            calls.append(("list", kwargs["status"]))
            return {"items": []}

        def get_effect_receipt(self, receipt_id, **_kwargs):
            calls.append(("get", receipt_id))
            return {"receiptId": receipt_id}

        def cancel_effect(self, receipt_id, **kwargs):
            calls.append(("cancel", (receipt_id, kwargs["idempotency_key"])))
            return {"receiptId": receipt_id, "status": "cancelled"}

        def retry_effect(self, receipt_id, **kwargs):
            calls.append(("retry", (receipt_id, kwargs["idempotency_key"])))
            return {"receiptId": receipt_id, "status": "retry_wait"}

        def reconcile_effect(self, receipt_id, **kwargs):
            calls.append(("reconcile", (receipt_id, kwargs["resolution"], kwargs["evidence"])))
            return {"receiptId": receipt_id, "status": "succeeded"}

    class Host:
        actions = Actions()

    client = GeneratedActionEffectClient(cast(Any, Host()))
    assert client.list(status="dead_letter") == {"items": []}
    assert client.get("receipt-1") == {"receiptId": "receipt-1"}
    assert client.cancel("receipt-1", reason="obsolete", idempotency_key="cancel-1")["status"] == "cancelled"
    assert client.retry("receipt-1", idempotency_key="retry-1")["status"] == "retry_wait"
    reconciled = client.reconcile(
        "receipt-1",
        {
            "resolution": "confirmed_delivered",
            "evidence": {
                "verificationMethod": "provider_query",
                "providerReference": "case-1",
                "verifiedAt": "2026-08-05T12:00:00Z",
                "externalExecutionId": "provider-1",
            },
        },
        idempotency_key="reconcile-1",
    )
    assert reconciled["status"] == "succeeded"
    assert calls[-1][1] == (
        "receipt-1",
        "confirmed_delivered",
        {
            "verificationMethod": "provider_query",
            "providerReference": "case-1",
            "verifiedAt": "2026-08-05T12:00:00Z",
            "externalExecutionId": "provider-1",
        },
    )


def test_python_osdk_manifest_and_drift_report() -> None:
    manifest = sdk_package_manifest()

    assert manifest["package_name"] == "foundry-lite"
    assert manifest["ontology_fingerprint"] == "16d0f0b5844eab24"
    assert manifest["object_api_names"] == ("Order", "Customer")
    assert manifest["link_api_names"] == ("OrderCustomer",)

    matching_catalog = {
        "ontologyContractFingerprint": manifest["ontology_fingerprint"],
        "objectTypes": [
            {"apiName": "Order", "actions": [{"apiName": "ApproveOrder"}]},
            {"apiName": "Customer", "actions": []},
        ],
        "linkTypes": [{"apiName": "OrderCustomer"}],
    }
    fresh_report = assert_foundry_lite_sdk_fresh(matching_catalog)
    assert fresh_report["requires_sdk_regeneration"] is False
    assert fresh_report["is_fingerprint_matched"] is True
    assert fresh_report["reason_codes"] == ()

    stale_catalog = {
        "objectTypes": [
            {"apiName": "Order", "actions": [{"apiName": "ApproveOrder"}]},
            {"apiName": "SupplierRisk", "actions": [{"apiName": "EscalateSupplier"}]},
        ],
        "linkTypes": [],
    }
    stale_report = sdk_ontology_drift_report(stale_catalog)
    assert stale_report["requires_sdk_regeneration"] is True
    assert stale_report["dynamic_only_object_api_names"] == ("SupplierRisk",)
    assert stale_report["static_only_object_api_names"] == ("Customer",)
    assert stale_report["dynamic_only_action_api_names"] == ("EscalateSupplier",)
    assert stale_report["static_only_link_api_names"] == ("OrderCustomer",)
    with pytest.raises(ValidationFailed, match="Python OSDK mirror is stale"):
        assert_foundry_lite_sdk_fresh(stale_catalog)


def test_python_osdk_object_set_query_compiles_filters_order_and_pages(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)

    page = (
        foundry(Order, ctx=ctx)
        .where(status={"in": ["PENDING", "REVIEW"]}, amount={"gte": 700})
        .order_by(amount="desc")
        .fetch_page(page_size=1)
    )

    assert [item.object_id for item in page["items"]] == ["O-1001"]
    assert page["nextPageToken"] is not None

    second_page = (
        foundry(Order, ctx=ctx)
        .where(status={"in": ["PENDING", "REVIEW"]}, amount={"gte": 700})
        .order_by(amount="desc")
        .fetch_page(page_size=1, page_token=page["nextPageToken"])
    )
    assert [item.object_id for item in second_page["items"]] == ["O-1002"]


def test_python_osdk_query_fails_closed_for_invalid_property_and_operator(foundry: FoundryLite) -> None:
    prepare_indexed_demo(foundry)

    with pytest.raises(ValidationFailed, match="unknown property"):
        foundry(Order).where(missing={"eq": "PENDING"})

    with pytest.raises(ValidationFailed, match="unsupported Python OSDK filter operator"):
        foundry(Order).where(status={"starts_with": "P"})


def test_python_osdk_aggregate_count_group_by_uses_object_query_boundary(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)

    result = foundry(Customer, ctx=ctx).aggregate(
        {"select": {"count": {"$count": "unordered"}}, "group_by": {"region": "exact"}}
    )

    counts_by_region = {bucket["group"]["region"]: bucket["metrics"][0]["value"] for bucket in result["data"]}
    assert result["excludedItems"] == 0
    assert counts_by_region == {"EU": 1, "NA": 1}


def test_python_osdk_aggregate_count_and_fail_closed_edges(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)

    result = (
        foundry(Order, ctx=ctx).where(status={"in": ["PENDING", "REVIEW"]}).aggregate({"$select": {"$count": "asc"}})
    )

    assert result == {"excludedItems": 0, "data": [{"group": {}, "metrics": [{"name": "count", "value": 2}]}]}
    with pytest.raises(ValidationFailed, match="unknown groupBy property"):
        foundry(Order, ctx=ctx).aggregate(
            {"select": {"count": {"$count": "unordered"}}, "groupBy": {"region": "exact"}}
        )
    with pytest.raises(ValidationFailed, match="unknown metric property"):
        foundry(Order, ctx=ctx).aggregate({"select": {"sumMissing": {"missing": {"$sum": "unordered"}}}})


def test_python_osdk_aggregate_numeric_metrics_run_server_side(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)

    summed = foundry(Order, ctx=ctx).aggregate({"select": {"sumAmount": {"amount": {"$sum": "unordered"}}}})

    assert summed == {
        "excludedItems": 0,
        "data": [{"group": {}, "metrics": [{"name": "sumAmount", "value": 2300.0}]}],
    }

    grouped = foundry(Order, ctx=ctx).aggregate(
        {
            "select": {"count": {"$count": "unordered"}, "maxAmount": {"amount": {"$max": "unordered"}}},
            "groupBy": {"customerId": "exact"},
        }
    )

    assert grouped["data"] == [
        {
            "group": {"customerId": "C-100"},
            "metrics": [{"name": "count", "value": 2}, {"name": "maxAmount", "value": 1200.0}],
        },
        {
            "group": {"customerId": "C-101"},
            "metrics": [{"name": "count", "value": 1}, {"name": "maxAmount", "value": 800.0}],
        },
    ]


def test_python_osdk_instance_link_and_bound_action_use_existing_boundaries(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry(Order, ctx=ctx).fetch_one("O-1001")

    customers = order.links.customer.fetch_page(page_size=10)

    assert [item.object_id for item in customers["items"]] == ["C-100"]
    assert customers["items"][0].properties["region"] == "NA"

    validation = order.actions.approve_order.validate_action({"reason": "approved through python osdk"})

    assert validation["result"] == "VALID"
    assert validation["target"]["currentObjectVersion"] == order.object_version
    assert validation["parameters"]["reason"]["result"] == "VALID"

    result = order.actions.approve_order.apply_action(
        {"reason": "approved through python osdk"},
        idempotency_key="python-osdk-approve-order",
    )

    assert result["status"] == "succeeded"
    assert result["target"] == {"objectType": "Order", "objectId": "O-1001"}
    edits = result.get("edits")
    cache_refresh = result.get("cacheRefresh")
    assert edits is not None
    assert cache_refresh is not None
    assert edits["changedProperties"] == ["operatorNote", "status"]
    assert cache_refresh["objectKeys"] == ["objects:Order:O-1001"]

    refreshed_order = foundry(Order, ctx=ctx).fetch_one("O-1001")
    invalid = refreshed_order.actions.approve_order.validate_action({"reason": "already approved"})
    assert invalid["result"] == "INVALID"
    assert invalid["submissionCriteria"][0]["message"] == "Only pending/review orders can be approved"


def test_python_osdk_action_plan_dry_run_and_durable_run_evidence(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)
    effect_adapter = AllowlistedActionEffectExecutor()
    effect_adapter.register_target(
        "mock_erp_simulator",
        lambda _request: ActionEffectExecutionResult("delivered", "mock-writeback-1", {"status": 202}, {}),
        allowed_kinds=frozenset({"webhook"}),
    )
    foundry._services.action_effects.action_effect_executor = effect_adapter
    order = foundry(Order, ctx=ctx).fetch_one("O-1001")
    action = order.actions.approve_order
    params = {"reason": "governed Python OSDK durable run"}
    plan = action.plan_action(params)
    dry_run = action.dry_run_action(params)
    run = action.start_action_run(
        params,
        idempotency_key="python-osdk-durable-approve",
        wait_seconds=1,
    )
    invoker = foundry(ApproveOrder, ctx=ctx)
    catalog = invoker.list_actions(limit=10)
    definition = invoker.get_action()
    schema = invoker.action_schema()
    observed = invoker.get_run(str(run["actionRunId"]))
    listed = invoker.list_runs(limit=10)
    events = invoker.run_events(str(run["actionRunId"]))
    logs = invoker.logs(limit=10)

    assert plan["isDryRun"] is False
    assert dry_run["isDryRun"] is True
    assert plan["target"] == dry_run["target"]
    assert catalog["items"][0]["apiName"] == "ApproveOrder"
    assert definition["apiName"] == "ApproveOrder"
    assert schema["x-foundry-action"] == "ApproveOrder"
    assert observed["actionRunId"] == run["actionRunId"]
    assert observed["status"] == "succeeded"
    assert listed["items"][0]["actionRunId"] == run["actionRunId"]
    assert events["events"][-1]["event"] == "action.run.succeeded"
    assert logs["items"][0]["actionRunId"] == run["actionRunId"]
    eligibility = invoker.revert_eligibility(str(run["actionRunId"]))
    assert eligibility["isEligible"] is False


def test_python_osdk_branch_action_routes_branch_and_idempotency_coordinates() -> None:
    calls: dict[str, dict[str, object]] = {}

    class FakeActions:
        def plan(self, action_api_name: str, **kwargs: object) -> dict[str, object]:
            calls["plan"] = {"actionApiName": action_api_name, **kwargs}
            return {"branchId": kwargs["branch_id"]}

        def execute_branch(self, action_api_name: str, **kwargs: object) -> dict[str, object]:
            calls["execute"] = {"actionApiName": action_api_name, **kwargs}
            return {"status": "succeeded", "branchId": kwargs["branch_id"]}

        def branch_link(self, branch_id: str, link_type: str, from_id: str, to_id: str, **kwargs: object):
            calls["link"] = {
                "branchId": branch_id,
                "linkType": link_type,
                "fromObjectId": from_id,
                "toObjectId": to_id,
                **kwargs,
            }
            return {"isDeleted": False}

        def branch_diff(self, branch_id: str, **kwargs: object):
            calls["diff"] = {"branchId": branch_id, **kwargs}
            return {"linkItems": []}

    class FakeHost:
        actions = FakeActions()

    invoker = OsdkActionInvoker(cast(Any, FakeHost()), ApproveOrder)
    plan = invoker.plan_action(
        {"reason": "scenario"},
        branch_id="ontology-branch-1",
        object_id="O-1",
        expected_object_version=7,
    )
    result = invoker.apply_on_branch(
        {"reason": "scenario"},
        branch_id="ontology-branch-1",
        object_id="O-1",
        expected_object_version=7,
        idempotency_key="python-osdk-branch-1",
    )
    link = invoker.branch_link("ontology-branch-1", "OrderCustomer", "O-1", "C-1")
    diff = invoker.branch_diff("ontology-branch-1")

    assert plan["branchId"] == "ontology-branch-1"
    assert result["status"] == "succeeded"
    assert link["isDeleted"] is False
    assert diff["linkItems"] == []
    assert calls["plan"]["branch_id"] == "ontology-branch-1"
    assert calls["execute"]["idempotency_key"] == "python-osdk-branch-1"
    assert calls["link"]["toObjectId"] == "C-1"


def test_python_osdk_function_batch_run_uses_durable_action_surface() -> None:
    calls: dict[str, object] = {}

    class FakeActions:
        def start_batch_run(self, action_api_name: str, **kwargs: object) -> dict[str, object]:
            calls.update({"actionApiName": action_api_name, **kwargs})
            return {"actionRunId": "run-batch-1", "status": "queued"}

    class FakeHost:
        actions = FakeActions()

    invoker = OsdkActionInvoker(cast(Any, FakeHost()), ApproveOrder)
    result = invoker.start_action_batch_run(
        [{"objectId": "O-1", "expectedObjectVersion": 3, "params": {"reason": "batch"}}],
        idempotency_key="python-function-batch-1",
        wait_seconds=5,
    )

    assert result == {"actionRunId": "run-batch-1", "status": "queued"}
    assert calls["actionApiName"] == "ApproveOrder"
    assert calls["object_type"] == "Order"
    assert calls["idempotency_key"] == "python-function-batch-1"
    assert calls["wait_seconds"] == 5


def test_python_osdk_action_invoker_requires_idempotency_and_typed_params(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry(Order, ctx=ctx).fetch_one("O-1001")

    with pytest.raises(ValidationFailed, match="missing required parameter"):
        foundry(ApproveOrder, ctx=ctx).apply_action({}, idempotency_key="missing-param", target=order)

    with pytest.raises(ValidationFailed, match="unknown parameter"):
        foundry(ApproveOrder, ctx=ctx).apply_action(
            {"reason": "ok", "unexpected": "nope"},
            idempotency_key="unknown-param",
            target=order,
        )

    with pytest.raises(ValidationFailed, match="requires idempotency_key"):
        foundry(ApproveOrder, ctx=ctx).apply_action({"reason": "ok"}, idempotency_key="", target=order)


def test_python_osdk_preserves_masking_and_action_permission_boundaries(foundry: FoundryLite) -> None:
    prepare_indexed_demo(foundry)
    viewer = RequestContext(actor_user_id="viewer", roles=("viewer",))

    order = foundry(Order, ctx=viewer).fetch_one("O-1001")

    assert order.properties["margin"] == "***MASKED***"
    with pytest.raises(ValidationFailed, match="masked property"):
        foundry(Order, ctx=viewer).where(margin={"gte": 1}).fetch_page()
    with pytest.raises(PermissionDenied, match="masked property"):
        foundry(Order, ctx=viewer).aggregate(
            {"select": {"count": {"$count": "unordered"}}, "groupBy": {"margin": "exact"}}
        )
    with pytest.raises(PermissionDenied, match="masked property"):
        foundry(Order, ctx=viewer).aggregate({"select": {"sumMargin": {"margin": {"$sum": "unordered"}}}})
    with pytest.raises(PermissionDenied):
        order.actions.approve_order.apply_action(
            {"reason": "viewer should not approve"},
            idempotency_key="viewer-denied",
        )


def test_python_osdk_dynamic_resources_keep_runtime_validation(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)
    dynamic_customer = foundry(Customer, ctx=ctx).where(region="NA").fetch_page(page_size=10)

    assert [item.object_id for item in dynamic_customer["items"]] == ["C-100"]


def test_python_osdk_iterators_and_edge_validation_paths(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)

    iterated = [item.object_id for item in foundry(Order, ctx=ctx).where(status="PENDING").iterate(page_size=1)]
    async_iterated = asyncio.run(_async_order_ids(foundry, ctx))

    assert iterated == ["O-1001"]
    assert async_iterated == iterated
    with pytest.raises(ValidationFailed, match="exactly one operator"):
        foundry(Order, ctx=ctx).where(status={"eq": "PENDING", "in": ["PENDING"]})
    with pytest.raises(ValidationFailed, match="logical filter must be a list"):
        foundry(Order, ctx=ctx).where({"and": "not-a-list"})
    with pytest.raises(ValidationFailed, match="order_by direction"):
        foundry(Order, ctx=ctx).order_by(status="sideways")
    with pytest.raises(ValidationFailed, match="unknown property"):
        foundry(Order, ctx=ctx).order_by(missing="asc")
    with pytest.raises(AttributeError):
        _missing_link = foundry(Order, ctx=ctx).fetch_one("O-1001").links.missing_link
    with pytest.raises(AttributeError):
        _missing_action = foundry(Order, ctx=ctx).fetch_one("O-1001").actions.missing_action
    with pytest.raises(ValidationFailed, match="unknown Python OSDK resource"):
        osdk_resource(foundry, cast(Any, object()), ctx=ctx)


def test_python_osdk_action_invoker_explicit_target_and_mismatch_paths(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry(Order, ctx=ctx).fetch_one("O-1001")

    validation = foundry(ApproveOrder, ctx=ctx).validate_action(
        {"reason": "explicit target"},
        object_id=order.object_id,
        expected_object_version=order.object_version,
    )

    assert validation["result"] == "VALID"
    with pytest.raises(ValidationFailed, match="target requires object_id"):
        foundry(ApproveOrder, ctx=ctx).validate_action({"reason": "missing target"})
    with pytest.raises(ValidationFailed, match="target object type mismatch"):
        foundry(ApproveOrder, ctx=ctx).validate_action(
            {"reason": "wrong target"},
            target=foundry(Customer, ctx=ctx).fetch_one("C-100"),
        )


def test_python_osdk_link_set_skips_missing_targets_and_page_tokens() -> None:
    client = _FakeOsdkHost()
    source = OsdkObject(
        client,
        None,
        "Order",
        "O-404",
        1,
        {"orderId": "O-404"},
    )
    link_set = OsdkLinkSet(source, OsdkLinkBinding("customer", "OrderCustomer", object_type("Customer")))

    page = link_set.fetch_page(page_size=5)
    second_page = link_set.fetch_page(page_size=5, page_token="cursor-after-first-page")

    assert [item.object_id for item in page["items"]] == ["C-100"]
    assert second_page == {"items": (), "nextPageToken": None}
    assert client.get_calls == [("Customer", "C-100")]


def test_python_osdk_resource_factory_returns_action_invoker_for_dynamic_action(foundry: FoundryLite) -> None:
    action = action_type("ApproveOrder", target_object_type="Order", parameter_names=("reason",))

    invoker = osdk_resource(foundry, action, ctx=RequestContext())

    assert invoker.action_type.api_name == "ApproveOrder"


class _FakeObjects:
    def __init__(self) -> None:
        self.get_calls: list[tuple[str, str]] = []

    def links(self, object_type: str, object_id: str, link_api_name: str, *, ctx: RequestContext | None = None) -> list:
        del object_type, object_id, link_api_name, ctx
        return [
            {"to": {"objectType": "Customer", "objectId": "C-100"}},
            {"to": {"objectType": "Customer", "objectId": "C-missing", "targetMissing": True}},
        ]

    def get(self, object_type: str, object_id: str, *, ctx: RequestContext | None = None) -> Mapping[str, object]:
        del ctx
        self.get_calls.append((object_type, object_id))
        return {
            "objectType": object_type,
            "objectId": object_id,
            "objectVersion": 3,
            "properties": {"customerId": object_id},
        }


class _FakeOsdkHost:
    def __init__(self) -> None:
        self.objects = _FakeObjects()

    @property
    def get_calls(self) -> list[tuple[str, str]]:
        return self.objects.get_calls


async def _async_order_ids(foundry: FoundryLite, ctx: RequestContext) -> list[str]:
    return [item.object_id async for item in foundry(Order, ctx=ctx).where(status="PENDING").async_iter(page_size=1)]
