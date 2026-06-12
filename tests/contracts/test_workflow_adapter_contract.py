from __future__ import annotations

import pytest
from foundry_lite.application.ports.workflow_adapter import WorkflowAdapter, WorkflowStartRequest
from foundry_lite.infrastructure.adapters import FakeWorkflowAdapter, LocalWorkflowAdapter


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
