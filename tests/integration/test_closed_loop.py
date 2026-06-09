from __future__ import annotations

from foundry_lite.application.core import FoundryLiteCore


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

    runs = core.list_runs()
    assert any(run["status"] == "SUCCESS" for run in runs["transformRuns"])
    assert any(run["status"] == "succeeded" for run in runs["materializationRuns"])
    assert any(event["event_type"] == "action.run.committed" for event in runs["outboxEvents"])
    assert result["customer"]["explain"]["lineage"]
