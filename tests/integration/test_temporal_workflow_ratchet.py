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
import tempfile
from collections.abc import Callable, Coroutine, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback; CI/dev are POSIX.
    fcntl = None  # type: ignore[assignment]

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.workflow_adapter import WorkflowStartRequest
from foundry_lite.application.services.workflow_orchestration_service import CONNECTOR_SYNC_WORKFLOW_NAME
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.infrastructure.adapters.temporal_workflow import (
    TemporalWorkflowAdapter,
    TemporalWorkflowAdapterConfig,
)
from foundry_lite.infrastructure.adapters.temporal_workflow import _temporal_workflow_id as temporal_workflow_id
from foundry_lite.infrastructure.adapters.temporal_workflows import (
    FOUNDRY_TASK_QUEUE,
    FOUNDRY_WORKFLOW_NAME,
    ConnectorSyncWorkflow,
    FoundryWorkflow,
    foundry_sandbox_runner,
    run_workflow_step,
)
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
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


_ALL_WORKFLOWS = [FoundryWorkflow, ConnectorSyncWorkflow, FlakyWorkflow, SleepyWorkflow, FailingWorkflow]
_ALL_ACTIVITIES = [run_workflow_step, flaky_step]
_TEMPORAL_TEST_SERVER_LOCK = "foundry-lite-temporal-test-server-download.lock"


class _UnavailableStartClient:
    async def start_workflow(self, *_args: object, **_kwargs: object) -> object:
        raise ConnectionError("Temporal frontend unavailable")


class _UnavailableLookupClient:
    def get_workflow_handle(self, _run_id: str) -> object:
        return _UnavailableDescribeHandle()


class _UnavailableDescribeHandle:
    async def describe(self) -> object:
        raise ConnectionError("Temporal describe unavailable")


@contextmanager
def _temporal_test_server_download_lock(lock_path: Path | None = None) -> Iterator[None]:
    """Serialize Temporal SDK test-server downloads across xdist workers."""
    if fcntl is None:
        yield
        return

    path = lock_path or Path(tempfile.gettempdir()) / _TEMPORAL_TEST_SERVER_LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _clear_stale_temporal_downloads() -> None:
    """Remove incomplete test-server download temp files (safe under the lock).

    The SDK waits 20s for an existing ``*.downloading`` file expecting another
    downloader; a stale one left by a timed-out cold download makes every later
    start fail. Under the cross-worker lock no real download is in flight, so any
    such temp file is genuinely stale.
    """
    for stale in Path(tempfile.gettempdir()).glob("temporal-test-server-*.downloading"):
        with contextlib.suppress(OSError):
            stale.unlink()


@pytest.fixture(scope="module", autouse=True)
def _temporal_test_server_warm() -> None:
    """Download/cache the time-skipping server once per worker before timed tests.

    A cold download can exceed the SDK's 20s window under parallel CI load; doing
    it once here, serialized across workers and retried on a cleaned temp dir,
    keeps the per-test starts cache-warm and stable.
    """

    async def _start_stop() -> None:
        async with await WorkflowEnvironment.start_time_skipping():
            return

    with _temporal_test_server_download_lock():
        last_error: Exception | None = None
        for _ in range(6):
            _clear_stale_temporal_downloads()
            try:
                asyncio.run(_start_stop())
                return
            except RuntimeError as exc:  # noqa: PERF203 - retry a slow cold download
                last_error = exc
        if last_error is not None:
            raise last_error


@asynccontextmanager
async def _harness(*, execution_timeout_seconds: int = 300):
    """Boot a time-skipping server + worker and yield (env, adapter)."""
    with _temporal_test_server_download_lock():
        env_context = await WorkflowEnvironment.start_time_skipping()
    async with env_context as env:
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
    assert modes[("workflow_run", "unavailable")].is_retryable is True


# --- normal-path -----------------------------------------------------------


def test_start_workflow_returns_processed_output() -> None:
    async def body() -> None:
        async with _harness() as (_env, adapter):
            run = await adapter.start_workflow_async(_request(FOUNDRY_WORKFLOW_NAME, "wf-ok", value=7))
            assert run.status == "succeeded"
            assert run.run_id.startswith("flite:")
            assert run.run_id != "wf-ok"
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
            assert first.run_id == second.run_id
            assert first.run_id != "wf-idem"
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
            assert len({run.run_id for run in runs}) == 1
            assert runs[0].run_id != "wf-race"
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
            details = cast(Mapping[str, object], run.error["details"])
            assert "permanent business failure" in str(run.error["operatorMessage"])
            assert details["workflowId"] == run.run_id
            assert details["workflowId"] != "wf-fail"
            assert details["temporalRunId"]

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


def test_start_workflow_temporal_unavailable_returns_retryable_error_payload() -> None:
    async def body() -> None:
        adapter = TemporalWorkflowAdapter(client=_UnavailableStartClient())

        run = await adapter.start_workflow_async(_request(FOUNDRY_WORKFLOW_NAME, "wf-unavailable", value=1))

        assert run.status == "failed"
        assert run.output == {}
        assert run.error is not None
        assert run.error["adapterProfile"] == "temporal"
        assert run.error["operation"] == "start_workflow"
        assert run.error["kind"] == "unavailable"
        assert run.error["retryable"] is True
        assert run.error["idempotencyKey"] == "wf-unavailable"
        details = cast(Mapping[str, object], run.error["details"])
        assert details["workflowId"] == run.run_id
        assert details["workflowId"] != "wf-unavailable"

    _run(body)


def test_workflow_run_temporal_unavailable_raises_retryable_adapter_error() -> None:
    async def body() -> None:
        adapter = TemporalWorkflowAdapter(client=_UnavailableLookupClient())

        with pytest.raises(AdapterError) as raised:
            await adapter.workflow_run_async("wf-lookup-down")

        failure = raised.value.failure
        assert failure.adapter_profile == "temporal"
        assert failure.operation == "workflow_run"
        assert failure.kind == "unavailable"
        assert failure.is_retryable is True
        assert failure.details["workflowId"] == "wf-lookup-down"

    _run(body)


# --- failure-injection (cancellation) --------------------------------------


def test_cancelled_workflow_is_reported_as_cancelled() -> None:
    async def body() -> None:
        async with _harness() as (env, adapter):
            request = _request(SLEEPY_WORKFLOW, "wf-cancel")
            workflow_id = temporal_workflow_id(request)
            handle = await env.client.start_workflow(
                SLEEPY_WORKFLOW,
                {"key": "wf-cancel"},
                id=workflow_id,
                task_queue=FOUNDRY_TASK_QUEUE,
            )
            await handle.cancel()
            # Cancellation is asynchronous: await the result so the workflow
            # reaches its terminal cancelled state before we observe it.
            with contextlib.suppress(WorkflowFailureError):
                await handle.result()
            run = await adapter.workflow_run_async(workflow_id)
            assert run is not None
            assert run.status == "cancelled"
            # The adapter's start path also classifies a terminal cancelled run:
            # an idempotent re-start re-attaches and reports cancelled, not a dup.
            restart = await adapter.start_workflow_async(request)
            assert restart.run_id == workflow_id
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
            started = await adapter.start_workflow_async(_request(FOUNDRY_WORKFLOW_NAME, "wf-lookup", n=1))
            run = await adapter.workflow_run_async(started.run_id)
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


def test_product_connector_sync_workflow_runs_through_temporal_and_audits(tmp_path: Path) -> None:
    async def body() -> None:
        async with _harness() as (env, _adapter):
            address = env.client.service_client.config.target_host
            temporal_adapter = TemporalWorkflowAdapter(
                TemporalWorkflowAdapterConfig(address=address, task_queue=FOUNDRY_TASK_QUEUE)
            )
            dependencies = create_local_core_dependencies(storage_root=tmp_path / "temporal-product")
            foundry = FoundryLite(dependencies=replace(dependencies, workflow_adapter=temporal_adapter))
            ctx = demo_admin_context()
            foundry.datasets.ensure("raw.workflow_orders", ctx=ctx, primary_key=["order_id"])

            run = await asyncio.to_thread(
                foundry.operations.start_connector_sync_workflow,
                "raw.workflow_orders",
                connector_name="rest",
                resource_name="orders",
                idempotency_key="temporal-connector-sync-orders",
                ctx=ctx,
            )
            lookup = await asyncio.to_thread(
                foundry.operations.product_workflow_run,
                run["workflowRunId"],
                ctx=ctx,
            )

            assert str(run["workflowRunId"]).startswith("flite:")
            assert run["workflowRunId"] != "temporal-connector-sync-orders"
            assert run["workflowName"] == CONNECTOR_SYNC_WORKFLOW_NAME
            assert run["workflowProfile"] == "temporal"
            assert run["status"] == "succeeded"
            output = cast(Mapping[str, object], run["output"])
            assert output["workflowKind"] == "connector_sync"
            assert output["datasetRef"] == "raw.workflow_orders"
            assert lookup["workflowRunId"] == run["workflowRunId"]
            assert lookup["idempotencyKey"] == "temporal-connector-sync-orders"
            assert run["foundryRunId"] is not None
            detail = foundry.operations.run_detail("audit", str(run["foundryRunId"]), ctx=ctx)
            row = cast(Mapping[str, object], detail["row"])
            after_ref = cast(Mapping[str, object], row["after_ref"])
            assert row["resource_id"] == run["workflowRunId"]
            assert after_ref["workflowRunId"] == run["workflowRunId"]
            assert after_ref["foundryRunId"] == run["foundryRunId"]

    _run(body)
