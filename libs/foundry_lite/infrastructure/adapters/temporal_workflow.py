"""Temporal-backed workflow adapter (durable orchestration boundary).

Implements the vendor-neutral ``WorkflowAdapter`` port on a Temporal cluster.
The adapter is workflow-agnostic: it starts workflows by their registered string
type name (``WorkflowStartRequest.workflow_name``), so it never imports the
workflow definitions — a worker process registers them on a task queue.

Source-of-truth and idempotency contract:
- ``start_workflow`` is an idempotent *start-and-wait*. The workflow id is the
  caller's ``idempotency_key`` with ``WorkflowIDReusePolicy.REJECT_DUPLICATE``;
  a second call with the same key does not start a duplicate run, it returns the
  outcome of the existing run (running → waits for it, completed → its result).
- A workflow failure/timeout is returned as a ``WorkflowRun`` with a populated,
  durable ``error`` payload (the operator-evidence contract), never as a silent
  success. A cancelled workflow returns status ``cancelled``.
- ``run_id`` exposed by this adapter is the Temporal *workflow id* (the
  idempotency key), so ``workflow_run`` can look a run up again; the underlying
  Temporal run uuid is carried in the payload details.

Temporal's client API is async; the port is sync. The adapter exposes an async
core (``start_workflow_async`` / ``workflow_run_async``) used directly by the
time-skipping ratchet tests, and thin blocking wrappers for the sync port. The
``temporalio`` import is lazy so importing this module never requires Temporal.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from importlib import import_module
from typing import Any

from foundry_lite.application.ports.adapter_failure import (
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureKind,
    AdapterFailureMode,
)
from foundry_lite.application.ports.workflow_adapter import (
    WorkflowRun,
    WorkflowStartRequest,
    WorkflowStatus,
)


@dataclass(frozen=True)
class TemporalWorkflowAdapterConfig:
    """Connection + execution settings for the Temporal workflow adapter."""

    address: str = "localhost:7233"
    namespace: str = "default"
    task_queue: str = "foundry-lite"
    execution_timeout_seconds: int = 300


class TemporalWorkflowAdapter:
    """Durable workflow boundary backed by a Temporal cluster."""

    profile_name = "temporal"

    def __init__(self, config: TemporalWorkflowAdapterConfig | None = None, *, client: Any | None = None) -> None:
        # ``client`` is injected by the time-skipping tests (it is bound to the
        # test event loop); production connects a fresh client per blocking call.
        self._config = config or TemporalWorkflowAdapterConfig()
        self._client = client

    def failure_contract(self) -> AdapterFailureContract:
        """Return the Temporal workflow failure taxonomy."""
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "start_workflow",
                    "timeout",
                    True,
                    "Workflow timed out; retry with the same idempotency key (start-and-wait is idempotent).",
                    timeout_seconds=self._config.execution_timeout_seconds,
                    has_required_idempotency_key=True,
                ),
                AdapterFailureMode(
                    "start_workflow",
                    "unavailable",
                    True,
                    "Temporal service was unavailable; retry with the same idempotency key.",
                    has_required_idempotency_key=True,
                ),
                AdapterFailureMode(
                    "start_workflow",
                    "unknown",
                    False,
                    "Workflow failed in business logic; inspect the run error payload, do not blind-retry.",
                    has_required_idempotency_key=True,
                ),
                AdapterFailureMode("workflow_run", "not_found", False, "Workflow run was not found."),
            ),
        )

    # -- sync port --------------------------------------------------------

    def start_workflow(self, request: WorkflowStartRequest) -> WorkflowRun:
        """Start (or idempotently return) a workflow run, blocking until it settles."""
        return _run_blocking(lambda: self.start_workflow_async(request))

    def workflow_run(self, run_id: str) -> WorkflowRun | None:
        """Return a known workflow run by id, or None when Temporal has no record."""
        return _run_blocking(lambda: self.workflow_run_async(run_id))

    # -- async core (exercised directly by the time-skipping tests) -------

    async def start_workflow_async(self, request: WorkflowStartRequest) -> WorkflowRun:
        """Idempotent start-and-wait against Temporal."""
        client = await self._client_handle()
        common = import_module("temporalio.common")
        exceptions = import_module("temporalio.exceptions")
        try:
            handle = await client.start_workflow(
                request.workflow_name,
                dict(request.input),
                id=request.idempotency_key,
                task_queue=self._config.task_queue,
                id_reuse_policy=common.WorkflowIDReusePolicy.REJECT_DUPLICATE,
                execution_timeout=timedelta(seconds=self._config.execution_timeout_seconds),
            )
        except exceptions.WorkflowAlreadyStartedError:
            # Idempotent: a run with this key already exists. Attach to it and
            # return its outcome instead of starting a duplicate run.
            handle = client.get_workflow_handle(request.idempotency_key)
        return await self._resolve_run(handle, request)

    async def workflow_run_async(self, run_id: str) -> WorkflowRun | None:
        """Describe a known run by workflow id; None when Temporal has no record."""
        client = await self._client_handle()
        service = import_module("temporalio.service")
        handle = client.get_workflow_handle(run_id)
        try:
            description = await handle.describe()
        except service.RPCError as exc:
            if getattr(exc, "status", None) == service.RPCStatusCode.NOT_FOUND:
                return None
            raise
        status = _map_describe_status(description.status)
        output: Mapping[str, object] = {}
        if status == "succeeded":
            output = _as_mapping(await handle.result())
        return WorkflowRun(
            run_id=run_id,
            workflow_name=description.workflow_type,
            status=status,
            output=output,
        )

    # -- internals --------------------------------------------------------

    async def _client_handle(self) -> Any:
        if self._client is not None:
            return self._client
        client_mod = import_module("temporalio.client")
        return await client_mod.Client.connect(self._config.address, namespace=self._config.namespace)

    async def _resolve_run(self, handle: Any, request: WorkflowStartRequest) -> WorkflowRun:
        client_mod = import_module("temporalio.client")
        try:
            output = await handle.result()
        except client_mod.WorkflowFailureError as exc:
            # Failure, timeout, and cancellation all surface as WorkflowFailureError;
            # _classify_failure walks the cause chain to pick the run status.
            return await self._settled_failure(handle, request, exc)
        return WorkflowRun(
            run_id=request.idempotency_key,
            workflow_name=request.workflow_name,
            status="succeeded",
            output=_as_mapping(output),
            error=None,
        )

    async def _settled_failure(self, handle: Any, request: WorkflowStartRequest, exc: BaseException) -> WorkflowRun:
        kind, status = _classify_failure(exc)
        description = await handle.describe()
        failure = AdapterFailure(
            adapter_profile=self.profile_name,
            operation="start_workflow",
            kind=kind,
            is_retryable=kind in {"timeout", "unavailable"},
            operator_message=f"Workflow '{request.workflow_name}' {status}: {_root_message(exc)}",
            timeout_seconds=self._config.execution_timeout_seconds if kind == "timeout" else None,
            idempotency_key=request.idempotency_key,
            details={
                "workflowId": request.idempotency_key,
                "temporalRunId": description.run_id,
                "workflowName": request.workflow_name,
            },
        )
        return WorkflowRun(
            run_id=request.idempotency_key,
            workflow_name=request.workflow_name,
            status=status,
            output={},
            error=failure.to_payload(),
        )


def _run_blocking(make_coro: Callable[[], Any]) -> Any:
    """Run an async core coroutine to completion from synchronous code.

    The sync port is for synchronous callers (``asyncio.run`` owns the loop).
    Code already inside an event loop must call the ``*_async`` core directly,
    or invoke the sync port on a worker thread (``asyncio.to_thread``).
    """
    return asyncio.run(make_coro())


def _as_mapping(value: object) -> Mapping[str, object]:
    """Coerce a workflow result into a mapping (Temporal decodes JSON objects to dict)."""
    if isinstance(value, Mapping):
        return dict(value)
    return {"result": value}


def _classify_failure(exc: BaseException) -> tuple[AdapterFailureKind, WorkflowStatus]:
    """Map a Temporal failure (walking the cause chain) to (kind, run status)."""
    exceptions = import_module("temporalio.exceptions")
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, exceptions.CancelledError):
            return "unknown", "cancelled"
        if isinstance(cause, exceptions.TimeoutError):
            return "timeout", "failed"
        cause = getattr(cause, "cause", None)
    return "unknown", "failed"


def _root_message(exc: BaseException) -> str:
    """Return the deepest cause message for an operator-readable failure summary."""
    cause: BaseException | None = exc
    message = str(exc)
    while cause is not None:
        text = str(cause)
        if text:
            message = text
        cause = getattr(cause, "cause", None)
    return message


def _map_describe_status(status: Any) -> WorkflowStatus:
    """Map a Temporal WorkflowExecutionStatus to the vendor-neutral run status."""
    client_mod = import_module("temporalio.client")
    enum = client_mod.WorkflowExecutionStatus
    mapping: dict[Any, WorkflowStatus] = {
        enum.RUNNING: "running",
        enum.CONTINUED_AS_NEW: "running",
        enum.COMPLETED: "succeeded",
        enum.FAILED: "failed",
        enum.TIMED_OUT: "failed",
        enum.TERMINATED: "failed",
        enum.CANCELED: "cancelled",
    }
    return mapping.get(status, "running")
