from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, NotRequired, Protocol, TypedDict

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract

WorkflowStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
WorkflowLedgerStatus = Literal[
    "requested",
    "starting",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "start_unknown",
]
ProductWorkflowStatus = WorkflowStatus | WorkflowLedgerStatus


@dataclass(frozen=True)
class WorkflowStartRequest:
    """Vendor-neutral request for starting a durable workflow run."""

    workflow_name: str
    tenant_id: str
    request_id: str
    idempotency_key: str
    input: Mapping[str, object]


@dataclass(frozen=True)
class WorkflowRun:
    """Durable workflow run reference returned by a WorkflowAdapter."""

    run_id: str
    workflow_name: str
    status: WorkflowStatus
    output: Mapping[str, object]
    error: Mapping[str, object] | None = None


@dataclass(frozen=True)
class WorkflowRunRecord:
    """Foundry-owned workflow run ledger row written before adapter start."""

    workflow_run_id: str
    tenant_id: str
    workflow_name: str
    workflow_profile: str
    status: WorkflowLedgerStatus
    idempotency_key: str
    request_fingerprint: str
    input: Mapping[str, object]
    output: Mapping[str, object]
    error: Mapping[str, object] | None
    dataset_id: str | None
    audit_event_id: str | None
    attempts: int
    created_at: str
    started_at: str | None
    completed_at: str | None


class WorkflowRunRow(TypedDict):
    """Persisted Foundry workflow ledger row."""

    id: str
    tenant_id: str
    workflow_name: str
    workflow_profile: str
    status: WorkflowLedgerStatus
    idempotency_key: str
    request_fingerprint: str
    input: Mapping[str, object]
    output: Mapping[str, object]
    error: Mapping[str, object] | None
    dataset_id: str | None
    audit_event_id: str | None
    attempts: int
    created_at: str
    started_at: str | None
    completed_at: str | None


class ProductWorkflowRun(TypedDict):
    """Operations-facing workflow run payload for product workflow orchestration."""

    workflowRunId: str
    workflowName: str
    workflowProfile: str
    status: ProductWorkflowStatus
    idempotencyKey: str
    foundryRunId: str | None
    operationPath: str | None
    output: Mapping[str, object]
    error: Mapping[str, object] | None
    auditEventId: NotRequired[str | None]


class WorkflowAdapter(Protocol):
    """Scale Foundation boundary for future Temporal-style orchestration."""

    @property
    def profile_name(self) -> str: ...

    def failure_contract(self) -> AdapterFailureContract:
        """Return the adapter failure taxonomy promised by this profile."""
        ...

    def start_workflow(self, request: WorkflowStartRequest) -> WorkflowRun:
        """Start or idempotently return a workflow run."""
        ...

    def workflow_run(self, tenant_id: str, run_id: str) -> WorkflowRun | None:
        """Return a tenant-scoped workflow run, or None when the adapter has no record."""
        ...


def workflow_run_id(request: WorkflowStartRequest) -> str:
    tenant = _stable_id_part(request.tenant_id)
    workflow = _stable_id_part(request.workflow_name)
    idempotency = _stable_id_part(request.idempotency_key)
    return f"flite:{tenant}:{workflow}:{idempotency}"


def workflow_run_id_matches_tenant(tenant_id: str, run_id: str) -> bool:
    parts = run_id.split(":")
    if len(parts) != 4 or parts[0] != "flite":
        return False
    return parts[1] == _stable_id_part(tenant_id)


def workflow_request_fingerprint(request: WorkflowStartRequest) -> str:
    payload = {
        "workflowName": request.workflow_name,
        "tenantId": request.tenant_id,
        "idempotencyKey": request.idempotency_key,
        "input": dict(request.input),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _stable_id_part(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]
