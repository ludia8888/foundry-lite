"""Local and Temporal orchestration adapters for durable Action runs."""

from __future__ import annotations

import asyncio
import hashlib
import queue
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import timedelta
from importlib import import_module
from typing import Any, Literal

from foundry_lite.application.ports.action_run_orchestrator import (
    ActionRunDispatchRequest,
    ActionRunDispatchResult,
    ActionRunRetryableFailure,
)

ACTION_RUN_WORKFLOW_NAME = "ActionRunWorkflow"
ACTION_RUN_TASK_QUEUE = "foundry-lite-action-runs"
_LOCAL_WORKER_SHUTDOWN_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class TemporalActionRunConfig:
    address: str = "localhost:7233"
    namespace: str = "default"
    task_queue: str = ACTION_RUN_TASK_QUEUE
    execution_timeout_seconds: int = 86_400


class LocalActionRunOrchestrator:
    profile_name = "local-action-runs"

    def __init__(self) -> None:
        self._queue: queue.Queue[ActionRunDispatchRequest | None] = queue.Queue()
        self._known: set[str] = set()
        self._cancelled: set[str] = set()
        self._driver: Callable[[ActionRunDispatchRequest], None] | None = None
        self._failures: dict[str, Exception] = {}
        self._lock = threading.Lock()
        self._is_started = False
        self._is_closed = False
        self._worker: threading.Thread | None = None
        self._retry_timers: set[threading.Timer] = set()

    def register_driver(self, driver: Callable[[ActionRunDispatchRequest], None]) -> None:
        self._driver = driver

    def dispatch(self, request: ActionRunDispatchRequest) -> ActionRunDispatchResult:
        workflow_id = action_run_workflow_id(request.tenant_id, request.run_id)
        with self._lock:
            if self._is_closed:
                return ActionRunDispatchResult(workflow_id, "unknown", ACTION_RUN_TASK_QUEUE)
            if workflow_id in self._known:
                return ActionRunDispatchResult(workflow_id, "already_dispatched", ACTION_RUN_TASK_QUEUE)
            self._known.add(workflow_id)
            self._queue.put(request)
            self._start_worker()
        return ActionRunDispatchResult(workflow_id, "dispatched", ACTION_RUN_TASK_QUEUE)

    def cancel(self, tenant_id: str, workflow_run_id: str, *, reason: str | None = None) -> bool:
        del reason
        if not action_run_workflow_id_matches_tenant(tenant_id, workflow_run_id):
            return False
        with self._lock:
            if workflow_run_id not in self._known:
                return False
            self._cancelled.add(workflow_run_id)
        return True

    def _start_worker(self) -> None:
        if self._is_started:
            return
        self._is_started = True
        self._worker = threading.Thread(target=self._work_loop, name="action-run-local-worker", daemon=True)
        self._worker.start()

    def close(self) -> None:
        """Cancel deferred retries, drain accepted work, and stop the local daemon."""
        with self._lock:
            if not self._is_closed:
                self._is_closed = True
                timers = tuple(self._retry_timers)
                self._retry_timers.clear()
                if self._worker is not None:
                    self._queue.put(None)
            else:
                timers = ()
            worker = self._worker
        for timer in timers:
            timer.cancel()
        if worker is None:
            return
        if worker is threading.current_thread():
            raise RuntimeError("action orchestrator cannot close from its own worker")
        worker.join(timeout=_LOCAL_WORKER_SHUTDOWN_TIMEOUT_SECONDS)
        if worker.is_alive():
            raise RuntimeError("action orchestrator did not stop before shutdown timeout")

    def _schedule_retry(self, request: ActionRunDispatchRequest) -> None:
        timer: threading.Timer

        def enqueue() -> None:
            with self._lock:
                self._retry_timers.discard(timer)
                if not self._is_closed:
                    self._queue.put(request)

        timer = threading.Timer(1.0, enqueue)
        timer.daemon = True
        with self._lock:
            if self._is_closed:
                return
            self._retry_timers.add(timer)
        timer.start()

    def _work_loop(self) -> None:
        while True:
            request = self._queue.get()
            if request is None:
                self._queue.task_done()
                return
            try:
                workflow_id = action_run_workflow_id(request.tenant_id, request.run_id)
                # The DB cancellation marker is the source of truth.  Even a queued
                # local run must reach the driver once so it can durably transition
                # ``cancelling`` to ``cancelled`` instead of being silently dropped.
                if self._driver is not None:
                    self._driver(request)
            except ActionRunRetryableFailure:
                self._schedule_retry(request)
            except Exception as exc:
                with self._lock:
                    self._failures[workflow_id] = exc
            finally:
                self._queue.task_done()


class TemporalActionRunOrchestrator:
    profile_name = "temporal-action-runs"

    def __init__(self, config: TemporalActionRunConfig | None = None, *, client: Any | None = None) -> None:
        self._config = config or TemporalActionRunConfig()
        self._client = client

    def dispatch(self, request: ActionRunDispatchRequest) -> ActionRunDispatchResult:
        return asyncio.run(self.dispatch_async(request))

    async def dispatch_async(self, request: ActionRunDispatchRequest) -> ActionRunDispatchResult:
        workflow_id = action_run_workflow_id(request.tenant_id, request.run_id)
        status: Literal["dispatched", "already_dispatched", "unknown"] = "unknown"
        try:
            client = await self._client_handle()
            common = import_module("temporalio.common")
            exceptions = import_module("temporalio.exceptions")
            try:
                await client.start_workflow(
                    ACTION_RUN_WORKFLOW_NAME,
                    asdict(request),
                    id=workflow_id,
                    task_queue=self._config.task_queue,
                    id_reuse_policy=common.WorkflowIDReusePolicy.REJECT_DUPLICATE,
                    execution_timeout=timedelta(seconds=self._config.execution_timeout_seconds),
                )
                status = "dispatched"
            except exceptions.WorkflowAlreadyStartedError:
                status = "already_dispatched"
        except Exception:
            status = "unknown"
        return ActionRunDispatchResult(workflow_id, status, self._config.task_queue)

    def cancel(self, tenant_id: str, workflow_run_id: str, *, reason: str | None = None) -> bool:
        del reason
        return asyncio.run(self.cancel_async(tenant_id, workflow_run_id))

    def close(self) -> None:
        return None

    async def cancel_async(self, tenant_id: str, workflow_run_id: str) -> bool:
        if not action_run_workflow_id_matches_tenant(tenant_id, workflow_run_id):
            return False
        try:
            client = await self._client_handle()
            await client.get_workflow_handle(workflow_run_id).cancel()
        except Exception:
            return False
        return True

    async def _client_handle(self) -> Any:
        if self._client is not None:
            return self._client
        client_module = import_module("temporalio.client")
        return await client_module.Client.connect(self._config.address, namespace=self._config.namespace)


def action_run_workflow_id(tenant_id: str, run_id: str) -> str:
    tenant_hash = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()[:20]
    return f"flite-action:{tenant_hash}:{run_id}"


def action_run_workflow_id_matches_tenant(tenant_id: str, workflow_run_id: str) -> bool:
    return workflow_run_id.startswith(f"flite-action:{hashlib.sha256(tenant_id.encode('utf-8')).hexdigest()[:20]}:")
