"""Local and Temporal Pipeline DAG orchestration adapters."""

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

from foundry_lite.application.ports.pipeline_dag_orchestrator import (
    PipelineDagDispatchRequest,
    PipelineDagDispatchResult,
)

PIPELINE_DAG_WORKFLOW_NAME = "PipelineDagWorkflow"
PIPELINE_DAG_TASK_QUEUE = "foundry-lite-pipeline-dag"
_LOCAL_WORKER_SHUTDOWN_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class TemporalPipelineDagConfig:
    address: str = "localhost:7233"
    namespace: str = "default"
    task_queue: str = PIPELINE_DAG_TASK_QUEUE
    execution_timeout_seconds: int = 86_400


class LocalPipelineDagOrchestrator:
    """Development adapter with a process-local queue and background worker."""

    profile_name = "local-pipeline-dag"

    def __init__(self) -> None:
        self._queue: queue.Queue[PipelineDagDispatchRequest | None] = queue.Queue()
        self._known: set[str] = set()
        self._cancelled: set[str] = set()
        self._failures: dict[str, Exception] = {}
        self._driver: Callable[[PipelineDagDispatchRequest], None] | None = None
        self._lock = threading.Lock()
        self._worker_started = False
        self._worker: threading.Thread | None = None
        self._is_closed = False

    def register_driver(self, driver: Callable[[PipelineDagDispatchRequest], None]) -> None:
        self._driver = driver

    def dispatch(self, request: PipelineDagDispatchRequest) -> PipelineDagDispatchResult:
        workflow_run_id = pipeline_dag_workflow_id(request.tenant_id, request.run_id)
        with self._lock:
            if self._is_closed:
                return PipelineDagDispatchResult(workflow_run_id, "unknown", PIPELINE_DAG_TASK_QUEUE)
            if workflow_run_id in self._known:
                return PipelineDagDispatchResult(
                    workflow_run_id=workflow_run_id,
                    status="already_dispatched",
                    task_queue=PIPELINE_DAG_TASK_QUEUE,
                )
            self._known.add(workflow_run_id)
            self._queue.put(request)
            self._start_worker()
        return PipelineDagDispatchResult(
            workflow_run_id=workflow_run_id,
            status="dispatched",
            task_queue=PIPELINE_DAG_TASK_QUEUE,
        )

    def cancel(
        self,
        tenant_id: str,
        workflow_run_id: str,
        *,
        reason: str | None = None,
    ) -> bool:
        del reason
        if not pipeline_dag_workflow_id_matches_tenant(tenant_id, workflow_run_id):
            return False
        with self._lock:
            if workflow_run_id not in self._known:
                return False
            self._cancelled.add(workflow_run_id)
        return True

    def failure_for(self, workflow_run_id: str) -> Exception | None:
        """Expose a local driver failure without terminating the queue worker."""

        with self._lock:
            return self._failures.get(workflow_run_id)

    def close(self) -> None:
        """Finish accepted work, then stop the local daemon before its DB is disposed."""
        with self._lock:
            if not self._is_closed:
                self._is_closed = True
                if self._worker is not None:
                    self._queue.put(None)
            worker = self._worker
        if worker is None:
            return
        if worker is threading.current_thread():
            raise RuntimeError("pipeline orchestrator cannot close from its own worker")
        worker.join(timeout=_LOCAL_WORKER_SHUTDOWN_TIMEOUT_SECONDS)
        if worker.is_alive():
            raise RuntimeError("pipeline orchestrator did not stop before shutdown timeout")

    def _start_worker(self) -> None:
        if self._worker_started:
            return
        self._worker_started = True
        self._worker = threading.Thread(target=self._work_loop, name="pipeline-dag-local-worker", daemon=True)
        self._worker.start()

    def _work_loop(self) -> None:
        while True:
            request = self._queue.get()
            if request is None:
                self._queue.task_done()
                return
            try:
                workflow_run_id = pipeline_dag_workflow_id(request.tenant_id, request.run_id)
                if workflow_run_id not in self._cancelled and self._driver is not None:
                    self._driver(request)
            except Exception as exc:
                with self._lock:
                    self._failures[workflow_run_id] = exc
            finally:
                self._queue.task_done()


class TemporalPipelineDagOrchestrator:
    """Non-blocking Temporal start/cancel adapter for production DAG runs."""

    profile_name = "temporal-pipeline-dag"

    def __init__(self, config: TemporalPipelineDagConfig | None = None, *, client: Any | None = None) -> None:
        self._config = config or TemporalPipelineDagConfig()
        self._client = client

    def dispatch(self, request: PipelineDagDispatchRequest) -> PipelineDagDispatchResult:
        return asyncio.run(self.dispatch_async(request))

    async def dispatch_async(self, request: PipelineDagDispatchRequest) -> PipelineDagDispatchResult:
        workflow_run_id = pipeline_dag_workflow_id(request.tenant_id, request.run_id)
        try:
            client = await self._client_handle()
            common = import_module("temporalio.common")
            exceptions = import_module("temporalio.exceptions")
            try:
                await client.start_workflow(
                    PIPELINE_DAG_WORKFLOW_NAME,
                    asdict(request),
                    id=workflow_run_id,
                    task_queue=self._config.task_queue,
                    id_reuse_policy=common.WorkflowIDReusePolicy.REJECT_DUPLICATE,
                    execution_timeout=timedelta(seconds=self._config.execution_timeout_seconds),
                )
                status: Literal["dispatched", "already_dispatched", "unknown"] = "dispatched"
            except exceptions.WorkflowAlreadyStartedError:
                status = "already_dispatched"
        except Exception:
            status = "unknown"
        return PipelineDagDispatchResult(
            workflow_run_id=workflow_run_id,
            status=status,
            task_queue=self._config.task_queue,
        )

    def cancel(
        self,
        tenant_id: str,
        workflow_run_id: str,
        *,
        reason: str | None = None,
    ) -> bool:
        del reason
        return asyncio.run(self.cancel_async(tenant_id, workflow_run_id))

    def close(self) -> None:
        return None

    async def cancel_async(self, tenant_id: str, workflow_run_id: str) -> bool:
        if not pipeline_dag_workflow_id_matches_tenant(tenant_id, workflow_run_id):
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
        return await client_module.Client.connect(
            self._config.address,
            namespace=self._config.namespace,
        )


def pipeline_dag_workflow_id(tenant_id: str, run_id: str) -> str:
    tenant_hash = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()[:20]
    return f"flite-pipeline:{tenant_hash}:{run_id}"


def pipeline_dag_workflow_id_matches_tenant(tenant_id: str, workflow_run_id: str) -> bool:
    prefix = pipeline_dag_workflow_id(tenant_id, "")
    return workflow_run_id.startswith(prefix)
