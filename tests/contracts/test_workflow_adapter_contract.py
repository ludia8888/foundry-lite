from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.application.ports.workflow_adapter import (
    WorkflowAdapter,
    WorkflowRun,
    WorkflowStartRequest,
    workflow_run_id,
)
from foundry_lite.application.services.workflow_orchestration_service import CONNECTOR_SYNC_WORKFLOW_NAME
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.domain.errors import ConflictDetected
from foundry_lite.infrastructure.adapters import FakeWorkflowAdapter, LocalWorkflowAdapter
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies


@pytest.fixture(params=[LocalWorkflowAdapter, FakeWorkflowAdapter])
def adapter(request: pytest.FixtureRequest) -> WorkflowAdapter:
    adapter_type = request.param
    return adapter_type()


class RetryableStartFailureWorkflowAdapter:
    profile_name = "retryable-start-failure"

    def __init__(self) -> None:
        self.start_calls = 0

    def failure_contract(self) -> AdapterFailureContract:
        return LocalWorkflowAdapter().failure_contract()

    def start_workflow(self, request: WorkflowStartRequest) -> WorkflowRun:
        self.start_calls += 1
        return WorkflowRun(
            run_id=workflow_run_id(request),
            workflow_name=request.workflow_name,
            status="failed",
            output={},
            error={"kind": "unavailable", "retryable": True},
        )

    def workflow_run(self, run_id: str) -> WorkflowRun | None:
        return None


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


def test_workflow_adapter_contract_namespaces_idempotency_by_tenant_and_workflow(adapter: WorkflowAdapter) -> None:
    base = WorkflowStartRequest(
        workflow_name="sync_orders",
        tenant_id="tenant-a",
        request_id="req-workflow-a",
        idempotency_key="daily-sync",
        input={"dataset": "raw.orders"},
    )
    other_tenant = WorkflowStartRequest(
        workflow_name="sync_orders",
        tenant_id="tenant-b",
        request_id="req-workflow-b",
        idempotency_key="daily-sync",
        input={"dataset": "raw.orders"},
    )
    other_workflow = WorkflowStartRequest(
        workflow_name="sync_customers",
        tenant_id="tenant-a",
        request_id="req-workflow-c",
        idempotency_key="daily-sync",
        input={"dataset": "raw.customers"},
    )

    runs = [adapter.start_workflow(request) for request in (base, other_tenant, other_workflow)]

    assert len({run.run_id for run in runs}) == 3
    assert all(run.run_id.startswith("flite:") for run in runs)
    assert all("daily-sync" not in run.run_id for run in runs)


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

    assert str(run["workflowRunId"]).startswith("flite:")
    assert run["workflowRunId"] != "connector-sync-orders"
    assert duplicate["workflowRunId"] == run["workflowRunId"]
    assert run["idempotencyKey"] == "connector-sync-orders"
    assert duplicate["idempotencyKey"] == "connector-sync-orders"
    assert run["workflowName"] == CONNECTOR_SYNC_WORKFLOW_NAME
    assert run["workflowProfile"] == adapter.profile_name
    assert run["status"] == "succeeded"
    assert run["output"]["datasetRef"] == "raw.workflow_orders"
    assert run["operationPath"] == f"/api/operations/runs/workflow/{run['workflowRunId']}"
    assert run["foundryRunId"] is not None
    assert duplicate["foundryRunId"] == run["foundryRunId"]
    workflow_runs = foundry.operations.query_runs(ctx=ctx, run_type="workflow")["workflowRuns"]
    detail = foundry.operations.run_detail("audit", str(run["foundryRunId"]), ctx=ctx)
    audits = foundry.operations.query_runs(ctx=ctx, run_type="audit")["auditEvents"]
    workflow_audits = [event for event in audits if event["resource_id"] == run["workflowRunId"]]
    assert [row["id"] for row in workflow_runs] == [run["workflowRunId"]]
    assert workflow_runs[0]["status"] == "succeeded"
    assert detail["row"]["resource_id"] == run["workflowRunId"]
    assert detail["row"]["after_ref"]["workflowName"] == CONNECTOR_SYNC_WORKFLOW_NAME
    assert len(workflow_audits) == 1


def test_product_workflow_operations_contract_same_key_different_request_conflicts(
    adapter: WorkflowAdapter,
    tmp_path: Path,
) -> None:
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "workflow-conflict-contract")
    foundry = FoundryLite(dependencies=replace(dependencies, workflow_adapter=adapter))
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.workflow_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("raw.workflow_customers", ctx=ctx, primary_key=["customer_id"])

    first = foundry.operations.start_connector_sync_workflow(
        "raw.workflow_orders",
        connector_name="rest",
        resource_name="orders",
        idempotency_key="shared-key",
        ctx=ctx,
    )
    with pytest.raises(ConflictDetected):
        foundry.operations.start_connector_sync_workflow(
            "raw.workflow_customers",
            connector_name="rest",
            resource_name="customers",
            idempotency_key="shared-key",
            ctx=ctx,
        )

    workflow_runs = foundry.operations.query_runs(ctx=ctx, run_type="workflow")["workflowRuns"]
    assert [row["id"] for row in workflow_runs] == [first["workflowRunId"]]


def test_product_workflow_operations_contract_retryable_start_failure_stays_retryable(
    tmp_path: Path,
) -> None:
    adapter = RetryableStartFailureWorkflowAdapter()
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "workflow-start-unknown-contract")
    foundry = FoundryLite(dependencies=replace(dependencies, workflow_adapter=adapter))
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.workflow_orders", ctx=ctx, primary_key=["order_id"])

    first = foundry.operations.start_connector_sync_workflow(
        "raw.workflow_orders",
        connector_name="rest",
        resource_name="orders",
        idempotency_key="transient-key",
        ctx=ctx,
    )
    second = foundry.operations.start_connector_sync_workflow(
        "raw.workflow_orders",
        connector_name="rest",
        resource_name="orders",
        idempotency_key="transient-key",
        ctx=ctx,
    )

    workflow_runs = foundry.operations.query_runs(ctx=ctx, run_type="workflow")["workflowRuns"]
    assert first["workflowRunId"] == second["workflowRunId"]
    assert first["status"] == "start_unknown"
    assert second["status"] == "start_unknown"
    assert second["foundryRunId"] == first["foundryRunId"]
    assert adapter.start_calls == 2
    assert workflow_runs[0]["status"] == "start_unknown"
    assert workflow_runs[0]["attempts"] == 2
    assert workflow_runs[0]["error"] == {"kind": "unavailable", "retryable": True}
