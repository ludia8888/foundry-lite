"""End-to-end proof of the Action IR v2 apply path through the public facade.

A native ``rulesV2`` action (FulfillOrder) modifies its target Order, creates a
second Order, and links them — all in one atomic transaction — via the real
``foundry.actions.apply`` entrypoint. Exercises multi-object/link atomicity,
idempotent replay, and per-action permission on the live pipeline (not fakes).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import ConflictDetected, NotFound, PermissionDenied
from foundry_lite.infrastructure import schema as db
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.main import app
from sqlalchemy import insert

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


def test_action_run_history_includes_sync_runs_for_operator_but_not_viewer(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    admin = _prepare_v2_demo(foundry, tmp_path)
    operator = RequestContext(
        tenant_id=admin.tenant_id,
        actor_user_id="action-history-operator",
        request_id="action-history-operator-request",
        roles=("ops_manager",),
    )
    order = foundry.objects.get("Order", "O-1001", ctx=operator)

    applied = foundry.actions.apply(
        "FulfillOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"carrier": "UPS"},
        idempotency_key="sync-run-history-1",
        ctx=operator,
    )

    page = foundry.actions.list_runs(ctx=operator)
    assert page["items"][0]["actionRunId"] == applied["actionRunId"]
    assert page["items"][0]["orchestration"]["dispatchStatus"] == "not_required"
    viewer = RequestContext(
        tenant_id=admin.tenant_id,
        actor_user_id="action-history-viewer",
        request_id="action-history-viewer-request",
        roles=("viewer",),
    )
    with pytest.raises(PermissionDenied, match="action:run:read"):
        foundry.actions.list_runs(ctx=viewer)


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
    foundry: FoundryLite, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    assert logs["monitoring"]["window"]["days"] == 30
    assert logs["monitoring"]["failure"] == {
        "count": 0,
        "rate": 0.0,
        "terminalSample": 1,
        "byStatus": {},
        "byErrorKind": {},
    }
    assert logs["monitoring"]["effects"]["deliveryBacklog"] == 0
    assert logs["monitoring"]["alerts"]["active"] == []
    matching = [item for item in logs["items"] if item["actionRunId"] == run_id]
    assert len(matching) == 1
    assert matching[0]["logObject"] == {"objectType": "[LOG] FulfillOrder", "objectId": run_id}
    assert len(matching[0]["editedObjects"]) == 3
    assert matching[0]["revert"] == {"isAllowed": True, "status": "eligible", "revertedByRunId": None}

    log_object_type = "[LOG] FulfillOrder"
    catalog_log = next(
        item for item in foundry.ontology.catalog(ctx=ctx)["objectTypes"] if item["apiName"] == log_object_type
    )
    assert catalog_log["primaryKeyProperty"] == "actionRunId"
    assert catalog_log["backing"] == {"mode": "action_log", "actionType": "FulfillOrder"}
    log_link = next(
        item
        for item in foundry.ontology.catalog(ctx=ctx)["linkTypes"]
        if item["apiName"] == "[LOG LINK] FulfillOrder::Order"
    )
    assert log_link == {
        "apiName": "[LOG LINK] FulfillOrder::Order",
        "displayName": "Fulfill order edited Order",
        "fromObjectType": log_object_type,
        "toObjectType": "Order",
        "cardinality": "many",
        "backing": {"mode": "action_log", "actionType": "FulfillOrder"},
    }
    object_log = foundry.objects.get(log_object_type, run_id, ctx=ctx)
    assert object_log["properties"]["status"] == "succeeded"
    assert object_log["properties"]["editedObjectCount"] == 3
    edited_order_links = foundry.objects.links(log_object_type, run_id, "[LOG LINK] FulfillOrder::Order", ctx=ctx)
    assert {item["to"]["objectId"] for item in edited_order_links} == {"O-1001", created_id}
    queried_logs = foundry.objects.query(
        log_object_type,
        filter_ast={"op": "eq", "property": "status", "value": "succeeded"},
        search_text="UPS",
        ctx=ctx,
    )
    assert [item["objectId"] for item in queried_logs["items"]] == [run_id]
    aggregate = foundry.objects.aggregate(
        log_object_type,
        group_by=["status"],
        select=[{"name": "runCount", "function": "count"}],
        ctx=ctx,
    )
    assert aggregate == {
        "groups": [{"key": {"status": "succeeded"}, "metrics": {"runCount": 1}}],
        "totalGroups": 1,
    }
    application = foundry.developer_console.create_osdk_application(
        app_api_name="actionLogReader",
        display_name="Action Log Reader",
        client_id="action-log-reader-client",
        resources=[
            {
                "resourceType": "action",
                "resourceApiName": "FulfillOrder",
                "scopes": ["osdk:action:FulfillOrder:validate"],
            }
        ],
        idempotency_key="create-action-log-reader",
        ctx=ctx,
    )
    scoped_ctx = RequestContext(
        actor_user_id=ctx.actor_user_id,
        roles=ctx.roles,
        application_id=str(application["application"]["id"]),
        client_id="action-log-reader-client",
        token_scopes=("osdk:action:FulfillOrder:validate",),
    )
    assert foundry.objects.get(log_object_type, run_id, ctx=scoped_ctx)["objectId"] == run_id
    denied_ctx = RequestContext(
        actor_user_id=ctx.actor_user_id,
        roles=ctx.roles,
        application_id=str(application["application"]["id"]),
        client_id="action-log-reader-client",
        token_scopes=(),
    )
    with pytest.raises(PermissionDenied, match="OSDK application scope denied"):
        foundry.objects.get(log_object_type, run_id, ctx=denied_ctx)

    monkeypatch.setattr(api_runtime, "foundry", foundry)
    api_client = TestClient(app)
    api_headers = {
        "X-Tenant-ID": ctx.tenant_id,
        "X-User-ID": ctx.actor_user_id,
        "X-Roles": ",".join(ctx.roles),
    }
    encoded_log_path = "/api/objects/%5BLOG%5D%20FulfillOrder"
    api_log = api_client.get(f"{encoded_log_path}/{run_id}", headers=api_headers)
    api_query = api_client.post(
        f"{encoded_log_path}/query",
        headers=api_headers,
        json={"filter": {"op": "eq", "property": "status", "value": "succeeded"}},
    )
    api_aggregate = api_client.post(
        f"{encoded_log_path}/aggregate",
        headers=api_headers,
        json={"groupBy": ["status"], "select": [{"name": "runCount", "function": "count"}]},
    )
    assert api_log.status_code == 200
    assert api_log.json()["objectId"] == run_id
    assert [item["objectId"] for item in api_query.json()["items"]] == [run_id]
    assert api_aggregate.json()["groups"][0]["metrics"] == {"runCount": 1}

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
    reverted_log_object = foundry.objects.get(log_object_type, run_id, ctx=ctx)
    assert reverted_log_object["objectVersion"] == 2
    assert reverted_log_object["properties"]["revertStatus"] == "reverted"
    first_page = foundry.objects.query(log_object_type, limit=1, ctx=ctx)
    assert first_page["nextCursor"] is not None
    second_page = foundry.objects.query(log_object_type, limit=1, cursor=first_page["nextCursor"], ctx=ctx)
    assert {first_page["items"][0]["objectId"], second_page["items"][0]["objectId"]} == {
        run_id,
        reverted["actionRunId"],
    }


def test_action_log_object_query_and_aggregation_execute_in_database_beyond_old_scan_cap(
    foundry: FoundryLite, tmp_path: Path
) -> None:
    ctx = _prepare_v2_demo(foundry, tmp_path)
    row_count = 5_001
    rows = [_bulk_action_log_row(index) for index in range(row_count)]
    with foundry.engine.begin() as transaction:
        transaction.execute(insert(db.action_log_entries), rows)

    first = foundry.objects.query(
        "[LOG] FulfillOrder",
        filter_ast={"op": "eq", "property": "status", "value": "succeeded"},
        order_by=[{"property": "createdAt", "direction": "asc"}],
        limit=2,
        ctx=ctx,
    )
    second = foundry.objects.query(
        "[LOG] FulfillOrder",
        filter_ast={"op": "eq", "property": "status", "value": "succeeded"},
        order_by=[{"property": "createdAt", "direction": "asc"}],
        limit=2,
        cursor=first["nextCursor"],
        ctx=ctx,
    )
    aggregate = foundry.objects.aggregate(
        "[LOG] FulfillOrder",
        select=[{"name": "runCount", "function": "count"}],
        ctx=ctx,
    )

    assert [item["objectId"] for item in first["items"]] == ["bulk-run-0000", "bulk-run-0001"]
    assert [item["objectId"] for item in second["items"]] == ["bulk-run-0002", "bulk-run-0003"]
    assert aggregate == {"groups": [{"key": {}, "metrics": {"runCount": row_count}}], "totalGroups": 1}


def _bulk_action_log_row(index: int) -> dict[str, object]:
    run_id = f"bulk-run-{index:04d}"
    timestamp = f"2026-08-04T00:00:00.{index:06d}Z"
    return {
        "id": f"bulk-log-{index:04d}",
        "tenant_id": "tenant-demo",
        "action_run_id": run_id,
        "log_object_type_api_name": "[LOG] FulfillOrder",
        "log_object_id": run_id,
        "action_type_id": "bulk-fulfill-order",
        "action_type_api_name": "FulfillOrder",
        "definition_version": "bulk-v1",
        "actor_user_id": "bulk-user",
        "status": "succeeded",
        "parameters": {"carrier": "UPS"},
        "result": {"status": "succeeded"},
        "branch_id": None,
        "plan_hash": f"plan-{index:04d}",
        "approval_id": None,
        "revert_allowed": False,
        "revert_status": "not_allowed",
        "reverted_by_run_id": None,
        "created_at": timestamp,
        "completed_at": timestamp,
    }


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
