from __future__ import annotations

import pytest
from foundry_lite.application.core import FoundryLiteCore

from tests.conftest import prepare_indexed_demo


@pytest.mark.integration_scenario("transform_clean_dataset")
def test_supply_chain_closed_loop_updates_customer_risk_and_records_replay_state(
    core: FoundryLiteCore,
) -> None:
    result = core.run_supply_chain_demo()

    assert result["action"]["status"] == "succeeded"
    assert result["customer"]["properties"]["customerId"] == "C-100"
    assert result["customer"]["properties"]["riskScore"] == 0.1
    assert result["customer"]["properties"]["approvedOrderCount"] == 2

    linked = core.get_links("Order", "O-1001", "OrderCustomer")
    assert linked[0]["to"]["objectId"] == "C-100"

    clean_orders = core.inspect_dataset("clean.orders")
    clean_order_lineage = core.lineage_for_resource(result["cleanOrdersVersion"])
    assert clean_orders["manifest"]["files"][0]["row_count"] == 3
    assert any(
        edge["from_resource_id"] == result["rawOrdersVersion"]
        and edge["to_resource_id"] == result["cleanOrdersVersion"]
        for edge in clean_order_lineage
    )

    runs = core.list_runs()
    assert any(run["status"] == "SUCCESS" for run in runs["transformRuns"])
    assert any(run["status"] == "succeeded" for run in runs["materializationRuns"])
    assert any(event["event_type"] == "action.run.committed" for event in runs["outboxEvents"])
    assert result["customer"]["explain"]["lineage"]


@pytest.mark.integration_scenario("materialization_downstream_transform")
def test_action_materialization_writes_dataset_versions_and_manifest_rows(
    core: FoundryLiteCore,
) -> None:
    ctx = prepare_indexed_demo(core)
    order = core.get_object("Order", "O-1001", ctx=ctx)
    action = core.apply_action(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Inventory confirmed"},
        idempotency_key="materialization-proof",
        ctx=ctx,
    )

    action_log = core.materialize("action_log", ctx=ctx)
    order_current = core.materialize("order_current", ctx=ctx)
    customer_risk = core.run_transform("customer_risk", ctx=ctx)

    action_log_versions = core.list_dataset_versions("ops.action_log", ctx=ctx)
    order_current_versions = core.list_dataset_versions("ops.order_current", ctx=ctx)
    action_log_manifest = core.inspect_dataset("ops.action_log", ctx=ctx)["manifest"]
    order_current_manifest = core.inspect_dataset("ops.order_current", ctx=ctx)["manifest"]
    customer_risk_rows = core.preview_dataset("clean.customers", ctx=ctx)
    downstream_lineage = core.lineage_for_resource(order_current.version_id, ctx=ctx)

    assert action_log.version_id == action_log_versions[0]["id"]
    assert order_current.version_id == order_current_versions[0]["id"]
    assert action["status"] == "succeeded"
    assert action_log.row_count == action_log_manifest["files"][0]["row_count"] == 1
    assert order_current.row_count == order_current_manifest["files"][0]["row_count"] == 3
    assert customer_risk.row_count == 2
    assert any(row["customer_id"] == "C-100" and row["risk_score"] == 0.1 for row in customer_risk_rows)
    assert any(
        edge["from_resource_id"] == order_current.version_id and edge["to_resource_id"] == customer_risk.version_id
        for edge in downstream_lineage
    )
