"""Durable orchestration boundary for asynchronous Action runs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

ActionRunDispatchStatus = Literal["dispatched", "already_dispatched", "unknown"]


class ActionRunRetryableFailure(RuntimeError):
    """Activity failed safely and may be redelivered by the orchestrator."""


@dataclass(frozen=True, slots=True)
class ActionRunDispatchRequest:
    tenant_id: str
    run_id: str
    action_api_name: str
    request_id: str
    idempotency_key: str
    execution_plan: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ActionRunDispatchResult:
    workflow_run_id: str
    status: ActionRunDispatchStatus
    task_queue: str


class ActionRunOrchestrator(Protocol):
    @property
    def profile_name(self) -> str: ...

    def dispatch(self, request: ActionRunDispatchRequest) -> ActionRunDispatchResult: ...

    def cancel(self, tenant_id: str, workflow_run_id: str, *, reason: str | None = None) -> bool: ...

    def close(self) -> None:
        """Drain and stop any process-local orchestration resources."""
        ...


class UnavailableActionRunOrchestrator:
    profile_name = "unavailable-action-run"

    def dispatch(self, request: ActionRunDispatchRequest) -> ActionRunDispatchResult:
        return ActionRunDispatchResult(f"undispatched:{request.run_id}", "unknown", "unavailable")

    def cancel(self, tenant_id: str, workflow_run_id: str, *, reason: str | None = None) -> bool:
        del tenant_id, workflow_run_id, reason
        return False

    def close(self) -> None:
        return None
