"""Workflow run lease claim/release predicates and value builders."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports.transaction_context import (
    WORKFLOW_RUN_CANCELLED,
    WORKFLOW_RUN_FAILED,
    WORKFLOW_RUN_SUCCEEDED,
    StatusTransition,
)
from foundry_lite.application.ports.workflow_adapter import WorkflowLedgerStatus, WorkflowRunRecord, WorkflowRunRow


def _workflow_lease_claimable(row: WorkflowRunRow, lease_owner_id: str, now: str) -> bool:
    if row["status"] in {"succeeded", "failed", "cancelled", "start_unknown"}:
        return True
    lease = _workflow_lease(row)
    if row["status"] in {"requested", "starting"} and lease is None:
        return True
    if lease is None:
        return False
    if lease.get("ownerId") == lease_owner_id:
        return True
    expires_at = lease.get("leaseExpiresAt")
    return isinstance(expires_at, str) and expires_at <= now


def _workflow_lease_claim_values(record: WorkflowRunRecord, attempts: int) -> dict[str, object]:
    return {
        "output": dict(record.output),
        "error": dict(record.error) if record.error is not None else None,
        "attempts": attempts,
        "started_at": record.started_at,
        "completed_at": None,
    }


def _workflow_lease_release_transition(status: WorkflowLedgerStatus) -> StatusTransition:
    if status == "cancelled":
        return WORKFLOW_RUN_CANCELLED
    if status == "failed":
        return WORKFLOW_RUN_FAILED
    return WORKFLOW_RUN_SUCCEEDED


def _workflow_lease_matches(row: WorkflowRunRow, lease_owner_id: str, lease_token: str) -> bool:
    lease = _workflow_lease(row)
    return lease is not None and lease.get("ownerId") == lease_owner_id and lease.get("leaseToken") == lease_token


def _workflow_lease_attempts(row: WorkflowRunRow, lease_owner_id: str) -> int:
    attempts = row["attempts"]
    current = attempts if isinstance(attempts, int) and not isinstance(attempts, bool) else 0
    lease = _workflow_lease(row)
    if row["status"] == "running" and lease is not None and lease.get("ownerId") == lease_owner_id:
        return current
    return current + 1


def _workflow_lease(row: WorkflowRunRow) -> Mapping[str, object] | None:
    output = row.get("output")
    if not isinstance(output, Mapping):
        return None
    lease = output.get("workerLease")
    return lease if isinstance(lease, Mapping) else None
