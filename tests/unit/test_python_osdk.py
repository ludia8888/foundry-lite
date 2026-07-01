from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, cast

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.osdk import osdk_resource
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied, ValidationFailed
from foundry_lite.osdk import (
    ApproveOrder,
    Customer,
    Order,
    OsdkLinkBinding,
    OsdkLinkSet,
    OsdkObject,
    action_type,
    assert_foundry_lite_sdk_fresh,
    object_type,
    sdk_ontology_drift_report,
    sdk_package_manifest,
)

from tests.conftest import prepare_indexed_demo


def test_python_osdk_manifest_and_drift_report() -> None:
    manifest = sdk_package_manifest()

    assert manifest["package_name"] == "foundry-lite"
    assert manifest["ontology_fingerprint"] == "13b1f74e129614c3"
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
    with pytest.raises(ValidationFailed, match="supports only \\$count"):
        foundry(Order, ctx=ctx).aggregate({"select": {"sumAmount": {"amount": {"$sum": "unordered"}}}})


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
    with pytest.raises(ValidationFailed, match="masked property"):
        foundry(Order, ctx=viewer).aggregate(
            {"select": {"count": {"$count": "unordered"}}, "groupBy": {"margin": "exact"}}
        )
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
