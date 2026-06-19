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
        """Run the first connector-sync workflow step through Temporal."""
        return await workflow.execute_activity(
            run_workflow_step,
            payload,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=_ACTIVITY_RETRY,
        )
