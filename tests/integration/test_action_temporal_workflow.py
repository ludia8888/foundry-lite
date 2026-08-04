"""Temporal determinism and retry classification for durable Action runs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any

import pytest
from foundry_lite.application.ports.action_run_orchestrator import ActionRunRetryableFailure
from foundry_lite.infrastructure.adapters.action_run_orchestrator import ACTION_RUN_TASK_QUEUE
from foundry_lite.infrastructure.adapters.action_temporal_workflows import ActionRunActivities, ActionRunWorkflow
from foundry_lite.infrastructure.adapters.temporal_workflows import foundry_sandbox_runner
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker


@asynccontextmanager
async def _harness(driver: Callable[..., dict[str, object]]):
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        activities = ActionRunActivities(driver, worker_id="worker-temporal-1")
        async with Worker(
            environment.client,
            task_queue=ACTION_RUN_TASK_QUEUE,
            workflows=[ActionRunWorkflow],
            activities=[activities.drive],
            workflow_runner=foundry_sandbox_runner(),
        ):
            yield environment


def _run(body: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
    return asyncio.run(body())


def _payload(run_id: str) -> dict[str, object]:
    return {
        "tenant_id": "tenant-a",
        "run_id": run_id,
        "action_api_name": "ApproveOrder",
        "request_id": f"request-{run_id}",
        "idempotency_key": f"idempotency-{run_id}",
        "execution_plan": {"planHash": "sha256:plan"},
    }


def test_action_temporal_workflow_retries_only_explicit_retryable_failures() -> None:
    attempts = 0

    def flaky_driver(request, worker_id) -> dict[str, object]:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ActionRunRetryableFailure("safe transient failure")
        return {"actionRunId": request.run_id, "status": "succeeded", "workerId": worker_id}

    async def scenario() -> dict[str, object]:
        async with _harness(flaky_driver) as environment:
            return await environment.client.execute_workflow(
                ActionRunWorkflow.run,
                _payload("retry"),
                id="action-retry-workflow",
                task_queue=ACTION_RUN_TASK_QUEUE,
            )

    result = _run(scenario)
    assert result["status"] == "succeeded"
    assert attempts == 3


def test_action_temporal_workflow_does_not_retry_permanent_failures() -> None:
    attempts = 0

    def permanent_driver(_request, _worker_id) -> dict[str, object]:
        nonlocal attempts
        attempts += 1
        raise ValueError("permanent validation failure")

    async def scenario() -> None:
        async with _harness(permanent_driver) as environment:
            with pytest.raises(WorkflowFailureError):
                await environment.client.execute_workflow(
                    ActionRunWorkflow.run,
                    _payload("permanent"),
                    id="action-permanent-workflow",
                    task_queue=ACTION_RUN_TASK_QUEUE,
                )

    _run(scenario)
    assert attempts == 1
