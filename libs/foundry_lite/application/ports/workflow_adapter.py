from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, NotRequired, Protocol, TypedDict

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract

WorkflowStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


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


class ProductWorkflowRun(TypedDict):
    """Operations-facing workflow run payload for product workflow orchestration."""

    workflowRunId: str
    workflowName: str
    workflowProfile: str
    status: WorkflowStatus
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

    def workflow_run(self, run_id: str) -> WorkflowRun | None:
        """Return a known workflow run, or None when the adapter has no record."""
        ...
