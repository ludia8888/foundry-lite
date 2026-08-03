"""End-to-end proof of the Action IR v2 apply path through the public facade.

A native ``rulesV2`` action (FulfillOrder) modifies its target Order, creates a
second Order, and links them — all in one atomic transaction — via the real
``foundry.actions.apply`` entrypoint. Exercises multi-object/link atomicity,
idempotent replay, and per-action permission on the live pipeline (not fakes).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import ConflictDetected, PermissionDenied

from tests.conftest import DEMO_ROOT

_FULFILL_ORDER_ACTION = """
  - apiName: FulfillOrder
    displayName: Fulfill order
    target: Order
    parameters:
      - apiName: carrier
        type: string
        required: true
    permissions:
      allowedRoles: [ops_manager]
    rulesV2:
      - kind: modifyObject
        ruleId: close-order
        objectType: Order
        target: {kind: parameter, parameter: __target__}
        assignments:
          - property: status
            value: {kind: literal, value: FULFILLED}
          - property: operatorNote
            value: {kind: parameter, parameter: carrier}
      - kind: createObject
        ruleId: mk-fulfillment
        objectType: Order
        primaryKey: {kind: generatedId, strategy: uuid}
        assignments:
          - property: status
            value: {kind: literal, value: SHIPMENT}
          - property: customerId
            value: {kind: objectProperty, parameter: __target__, property: customerId}
      - kind: createLink
        ruleId: link-fulfillment
        linkType: OrderCustomer
        source: {kind: priorRuleOutput, ruleId: mk-fulfillment, output: objectId}
        target: {kind: objectProperty, parameter: __target__, property: customerId}
"""


def _v2_ontology(tmp_path: Path) -> Path:
    ontology = (DEMO_ROOT / "ontology" / "order-customer.yaml").read_text(encoding="utf-8")
    path = tmp_path / "order-customer-v2.yaml"
    path.write_text(ontology + _FULFILL_ORDER_ACTION, encoding="utf-8")
    return path


def _prepare_v2_demo(foundry: FoundryLite, tmp_path: Path) -> RequestContext:
    ctx = demo_admin_context()
    foundry.demo.seed_files()
    foundry.datasets.ensure("raw.erp_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("raw.crm_customers", ctx=ctx, primary_key=["customer_id"])
    foundry.datasets.ensure("clean.orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.order_finance", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.customers", ctx=ctx, primary_key=["customer_id"])
    foundry.demo.register_transforms(ctx)
    foundry.datasets.upload_csv("raw.erp_orders", str(DEMO_ROOT / "data" / "orders.csv"), ctx=ctx)
    foundry.datasets.upload_csv("raw.crm_customers", str(DEMO_ROOT / "data" / "customers.csv"), ctx=ctx)
    foundry.transforms.run("clean_orders", ctx=ctx)
    foundry.transforms.run("clean_order_finance", ctx=ctx)
    foundry.transforms.run("clean_customers", ctx=ctx)
    foundry.ontology.apply(str(_v2_ontology(tmp_path)), ctx=ctx)
    foundry.objects.reindex("Order", ctx=ctx)
    foundry.objects.reindex("Customer", ctx=ctx)
    return ctx


def test_v2_action_commits_modify_create_and_link_atomically(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = _prepare_v2_demo(foundry, tmp_path)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    customer_id = order["properties"]["customerId"]
    original_version = order["objectVersion"]

    response = foundry.actions.apply(
        "FulfillOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=original_version,
        params={"carrier": "UPS"},
        idempotency_key="fulfill-happy",
        ctx=ctx,
    )

    # The submission returns its whole-plan summary with the primary target intact.
    plan = response.get("plan")
    assert plan is not None
    assert response["status"] == "succeeded"
    assert response["target"] == {"objectType": "Order", "objectId": "O-1001"}
    assert plan["editCount"] == 3
    assert len(plan["createdObjectIds"]) == 1
    assert plan["linksCreated"] == 1
    assert {edit["operation"] for edit in plan["edits"]} == {"create_object", "set_property", "create_link"}
    created_id = plan["createdObjectIds"][0]

    # Read-your-writes: target modified, the second object created, and the link live.
    fulfilled = foundry.objects.get("Order", "O-1001", ctx=ctx)
    assert fulfilled["properties"]["status"] == "FULFILLED"
    assert fulfilled["properties"]["operatorNote"] == "UPS"
    assert fulfilled["objectVersion"] == original_version + 1
    created = foundry.objects.get("Order", created_id, ctx=ctx)
    assert created["properties"]["status"] == "SHIPMENT"
    assert created["properties"]["customerId"] == customer_id
    links = foundry.objects.links("Order", created_id, "OrderCustomer", ctx=ctx)
    assert [link["to"]["objectId"] for link in links] == [customer_id]

    # Exactly one action run, terminal succeeded, with per-object edit rows recorded.
    runs = foundry.operations.list_runs(ctx=ctx)
    run_rows = [row for row in runs["actionRuns"] if row["idempotency_key"] == "fulfill-happy"]
    edit_rows = [row for row in runs["objectEdits"] if str(row.get("idempotency_key", "")).startswith("fulfill-happy:")]
    assert len(run_rows) == 1
    assert run_rows[0]["status"] == "succeeded"
    assert {row["edit_type"] for row in edit_rows} == {"create_object", "set_property", "create_link"}


def test_v2_action_replays_idempotently_without_duplicating_edits(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = _prepare_v2_demo(foundry, tmp_path)
    original_version = foundry.objects.get("Order", "O-1001", ctx=ctx)["objectVersion"]

    first = foundry.actions.apply(
        "FulfillOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=original_version,
        params={"carrier": "UPS"},
        idempotency_key="fulfill-idem",
        ctx=ctx,
    )
    replay = foundry.actions.apply(
        "FulfillOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=original_version,
        params={"carrier": "UPS"},
        idempotency_key="fulfill-idem",
        ctx=ctx,
    )

    first_plan = first.get("plan")
    replay_plan = replay.get("plan")
    assert first_plan is not None and replay_plan is not None
    assert replay.get("idempotentReplay") is True
    assert replay_plan["createdObjectIds"] == first_plan["createdObjectIds"]
    # One run, one created object, one link — the replay committed nothing new.
    fulfilled = foundry.objects.get("Order", "O-1001", ctx=ctx)
    assert fulfilled["objectVersion"] == original_version + 1
    runs = foundry.operations.list_runs(ctx=ctx)
    run_rows = [row for row in runs["actionRuns"] if row["idempotency_key"] == "fulfill-idem"]
    assert len(run_rows) == 1


def test_v2_action_denies_apply_without_the_action_role(foundry: FoundryLite, tmp_path: Path) -> None:
    admin_ctx = _prepare_v2_demo(foundry, tmp_path)
    original_version = foundry.objects.get("Order", "O-1001", ctx=admin_ctx)["objectVersion"]
    viewer = RequestContext(actor_user_id="viewer-1", roles=("viewer",))

    with pytest.raises(PermissionDenied):
        foundry.actions.apply(
            "FulfillOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=original_version,
            params={"carrier": "UPS"},
            idempotency_key="fulfill-denied",
            ctx=viewer,
        )

    # A stale expected version on an authorized call still conflicts (optimistic concurrency preserved).
    with pytest.raises(ConflictDetected):
        foundry.actions.apply(
            "FulfillOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=original_version + 99,
            params={"carrier": "UPS"},
            idempotency_key="fulfill-stale",
            ctx=admin_ctx,
        )
