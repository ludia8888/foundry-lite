"""Temporal workflow + activity definitions executed by a Foundry-lite worker.

This module is intentionally minimal and free of heavy Foundry-lite imports:
Temporal re-imports a workflow's defining module inside a deterministic sandbox,
so the workflow definition must avoid non-deterministic or expensive top-level
imports. The ``TemporalWorkflowAdapter`` starts workflows by their registered
*string* type name, so it never imports this module; only a worker process (and
the ratchet tests) register these definitions on a task queue.

``FoundryWorkflow`` is the representative durable workflow: it runs one activity
that processes the vendor-neutral input payload and returns a deterministic
result. Activities run outside the sandbox, so real work belongs there.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
from collections.abc import Callable, Sequence
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import CancelledError
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions
from temporalio.workflow import ActivityCancellationType

#: App + native-extension modules the workflow sandbox must NOT re-import.
#: Importing this module pulls in the ``foundry_lite`` package __init__ chain
#: (which loads duckdb's C extension); the deterministic sandbox re-imports a
#: workflow's module graph, and re-importing those native modules fails. They
#: are trusted, deterministic-enough host modules, so we pass them through.
_PASSTHROUGH_MODULES = ("foundry_lite", "duckdb", "pyarrow", "sqlalchemy")

#: Default task queue a Foundry-lite worker listens on and the adapter targets.
FOUNDRY_TASK_QUEUE = "foundry-lite"

#: Registered workflow type name the adapter starts by string (vendor-neutral).
FOUNDRY_WORKFLOW_NAME = "FoundryWorkflow"

#: First product workflow type used by S52 Temporal engine integration.
CONNECTOR_SYNC_WORKFLOW_NAME = "ConnectorSyncWorkflow"

#: Activity name the connector workflow runs; a worker binds it to a real sync driver.
CONNECTOR_SYNC_ACTIVITY_NAME = "run_connector_sync_step"

#: Media processing product workflow type (L5: the first product-driven Temporal use case).
MEDIA_PROCESSING_WORKFLOW_NAME = "MediaProcessingWorkflow"

#: Activity name the media workflow runs; a worker binds it to a real processing driver.
MEDIA_PROCESSING_ACTIVITY_NAME = "run_media_processing_step"

PIPELINE_DAG_WORKFLOW_NAME = "PipelineDagWorkflow"
PIPELINE_DAG_TASK_QUEUE = "foundry-lite-pipeline-dag"
PIPELINE_DAG_BEGIN_ACTIVITY_NAME = "pipeline_dag_begin"
PIPELINE_DAG_NODE_ACTIVITY_NAME = "pipeline_dag_execute_node"
PIPELINE_DAG_FINALIZE_ACTIVITY_NAME = "pipeline_dag_finalize"
PIPELINE_DAG_PREVIEW_ACTIVITY_NAME = "pipeline_dag_execute_preview"
PIPELINE_DAG_HEARTBEAT_SECONDS = 5

#: Bounded activity retry so a permanent failure surfaces instead of looping
#: forever (a timeout with unbounded retries never completes the workflow).
_ACTIVITY_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_attempts=5,
)


def foundry_sandbox_runner() -> SandboxedWorkflowRunner:
    """Build the workflow sandbox runner a Foundry-lite worker must register.

    Use this for the ``workflow_runner`` of every ``Worker`` that hosts these
    workflows (real worker bootstrap and the ratchet tests) so the sandbox does
    not re-import the app's native-extension dependency graph.
    """
    return SandboxedWorkflowRunner(
        restrictions=SandboxRestrictions.default.with_passthrough_modules(*_PASSTHROUGH_MODULES)
    )


@activity.defn(name="run_workflow_step")
async def run_workflow_step(payload: dict[str, Any]) -> dict[str, Any]:
    """Process one workflow step deterministically (echo the input as processed)."""
    return {"processed": True, **payload}


@activity.defn(name=CONNECTOR_SYNC_ACTIVITY_NAME)
async def run_connector_sync_step(payload: dict[str, Any]) -> dict[str, Any]:
    """Default connector-sync activity for workers that have not bound a real driver yet."""
    return {"processed": True, **payload}


@workflow.defn(name=FOUNDRY_WORKFLOW_NAME)
class FoundryWorkflow:
    """Representative Foundry-lite durable workflow: run a single processing step."""

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute the processing activity with a bounded timeout and retry."""
        return await workflow.execute_activity(
            run_workflow_step,
            payload,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=_ACTIVITY_RETRY,
        )


@workflow.defn(name=CONNECTOR_SYNC_WORKFLOW_NAME)
class ConnectorSyncWorkflow:
    """Product workflow entrypoint for connector sync orchestration."""

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Run connector sync through the worker-bound activity driver."""
        return await workflow.execute_activity(
            CONNECTOR_SYNC_ACTIVITY_NAME,
            payload,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=_ACTIVITY_RETRY,
        )


@workflow.defn(name=MEDIA_PROCESSING_WORKFLOW_NAME)
class MediaProcessingWorkflow:
    """Product workflow that orchestrates one media processing run (L5).

    It only *drives* the work: the activity triggers ``MediaProcessingService.process``,
    whose atomic DB commit is the sole success signal. This workflow's status is
    orchestration state — a "completed" workflow never makes an uncommitted derivative
    resolvable, and a later failed/timed-out workflow never un-commits one
    (invariant ``workflow_status_does_not_replace_domain_commit``).
    """

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Run the media processing step through Temporal by activity name."""
        return await workflow.execute_activity(
            MEDIA_PROCESSING_ACTIVITY_NAME,
            payload,
            start_to_close_timeout=timedelta(seconds=120),
            retry_policy=_ACTIVITY_RETRY,
        )


@workflow.defn(name=PIPELINE_DAG_WORKFLOW_NAME)
class PipelineDagWorkflow:
    """Deterministic fork/join controller for one immutable Pipeline plan."""

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("is_commit_forbidden") is True:
            return await workflow.execute_activity(
                PIPELINE_DAG_PREVIEW_ACTIVITY_NAME,
                payload,
                start_to_close_timeout=timedelta(seconds=300),
                heartbeat_timeout=timedelta(seconds=PIPELINE_DAG_HEARTBEAT_SECONDS),
                retry_policy=_control_retry_policy(),
                cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            )
        await workflow.execute_activity(
            PIPELINE_DAG_BEGIN_ACTIVITY_NAME,
            payload,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=_control_retry_policy(),
        )
        outcomes = await _execute_pipeline_nodes(payload)
        return await workflow.execute_activity(
            PIPELINE_DAG_FINALIZE_ACTIVITY_NAME,
            {**payload, "nodeOutcomes": outcomes},
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=_control_retry_policy(),
        )


class ConnectorSyncActivities:
    """Worker-side activity that drives the real connector snapshot commit."""

    def __init__(self, driver: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self._driver = driver

    @activity.defn(name=CONNECTOR_SYNC_ACTIVITY_NAME)
    async def run_connector_sync_step(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Trigger connector snapshot ingest and return the committed dataset evidence."""
        return self._driver(payload)


class MediaProcessingActivities:
    """Worker-side activity that drives real media processing.

    The activity runs outside the deterministic sandbox, so it may call into the
    application. A worker constructs this with a ``driver`` that performs one
    ``MediaProcessingService.process`` for the payload and returns the orchestration
    result. The returned mapping is workflow output only — never a serving-truth
    substitute for the DB COMMITTED derivative.
    """

    def __init__(self, driver: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self._driver = driver

    @activity.defn(name=MEDIA_PROCESSING_ACTIVITY_NAME)
    async def run_media_processing_step(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Trigger media processing for the payload and return the orchestration result."""
        return self._driver(payload)


class PipelineDagActivities:
    """Worker bindings for DB-fenced begin, node, and terminal operations."""

    def __init__(
        self,
        driver: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        worker_id: str = "pipeline-worker",
        preview_driver: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        node_subprocess_argv: Sequence[str] | None = None,
        termination_grace_seconds: float = 5.0,
    ) -> None:
        self._driver = driver
        self._worker_id = worker_id
        self._preview_driver = preview_driver
        self._node_subprocess_argv = tuple(node_subprocess_argv or ())
        self._termination_grace_seconds = termination_grace_seconds

    @activity.defn(name=PIPELINE_DAG_BEGIN_ACTIVITY_NAME)
    async def begin(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._drive("begin", payload)

    @activity.defn(name=PIPELINE_DAG_NODE_ACTIVITY_NAME)
    async def execute_node(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._drive("execute_node", payload)

    @activity.defn(name=PIPELINE_DAG_FINALIZE_ACTIVITY_NAME)
    async def finalize(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._drive("finalize", payload)

    @activity.defn(name=PIPELINE_DAG_PREVIEW_ACTIVITY_NAME)
    async def execute_preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._drive("execute_preview", payload)

    async def _drive(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        info = activity.info()
        activity.heartbeat({"operation": operation, "attempt": info.attempt})
        enriched = {
            **payload,
            "operation": operation,
            "temporalAttempt": info.attempt,
            "externalExecutionId": info.activity_id,
            "workflowRunId": info.workflow_id,
            "workerIdentity": self._worker_id,
        }
        try:
            result = await self._run_driver(operation, enriched)
        except asyncio.CancelledError:
            await self._cancel_execution(enriched)
            raise
        activity.heartbeat({"operation": operation, "completed": True})
        return result

    async def _run_driver(
        self,
        operation: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if operation in {"execute_node", "execute_preview"} and self._node_subprocess_argv:
            return await self._run_node_subprocess(payload)
        if operation == "execute_preview" and self._preview_driver is not None:
            return await asyncio.to_thread(self._preview_driver, payload)
        return await asyncio.to_thread(self._driver, payload)

    async def _run_node_subprocess(self, payload: dict[str, Any]) -> dict[str, Any]:
        process = await asyncio.create_subprocess_exec(
            *self._node_subprocess_argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        communicate = asyncio.create_task(process.communicate(json.dumps(payload).encode()))
        cancellation = asyncio.create_task(activity.wait_for_cancelled())
        try:
            stdout, stderr = await _wait_for_subprocess(communicate, cancellation)
        except asyncio.CancelledError:
            await _terminate_process(process, self._termination_grace_seconds)
            communicate.cancel()
            await asyncio.gather(communicate, return_exceptions=True)
            raise
        finally:
            cancellation.cancel()
            await asyncio.gather(cancellation, return_exceptions=True)
        if process.returncode:
            raise RuntimeError(_subprocess_error(stderr))
        return _subprocess_result(stdout)

    async def _cancel_execution(self, payload: dict[str, Any]) -> None:
        if payload.get("is_commit_forbidden") is True and self._preview_driver is not None:
            await asyncio.to_thread(
                self._preview_driver,
                {**payload, "operation": "cancel_preview"},
            )
            return
        await asyncio.to_thread(self._driver, {**payload, "operation": "cancel_node"})


async def _execute_pipeline_nodes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    plan = payload.get("execution_plan")
    plan = plan if isinstance(plan, dict) else {}
    nodes = _plan_rows(plan.get("nodes"))
    edges = _plan_rows(plan.get("edges"))
    pending = {str(node["nodeId"]): node for node in nodes}
    outcomes: dict[str, dict[str, Any]] = {}
    while pending:
        ready = _ready_nodes(pending, edges, outcomes)
        if not ready:
            outcomes.update(_blocked_outcomes(pending, edges, outcomes))
            break
        results = await asyncio.gather(
            *[_execute_pipeline_node(payload, node, outcomes) for node in ready],
        )
        for result in results:
            outcomes[str(result["nodeId"])] = result
            pending.pop(str(result["nodeId"]), None)
    return [outcomes[node_id] for node_id in sorted(outcomes)]


async def _execute_pipeline_node(
    payload: dict[str, Any],
    node: dict[str, Any],
    outcomes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    policy = _node_execution_policy(node)
    try:
        result = await workflow.execute_activity(
            PIPELINE_DAG_NODE_ACTIVITY_NAME,
            {**payload, "node": node, "upstreamOutcomes": outcomes},
            task_queue=_node_activity_task_queue(node),
            start_to_close_timeout=timedelta(seconds=policy["timeoutSeconds"]),
            heartbeat_timeout=timedelta(seconds=min(PIPELINE_DAG_HEARTBEAT_SECONDS, policy["timeoutSeconds"])),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=policy["initialBackoffSeconds"]),
                backoff_coefficient=2.0,
                maximum_interval=timedelta(seconds=policy["maximumBackoffSeconds"]),
                maximum_attempts=policy["maximumAttempts"],
            ),
            cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )
    except CancelledError:
        raise
    except Exception as exc:
        return {"nodeId": node["nodeId"], "status": "failed", "error": str(exc)}
    return dict(result)


def _ready_nodes(
    pending: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
    outcomes: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    ready: list[dict[str, Any]] = []
    for node_id in sorted(pending):
        upstream = _upstream_ids(node_id, edges)
        if any(_is_terminal_failure(outcomes.get(value)) for value in upstream):
            continue
        if all(outcomes.get(value, {}).get("status") == "succeeded" for value in upstream):
            ready.append(pending[node_id])
    return ready


def _blocked_outcomes(
    pending: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
    outcomes: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        node_id: {
            "nodeId": node_id,
            "status": "skipped",
            "blockedBy": [
                upstream for upstream in _upstream_ids(node_id, edges) if _is_terminal_failure(outcomes.get(upstream))
            ],
        }
        for node_id in pending
    }


def _upstream_ids(node_id: str, edges: list[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(sorted(str(edge["sourceNodeId"]) for edge in edges if str(edge.get("targetNodeId")) == node_id))


def _is_terminal_failure(outcome: dict[str, Any] | None) -> bool:
    return outcome is not None and outcome.get("status") in {"failed", "cancelled", "skipped"}


def _plan_rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict) and item.get("nodeId", item.get("edgeId"))]


def _node_execution_policy(node: dict[str, Any]) -> dict[str, int]:
    raw = node.get("executionPolicy")
    policy = raw if isinstance(raw, dict) else {}
    return {
        "maximumAttempts": int(policy.get("maximumAttempts") or 3),
        "initialBackoffSeconds": int(policy.get("initialBackoffSeconds") or 1),
        "maximumBackoffSeconds": int(policy.get("maximumBackoffSeconds") or 30),
        "timeoutSeconds": int(policy.get("timeoutSeconds") or 300),
    }


def _node_activity_task_queue(node: dict[str, Any]) -> str:
    capability = str(node.get("runtimeCapability") or "")
    return pipeline_capability_task_queue(workflow.info().task_queue, capability)


def pipeline_capability_task_queue(base_task_queue: str, runtime_capability: str) -> str:
    """Return the deterministic worker queue for an allowlisted runtime capability."""
    capability = runtime_capability.strip()
    return f"{base_task_queue}.capability.{capability}" if capability else base_task_queue


def _control_retry_policy() -> RetryPolicy:
    return RetryPolicy(
        initial_interval=timedelta(seconds=1),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(seconds=30),
        maximum_attempts=3,
    )


async def _wait_for_subprocess(
    communicate: asyncio.Task[tuple[bytes, bytes]],
    cancellation: asyncio.Task[None],
) -> tuple[bytes, bytes]:
    while True:
        done, _ = await asyncio.wait(
            {communicate, cancellation},
            timeout=1,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if cancellation in done:
            raise asyncio.CancelledError
        if communicate in done:
            return communicate.result()
        activity.heartbeat({"operation": "execute_node", "subprocess": "running"})


async def _terminate_process(
    process: asyncio.subprocess.Process,
    grace_seconds: float,
) -> None:
    if process.returncode is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), timeout=grace_seconds)
    except TimeoutError:
        os.killpg(process.pid, signal.SIGKILL)
        await process.wait()


def _subprocess_result(stdout: bytes) -> dict[str, Any]:
    prefix = b"FOUNDRY_LITE_PIPELINE_RESULT="
    line = next((item for item in reversed(stdout.splitlines()) if item.startswith(prefix)), None)
    if line is None:
        raise RuntimeError("pipeline node subprocess did not return a result")
    value = json.loads(line[len(prefix) :])
    if not isinstance(value, dict):
        raise RuntimeError("pipeline node subprocess returned an invalid result")
    return value


def _subprocess_error(stderr: bytes) -> str:
    text = stderr.decode("utf-8", errors="replace").strip()
    return text[-2000:] or "pipeline node subprocess failed"
