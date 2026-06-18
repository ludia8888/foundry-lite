"""Temporal workflow adapter ratchet — tricky-failure coverage on a real server.

Runs against Temporal's time-skipping test server (no wall-clock waits: retry
backoff and execution timeouts fast-forward deterministically). The adapter's
async core is exercised with the env's client injected; one test drives the sync
port (`start_workflow`) end-to-end through a fresh-client connection.

Proof classes (docs/infra-ratchet.md):
- adapter-contract     : failure taxonomy + profile name
- normal-path          : start-and-wait returns the processed output
- retry-idempotency    : flaky activity retried to success; same key → no dup run
- concurrency-race     : two concurrent starts on one key → one run, same result
- failure-injection    : business failure → status failed + durable error payload
- partial-success      : activity partial failures retried, run still succeeds
- recovery-cleanup     : re-attach to an existing run by key after completion
- operator-evidence    : failure visible in the run error payload, not just logs
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Coroutine
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

from foundry_lite.application.ports.workflow_adapter import WorkflowStartRequest
from foundry_lite.infrastructure.adapters.temporal_workflow import (
    TemporalWorkflowAdapter,
    TemporalWorkflowAdapterConfig,
)
from foundry_lite.infrastructure.adapters.temporal_workflows import (
    FOUNDRY_TASK_QUEUE,
    FOUNDRY_WORKFLOW_NAME,
    FoundryWorkflow,
    foundry_sandbox_runner,
    run_workflow_step,
)
from temporalio import activity, workflow
from temporalio.client import WorkflowFailureError
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

# --- test-only workflows/activities (no asyncio.sleep: §18.1 forbids it in tests) ---

_FLAKY_ATTEMPTS: dict[str, int] = {}

FLAKY_WORKFLOW = "FlakyWorkflow"
SLEEPY_WORKFLOW = "SleepyWorkflow"
FAILING_WORKFLOW = "FailingWorkflow"


@activity.defn(name="flaky_step")
async def flaky_step(key: str) -> dict[str, Any]:
    """Fail the first two attempts for a key, then succeed (drives retry/backoff)."""
    _FLAKY_ATTEMPTS[key] = _FLAKY_ATTEMPTS.get(key, 0) + 1
    if _FLAKY_ATTEMPTS[key] < 3:
        raise RuntimeError(f"transient failure {_FLAKY_ATTEMPTS[key]}")
    return {"attempts": _FLAKY_ATTEMPTS[key]}


@workflow.defn(name=FLAKY_WORKFLOW)
class FlakyWorkflow:
    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await workflow.execute_activity(
            flaky_step,
            str(payload.get("key", "")),
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=5),
        )


@workflow.defn(name=SLEEPY_WORKFLOW)
class SleepyWorkflow:
    @workflow.run
    async def run(self, _payload: dict[str, Any]) -> dict[str, Any]:
        # workflow.sleep is a durable timer (not asyncio.sleep); under the
        # adapter's short execution_timeout the run times out before it returns.
        await workflow.sleep(timedelta(hours=1))
        return {"done": True}


@workflow.defn(name=FAILING_WORKFLOW)
class FailingWorkflow:
    @workflow.run
    async def run(self, _payload: dict[str, Any]) -> dict[str, Any]:
        raise ApplicationError("permanent business failure", non_retryable=True)


_ALL_WORKFLOWS = [FoundryWorkflow, FlakyWorkflow, SleepyWorkflow, FailingWorkflow]
_ALL_ACTIVITIES = [run_workflow_step, flaky_step]


@asynccontextmanager
async def _harness(*, execution_timeout_seconds: int = 300):
    """Boot a time-skipping server + worker and yield (env, adapter)."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=FOUNDRY_TASK_QUEUE,
            workflows=_ALL_WORKFLOWS,
            activities=_ALL_ACTIVITIES,
            workflow_runner=foundry_sandbox_runner(),
        ):
            adapter = TemporalWorkflowAdapter(
                TemporalWorkflowAdapterConfig(
                    task_queue=FOUNDRY_TASK_QUEUE,
                    execution_timeout_seconds=execution_timeout_seconds,
                ),
                client=env.client,
            )
            yield env, adapter


def _run(body: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
    return asyncio.run(body())


def _request(workflow_name: str, key: str, **payload: Any) -> WorkflowStartRequest:
    return WorkflowStartRequest(
        workflow_name=workflow_name,
        tenant_id="tenant-1",
        request_id=f"req-{key}",
        idempotency_key=key,
        input={"key": key, **payload},
    )


# --- adapter-contract ------------------------------------------------------


def test_temporal_adapter_declares_failure_taxonomy() -> None:
    adapter = TemporalWorkflowAdapter(TemporalWorkflowAdapterConfig())
    assert adapter.profile_name == "temporal"
    contract = adapter.failure_contract()
    assert contract.adapter_profile == "temporal"
    modes = {(mode.operation, mode.kind): mode for mode in contract.modes}
    assert modes[("start_workflow", "timeout")].is_retryable is True
    assert modes[("start_workflow", "timeout")].has_required_idempotency_key is True
    assert modes[("start_workflow", "unavailable")].is_retryable is True
    assert modes[("start_workflow", "unknown")].is_retryable is False
    assert modes[("workflow_run", "not_found")].is_retryable is False


# --- normal-path -----------------------------------------------------------


def test_start_workflow_returns_processed_output() -> None:
    async def body() -> None:
        async with _harness() as (_env, adapter):
            run = await adapter.start_workflow_async(_request(FOUNDRY_WORKFLOW_NAME, "wf-ok", value=7))
            assert run.status == "succeeded"
            assert run.run_id == "wf-ok"
            assert run.output["processed"] is True
            assert run.output["value"] == 7
            assert run.error is None

    _run(body)


# --- retry-idempotency (retry) + partial-success ---------------------------


def test_flaky_activity_is_retried_until_success() -> None:
    async def body() -> None:
        async with _harness() as (_env, adapter):
            run = await adapter.start_workflow_async(_request(FLAKY_WORKFLOW, "wf-retry"))
            assert run.status == "succeeded"
            # Two transient failures, third attempt succeeds: time-skipping
            # fast-forwarded the retry backoff with no wall-clock wait.
            assert run.output["attempts"] == 3

    _run(body)


# --- retry-idempotency (idempotent re-attach) + recovery-cleanup -----------


def test_same_idempotency_key_returns_existing_run_without_duplicate() -> None:
    async def body() -> None:
        _FLAKY_ATTEMPTS.pop("wf-idem", None)
        async with _harness() as (_env, adapter):
            first = await adapter.start_workflow_async(_request(FLAKY_WORKFLOW, "wf-idem"))
            attempts_after_first = _FLAKY_ATTEMPTS["wf-idem"]
            second = await adapter.start_workflow_async(_request(FLAKY_WORKFLOW, "wf-idem"))
            assert first.status == second.status == "succeeded"
            assert first.run_id == second.run_id == "wf-idem"
            assert second.output == first.output
            # The second start re-attached to the completed run: the activity
            # did not run again (no new attempts), so there is no duplicate run.
            assert _FLAKY_ATTEMPTS["wf-idem"] == attempts_after_first

    _run(body)


# --- concurrency-race ------------------------------------------------------


def test_concurrent_starts_on_one_key_produce_one_run() -> None:
    async def body() -> None:
        _FLAKY_ATTEMPTS.pop("wf-race", None)
        async with _harness() as (_env, adapter):
            runs = await asyncio.gather(
                adapter.start_workflow_async(_request(FLAKY_WORKFLOW, "wf-race")),
                adapter.start_workflow_async(_request(FLAKY_WORKFLOW, "wf-race")),
            )
            assert {run.run_id for run in runs} == {"wf-race"}
            assert all(run.status == "succeeded" for run in runs)
            assert runs[0].output == runs[1].output

    _run(body)


# --- failure-injection + operator-evidence ---------------------------------


def test_business_failure_returns_durable_error_payload() -> None:
    async def body() -> None:
        async with _harness() as (_env, adapter):
            run = await adapter.start_workflow_async(_request(FAILING_WORKFLOW, "wf-fail"))
            assert run.status == "failed"
            assert run.output == {}
            assert run.error is not None
            # Operator-evidence: the failure is visible in a durable run payload,
            # not only in a log line.
            assert run.error["adapterProfile"] == "temporal"
            assert run.error["operation"] == "start_workflow"
            assert run.error["kind"] == "unknown"
            assert run.error["retryable"] is False
            assert "permanent business failure" in run.error["operatorMessage"]
            assert run.error["details"]["workflowId"] == "wf-fail"
            assert run.error["details"]["temporalRunId"]

    _run(body)


# --- failure-injection (timeout) -------------------------------------------


def test_execution_timeout_is_reported_as_retryable_timeout() -> None:
    async def body() -> None:
        async with _harness(execution_timeout_seconds=2) as (_env, adapter):
            run = await adapter.start_workflow_async(_request(SLEEPY_WORKFLOW, "wf-timeout"))
            assert run.status == "failed"
            assert run.error is not None
            assert run.error["kind"] == "timeout"
            assert run.error["retryable"] is True
            assert run.error["timeoutSeconds"] == 2

    _run(body)


# --- failure-injection (cancellation) --------------------------------------


def test_cancelled_workflow_is_reported_as_cancelled() -> None:
    async def body() -> None:
        async with _harness() as (env, adapter):
            handle = await env.client.start_workflow(
                SLEEPY_WORKFLOW,
                {"key": "wf-cancel"},
                id="wf-cancel",
                task_queue=FOUNDRY_TASK_QUEUE,
            )
            await handle.cancel()
            # Cancellation is asynchronous: await the result so the workflow
            # reaches its terminal cancelled state before we observe it.
            with contextlib.suppress(WorkflowFailureError):
                await handle.result()
            run = await adapter.workflow_run_async("wf-cancel")
            assert run is not None
            assert run.status == "cancelled"
            # The adapter's start path also classifies a terminal cancelled run:
            # an idempotent re-start re-attaches and reports cancelled, not a dup.
            restart = await adapter.start_workflow_async(_request(SLEEPY_WORKFLOW, "wf-cancel"))
            assert restart.status == "cancelled"

    _run(body)


# --- workflow_run lookup ---------------------------------------------------


def test_workflow_run_returns_none_for_unknown_id() -> None:
    async def body() -> None:
        async with _harness() as (_env, adapter):
            assert await adapter.workflow_run_async("does-not-exist") is None

    _run(body)


def test_workflow_run_describes_completed_run() -> None:
    async def body() -> None:
        async with _harness() as (_env, adapter):
            await adapter.start_workflow_async(_request(FOUNDRY_WORKFLOW_NAME, "wf-lookup", n=1))
            run = await adapter.workflow_run_async("wf-lookup")
            assert run is not None
            assert run.status == "succeeded"
            assert run.workflow_name == FOUNDRY_WORKFLOW_NAME

    _run(body)


# --- sync port bridge (asyncio.run path, fresh client) ---------------------


def test_sync_start_workflow_bridges_to_async_core() -> None:
    async def body() -> None:
        async with _harness() as (env, _adapter):
            address = env.client.service_client.config.target_host
            # Fresh-client adapter (no injected client): the sync port runs the
            # async core via asyncio.run on a worker thread, leaving the test
            # loop free to drive the worker.
            sync_adapter = TemporalWorkflowAdapter(
                TemporalWorkflowAdapterConfig(address=address, task_queue=FOUNDRY_TASK_QUEUE)
            )
            run = await asyncio.to_thread(sync_adapter.start_workflow, _request(FOUNDRY_WORKFLOW_NAME, "wf-sync", v=9))
            assert run.status == "succeeded"
            assert run.output["v"] == 9

    _run(body)
