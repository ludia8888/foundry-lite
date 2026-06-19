from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports.workflow_adapter import WorkflowAdapter, WorkflowStartRequest
from foundry_lite.application.services.workflow_orchestration_service import CONNECTOR_SYNC_WORKFLOW_NAME
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.infrastructure.adapters import FakeWorkflowAdapter, LocalWorkflowAdapter
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies


@pytest.fixture(params=[LocalWorkflowAdapter, FakeWorkflowAdapter])
def adapter(request: pytest.FixtureRequest) -> WorkflowAdapter:
    adapter_type = request.param
    return adapter_type()


def test_workflow_adapter_contract_starts_and_reads_run(adapter: WorkflowAdapter) -> None:
    request = WorkflowStartRequest(
        workflow_name="sync_orders",
        tenant_id="tenant-demo",
        request_id="req-workflow-1",
        idempotency_key="sync-orders-1",
        input={"dataset": "raw.orders"},
    )

    run = adapter.start_workflow(request)
    fetched = adapter.workflow_run(run.run_id)

    assert run.status == "succeeded"
    assert run.workflow_name == "sync_orders"
    assert run.output["request_id"] == "req-workflow-1"
    assert fetched == run


def test_workflow_adapter_contract_is_idempotent(adapter: WorkflowAdapter) -> None:
    request = WorkflowStartRequest(
        workflow_name="sync_orders",
        tenant_id="tenant-demo",
        request_id="req-workflow-2",
        idempotency_key="sync-orders-repeat",
        input={"dataset": "raw.orders"},
    )

    first = adapter.start_workflow(request)
    second = adapter.start_workflow(request)

    assert second == first
    assert adapter.workflow_run("missing") is None


def test_product_workflow_operations_contract_starts_connector_sync_and_audits(
    adapter: WorkflowAdapter,
    tmp_path: Path,
) -> None:
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "workflow-contract")
    foundry = FoundryLite(dependencies=replace(dependencies, workflow_adapter=adapter))
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.workflow_orders", ctx=ctx, primary_key=["order_id"])

    run = foundry.operations.start_connector_sync_workflow(
        "raw.workflow_orders",
        connector_name="rest",
        resource_name="orders",
        idempotency_key="connector-sync-orders",
        ctx=ctx,
    )
    duplicate = foundry.operations.start_connector_sync_workflow(
        "raw.workflow_orders",
        connector_name="rest",
        resource_name="orders",
        idempotency_key="connector-sync-orders",
        ctx=ctx,
    )

    assert run["workflowRunId"] == "connector-sync-orders"
    assert duplicate["workflowRunId"] == run["workflowRunId"]
    assert run["workflowName"] == CONNECTOR_SYNC_WORKFLOW_NAME
    assert run["workflowProfile"] == adapter.profile_name
    assert run["status"] == "succeeded"
    assert run["output"]["datasetRef"] == "raw.workflow_orders"
    assert run["foundryRunId"] is not None
    detail = foundry.operations.run_detail("audit", str(run["foundryRunId"]), ctx=ctx)
    assert detail["row"]["resource_id"] == run["workflowRunId"]
    assert detail["row"]["after_ref"]["workflowName"] == CONNECTOR_SYNC_WORKFLOW_NAME
