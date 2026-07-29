"""Durable orchestration boundary for Pipeline Builder DAG runs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

PipelineDagDispatchStatus = Literal["dispatched", "already_dispatched", "unknown"]


@dataclass(frozen=True, slots=True)
class PipelineDagDispatchRequest:
    """Immutable coordinates handed to a durable DAG orchestrator."""

    tenant_id: str
    run_id: str
    pipeline_id: str
    version_id: str
    request_id: str
    idempotency_key: str
    execution_plan: Mapping[str, object]
    target_node_ids: tuple[str, ...]
    is_commit_forbidden: bool = False


@dataclass(frozen=True, slots=True)
class PipelineDagDispatchResult:
    """Non-blocking dispatch acknowledgement."""

    workflow_run_id: str
    status: PipelineDagDispatchStatus
    task_queue: str


class PipelineDagOrchestrator(Protocol):
    """Dispatch and control a DAG without executing nodes in the API process."""

    @property
    def profile_name(self) -> str: ...

    def dispatch(self, request: PipelineDagDispatchRequest) -> PipelineDagDispatchResult: ...

    def cancel(
        self,
        tenant_id: str,
        workflow_run_id: str,
        *,
        reason: str | None = None,
    ) -> bool: ...


class UnavailablePipelineDagOrchestrator:
    """Compatibility null object for hand-built dependency bundles."""

    profile_name = "unavailable-pipeline-dag"

    def dispatch(self, request: PipelineDagDispatchRequest) -> PipelineDagDispatchResult:
        return PipelineDagDispatchResult(
            workflow_run_id=f"undispatched:{request.run_id}",
            status="unknown",
            task_queue="unavailable",
        )

    def cancel(
        self,
        tenant_id: str,
        workflow_run_id: str,
        *,
        reason: str | None = None,
    ) -> bool:
        del tenant_id, workflow_run_id, reason
        return False
