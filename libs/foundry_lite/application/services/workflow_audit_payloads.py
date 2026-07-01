"""Application service helpers for workflow audit payloads workflows."""

from __future__ import annotations

from foundry_lite.application.ports import AuditEventRecord, RuntimeJsonObject, WorkflowRunRow
from foundry_lite.application.primitives import _now
from foundry_lite.domain.context import RequestContext


def workflow_start_audit_record(ctx: RequestContext, row: WorkflowRunRow, *, audit_id: str) -> AuditEventRecord:
    return AuditEventRecord(
        event_id=audit_id,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.actor_user_id,
        event_type=f"workflow.{row['status']}",
        resource_type="product_workflow",
        resource_id=row["id"],
        action="workflow:start",
        decision="allow",
        policy_decision={"permission": "dataset:write"},
        before_ref={},
        after_ref=workflow_after_ref(row, audit_id),
        correlation_id=row["id"],
        request_id=ctx.request_id,
        metadata={},
        created_at=_now(),
    )


def workflow_reconciliation_audit_record(
    ctx: RequestContext,
    row: WorkflowRunRow,
    audit_id: str,
    previous_status: str,
) -> AuditEventRecord:
    after_ref: dict[str, object] = dict(workflow_after_ref(row, audit_id))
    after_ref["reconciliation"] = {
        "source": "workflow_adapter_lookup",
        "previousStatus": previous_status,
    }
    return AuditEventRecord(
        event_id=audit_id,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.actor_user_id,
        event_type="workflow.reconciled",
        resource_type="product_workflow",
        resource_id=row["id"],
        action="workflow:reconcile",
        decision="allow",
        policy_decision={"permission": "dataset:write"},
        before_ref={"status": previous_status},
        after_ref=after_ref,
        correlation_id=row["id"],
        request_id=ctx.request_id,
        metadata={},
        created_at=_now(),
    )


def workflow_cancelled_audit_record(
    ctx: RequestContext,
    row: WorkflowRunRow,
    audit_id: str,
    previous_status: str,
    reason: str | None,
) -> AuditEventRecord:
    after_ref: dict[str, object] = dict(workflow_after_ref(row, audit_id))
    after_ref["cancellation"] = {
        "previousStatus": previous_status,
        "reason": reason or "operator_cancelled",
        "cleanup": _cleanup_payload(row),
    }
    return AuditEventRecord(
        event_id=audit_id,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.actor_user_id,
        event_type="workflow.cancelled",
        resource_type="product_workflow",
        resource_id=row["id"],
        action="workflow:cancel",
        decision="allow",
        policy_decision={"permission": "operations:retry"},
        before_ref={"status": previous_status},
        after_ref=after_ref,
        correlation_id=row["id"],
        request_id=ctx.request_id,
        metadata={},
        created_at=_now(),
    )


def workflow_after_ref(row: WorkflowRunRow, audit_id: str) -> RuntimeJsonObject:
    return {
        "workflowRunId": row["id"],
        "workflowName": row["workflow_name"],
        "workflowProfile": row["workflow_profile"],
        "foundryRunId": audit_id,
        "datasetId": row["dataset_id"],
        "idempotencyKey": row["idempotency_key"],
        "status": row["status"],
        "output": dict(row["output"]),
        "error": dict(row["error"]) if row["error"] is not None else None,
    }


def _cleanup_payload(row: WorkflowRunRow) -> object:
    output = row["output"]
    cancellation = output.get("cancellation") if hasattr(output, "get") else None
    if isinstance(cancellation, dict):
        return cancellation.get("cleanup", {})
    return {}
