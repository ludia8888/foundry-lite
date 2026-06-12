from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest
from foundry_lite.application.core import FoundryLiteCore
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ConflictDetected,
    ExternalSystemError,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)

from tests.conftest import DEMO_ROOT, prepare_indexed_demo


@pytest.mark.integration_scenario("ontology_index")
def test_ontology_import_indexes_order_customer_and_supports_object_query(
    core: FoundryLiteCore,
) -> None:
    ctx = prepare_indexed_demo(core)

    order = core.get_object("Order", "O-1001", ctx=ctx, include_explain=True)
    customer = core.get_object("Customer", "C-100", ctx=ctx)
    linked_customer = core.get_links("Order", "O-1001", "OrderCustomer")[0]["to"]

    assert order["properties"]["orderId"] == "O-1001"
    assert order["sourceDatasetVersionId"]
    explain = order.get("explain")
    assert explain is not None
    assert explain["lineage"]
    assert customer["properties"]["customerId"] == "C-100"
    assert linked_customer["objectType"] == "Customer"
    assert linked_customer["objectId"] == "C-100"
    assert linked_customer["properties"]["customerId"] == "C-100"


def test_ontology_activation_rejects_missing_backing_column(
    core: FoundryLiteCore,
    tmp_path: Path,
) -> None:
    ctx = prepare_indexed_demo(core)
    bad_yaml = tmp_path / "bad-ontology.yaml"
    bad_yaml.write_text(
        """
objectTypes:
  - apiName: Order
    displayName: Order
    primaryKey: orderId
    backing:
      dataset: clean.orders
      mode: snapshot
      primaryKeyColumns: [order_id]
    properties:
      - apiName: orderId
        column: order_id
        type: string
        nullable: false
      - apiName: badProperty
        column: does_not_exist
        type: string
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValidationFailed):
        core.apply_ontology(bad_yaml, ctx=ctx)


@pytest.mark.integration_scenario("object_action_audit")
def test_action_apply_is_idempotent_and_rejects_stale_object_version(
    core: FoundryLiteCore,
) -> None:
    ctx = prepare_indexed_demo(core)
    order = core.get_object("Order", "O-1001", ctx=ctx)
    first = core.apply_action(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Inventory confirmed"},
        idempotency_key="same-key",
        ctx=ctx,
    )
    replay = core.apply_action(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Inventory confirmed"},
        idempotency_key="same-key",
        ctx=ctx,
    )

    approved = core.get_object("Order", "O-1001", ctx=ctx)
    runs = core.list_runs(ctx=ctx)
    assert approved["properties"]["status"] == "APPROVED"
    assert approved["properties"]["operatorNote"] == "Inventory confirmed"
    assert any(
        event["event_type"] == "object.edit.committed" and event["aggregate_id"] == "O-1001"
        for event in runs["outboxEvents"]
    )
    assert any(
        event["event_type"] == "action.run.committed" and event["resource_id"] == first["actionRunId"]
        for event in runs["auditEvents"]
    )

    assert replay["idempotentReplay"] is True
    assert replay["actionRunId"] == first["actionRunId"]
    with pytest.raises(ConflictDetected):
        core.apply_action(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "Inventory confirmed again"},
            idempotency_key="different-key",
            ctx=ctx,
        )


@pytest.mark.integration_scenario("failed_run_replay_or_dlq")
def test_before_commit_writeback_failure_does_not_edit_object(
    core: FoundryLiteCore,
) -> None:
    ctx = prepare_indexed_demo(core)
    order = core.get_object("Order", "O-1001", ctx=ctx)

    with pytest.raises(ExternalSystemError):
        core.apply_action(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "Inventory confirmed"},
            idempotency_key="writeback-fails",
            simulate_writeback_failure=True,
            ctx=ctx,
        )

    after = core.get_object("Order", "O-1001", ctx=ctx)
    failed_runs = [run for run in core.list_runs(ctx=ctx)["actionRuns"] if run["idempotency_key"] == "writeback-fails"]
    replay = core.apply_action(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Inventory confirmed"},
        idempotency_key="writeback-fails",
        ctx=ctx,
    )
    after_replay_runs = [
        run for run in core.list_runs(ctx=ctx)["actionRuns"] if run["idempotency_key"] == "writeback-fails"
    ]
    assert after["objectVersion"] == order["objectVersion"]
    assert after["properties"]["status"] == "PENDING"
    assert len(failed_runs) == 1
    assert replay["idempotentReplay"] is True
    assert replay["status"] == "failed"
    assert replay["actionRunId"] == failed_runs[0]["id"]
    assert [run["id"] for run in after_replay_runs] == [failed_runs[0]["id"]]
    writeback = core.list_runs(ctx=ctx)["actionWritebacks"][0]
    writeback_request = cast(Mapping[str, object], writeback["request"])
    writeback_response = cast(Mapping[str, object], writeback["response"])
    assert writeback["connector_id"] == "mock_erp_simulator"
    assert writeback_request["networkCall"] is False
    assert writeback_response["simulated"] is True
    audit_events = core.list_runs(ctx=ctx)["auditEvents"]
    assert any(event["event_type"] == "action.run.failed" for event in audit_events)


@pytest.mark.integration_scenario("permission_tenant_isolation")
def test_viewer_sees_masked_margin_and_cannot_approve_order(
    core: FoundryLiteCore,
) -> None:
    prepare_indexed_demo(core)
    viewer = RequestContext(actor_user_id="viewer-1", roles=("viewer",))
    finance = RequestContext(actor_user_id="finance-1", roles=("finance",))
    other_tenant = RequestContext(
        tenant_id="tenant-other",
        actor_user_id="other-admin",
        roles=("admin", "data_engineer", "ops_manager", "finance"),
    )
    order = core.get_object("Order", "O-1001", ctx=viewer)
    dataset_preview = core.preview_dataset("clean.orders", ctx=viewer, limit=1)
    finance_order = core.get_object("Order", "O-1001", ctx=finance)

    assert order["properties"]["margin"] == "***MASKED***"
    assert dataset_preview[0]["order_id"] == "O-1001"
    assert finance_order["properties"]["margin"] != "***MASKED***"
    with pytest.raises(PermissionDenied):
        core.apply_ontology(str(DEMO_ROOT / "ontology" / "order-customer.yaml"), ctx=viewer)
    with pytest.raises(NotFound):
        core.get_object("Order", "O-1001", ctx=other_tenant)
    assert all(not rows for rows in core.list_runs(ctx=other_tenant).values())
    with pytest.raises(PermissionDenied):
        core.apply_action(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "Inventory confirmed"},
            idempotency_key="viewer-denied",
            ctx=viewer,
        )
    assert any(event["decision"] == "deny" for event in core.list_runs()["auditEvents"])


def test_only_ops_manager_or_admin_can_execute_approve_order(
    core: FoundryLiteCore,
) -> None:
    prepare_indexed_demo(core)
    data_engineer = RequestContext(actor_user_id="engineer-1", roles=("data_engineer",))
    ops_manager = RequestContext(actor_user_id="ops-1", roles=("ops_manager",))
    order = core.get_object("Order", "O-1001", ctx=ops_manager)

    with pytest.raises(PermissionDenied):
        core.apply_action(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "Inventory confirmed"},
            idempotency_key="engineer-denied",
            ctx=data_engineer,
        )

    approved = core.apply_action(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Inventory confirmed"},
        idempotency_key="ops-approved",
        ctx=ops_manager,
    )

    assert approved["status"] == "succeeded"
    assert any(event["decision"] == "deny" for event in core.list_runs()["auditEvents"])
