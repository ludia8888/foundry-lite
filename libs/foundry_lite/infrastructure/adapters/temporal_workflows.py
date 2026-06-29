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

from collections.abc import Callable
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

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
