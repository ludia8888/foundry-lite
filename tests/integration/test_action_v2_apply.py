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
from foundry_lite.domain.errors import ConflictDetected, NotFound, PermissionDenied

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
    revert:
      enabled: true
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


def test_v2_action_writes_one_queryable_log_and_reverts_all_internal_edits(
    foundry: FoundryLite, tmp_path: Path
) -> None:
    ctx = _prepare_v2_demo(foundry, tmp_path)
    before = foundry.objects.get("Order", "O-1001", ctx=ctx)
    response = foundry.actions.apply(
        "FulfillOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=before["objectVersion"],
        params={"carrier": "UPS"},
        idempotency_key="fulfill-revert",
        ctx=ctx,
    )
    run_id = str(response["actionRunId"])
    created_id = str(response["plan"]["createdObjectIds"][0])

    logs = foundry.actions.logs(ctx=ctx)
    assert logs["monitoring"]["window"]["observedRuns"] == 1
    assert logs["monitoring"]["durationMs"]["terminalSample"] == 1
    assert logs["monitoring"]["failure"] == {"count": 0, "rate": 0.0}
    assert logs["monitoring"]["effects"]["deliveryBacklog"] == 0
    matching = [item for item in logs["items"] if item["actionRunId"] == run_id]
    assert len(matching) == 1
    assert matching[0]["logObject"] == {"objectType": "[LOG] FulfillOrder", "objectId": run_id}
    assert len(matching[0]["editedObjects"]) == 3
    assert matching[0]["revert"] == {"isAllowed": True, "status": "eligible", "revertedByRunId": None}

    eligibility = foundry.actions.revert_eligibility(run_id, ctx=ctx)
    assert eligibility == {
        "actionRunId": run_id,
        "isEligible": True,
        "reason": None,
        "editCount": 3,
        "hasPreservedExternalEffects": False,
        "logEntryId": f"action_log_{run_id}",
    }
    reverted = foundry.actions.revert(run_id, idempotency_key="revert-once", ctx=ctx)
    replay = foundry.actions.revert(run_id, idempotency_key="revert-once", ctx=ctx)
    assert reverted == replay
    assert reverted["status"] == "succeeded"
    assert reverted["revertOfActionRunId"] == run_id
    assert reverted["hasPreservedExternalEffects"] is False

    restored = foundry.objects.get("Order", "O-1001", ctx=ctx)
    assert restored["properties"]["status"] == before["properties"]["status"]
    assert "operatorNote" not in restored["properties"]
    with pytest.raises(NotFound, match="object not found"):
        foundry.objects.get("Order", created_id, ctx=ctx)
    updated_logs = foundry.actions.logs(ctx=ctx)["items"]
    original = next(item for item in updated_logs if item["actionRunId"] == run_id)
    revert_log = next(item for item in updated_logs if item["actionRunId"] == reverted["actionRunId"])
    assert original["revert"]["status"] == "reverted"
    assert original["revert"]["revertedByRunId"] == reverted["actionRunId"]
    assert revert_log["revert"]["isAllowed"] is False


def test_v2_action_revert_is_blocked_after_a_later_object_edit(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = _prepare_v2_demo(foundry, tmp_path)
    before = foundry.objects.get("Order", "O-1001", ctx=ctx)
    original = foundry.actions.apply(
        "FulfillOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=before["objectVersion"],
        params={"carrier": "UPS"},
        idempotency_key="fulfill-before-later-edit",
        ctx=ctx,
    )
    current = foundry.objects.get("Order", "O-1001", ctx=ctx)
    foundry.actions.apply(
        "FulfillOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=current["objectVersion"],
        params={"carrier": "DHL"},
        idempotency_key="later-fulfillment",
        ctx=ctx,
    )

    eligibility = foundry.actions.revert_eligibility(str(original["actionRunId"]), ctx=ctx)
    assert eligibility["isEligible"] is False
    assert eligibility["reason"] == "later_edit_touched_affected_object"
