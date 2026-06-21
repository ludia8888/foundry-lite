from __future__ import annotations

from foundry_lite.application.ports.workflow_adapter import WorkflowStartRequest
from foundry_lite.infrastructure.adapters.temporal_workflow import _temporal_workflow_id


def test_temporal_workflow_id_namespaces_tenant_and_workflow() -> None:
    base = WorkflowStartRequest(
        workflow_name="sync_orders",
        tenant_id="tenant-a",
        request_id="req-1",
        idempotency_key="daily-sync",
        input={"dataset": "raw.orders"},
    )
    other_tenant = WorkflowStartRequest(
        workflow_name="sync_orders",
        tenant_id="tenant-b",
        request_id="req-2",
        idempotency_key="daily-sync",
        input={"dataset": "raw.orders"},
    )
    other_workflow = WorkflowStartRequest(
        workflow_name="sync_customers",
        tenant_id="tenant-a",
        request_id="req-3",
        idempotency_key="daily-sync",
        input={"dataset": "raw.customers"},
    )

    workflow_ids = {
        _temporal_workflow_id(base),
        _temporal_workflow_id(other_tenant),
        _temporal_workflow_id(other_workflow),
    }

    assert len(workflow_ids) == 3
    assert all(workflow_id.startswith("flite:") for workflow_id in workflow_ids)
    assert all("daily-sync" not in workflow_id for workflow_id in workflow_ids)
