"""Temporal workflow and activity for fenced Action execution."""

from __future__ import annotations

import asyncio
import json
import os
import signal
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.workflow import ActivityCancellationType

from foundry_lite.application.ports.action_run_orchestrator import (
    ActionRunDispatchRequest,
    ActionRunRetryableFailure,
)

ActionRunDriver = Callable[[ActionRunDispatchRequest, str], dict[str, Any]]
ACTION_RUN_RESULT_PREFIX = b"FOUNDRY_LITE_ACTION_RESULT="


@workflow.defn(name="ActionRunWorkflow")
class ActionRunWorkflow:
    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await workflow.execute_activity(
            "drive_action_run",
            payload,
            start_to_close_timeout=timedelta(minutes=30),
            heartbeat_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(
                maximum_attempts=3,
                initial_interval=timedelta(seconds=1),
                backoff_coefficient=2.0,
                maximum_interval=timedelta(seconds=30),
            ),
            cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )


class ActionRunActivities:
    def __init__(
        self,
        driver: ActionRunDriver | None,
        *,
        worker_id: str,
        activity_subprocess_argv: Sequence[str] | None = None,
        termination_grace_seconds: float = 5.0,
    ) -> None:
        self._driver = driver
        self._worker_id = worker_id
        self._activity_subprocess_argv = tuple(activity_subprocess_argv or ())
        self._termination_grace_seconds = termination_grace_seconds

    @activity.defn(name="drive_action_run")
    async def drive(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self._drive_with_heartbeats(action_run_dispatch_request_from_payload(payload))
        except ActionRunRetryableFailure:
            raise
        except Exception as exc:
            raise ApplicationError(str(exc), type=exc.__class__.__name__, non_retryable=True) from exc

    async def _drive_with_heartbeats(self, request: ActionRunDispatchRequest) -> dict[str, Any]:
        if self._activity_subprocess_argv:
            return await self._drive_subprocess(request)
        if self._driver is None:
            raise RuntimeError("Action activity driver is not configured")
        task = asyncio.create_task(asyncio.to_thread(self._driver, request, self._worker_id))
        while not task.done():
            activity.heartbeat({"runId": request.run_id, "workerId": self._worker_id})
            try:
                return await asyncio.wait_for(asyncio.shield(task), timeout=3)
            except TimeoutError:
                continue
        return await task

    async def _drive_subprocess(self, request: ActionRunDispatchRequest) -> dict[str, Any]:
        process = await asyncio.create_subprocess_exec(
            *self._activity_subprocess_argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        payload = {**asdict(request), "worker_id": self._worker_id}
        communicate = asyncio.create_task(process.communicate(json.dumps(payload).encode()))
        cancellation = asyncio.create_task(activity.wait_for_cancelled())
        try:
            stdout, stderr = await _wait_for_action_subprocess(communicate, cancellation, request, self._worker_id)
        except asyncio.CancelledError:
            await _terminate_action_process(process, self._termination_grace_seconds)
            communicate.cancel()
            await asyncio.gather(communicate, return_exceptions=True)
            raise
        finally:
            cancellation.cancel()
            await asyncio.gather(cancellation, return_exceptions=True)
        if process.returncode:
            raise RuntimeError(_action_subprocess_error(stderr))
        return _action_subprocess_result(stdout)


def action_run_dispatch_request_from_payload(payload: Mapping[str, Any]) -> ActionRunDispatchRequest:
    execution_plan = payload.get("execution_plan")
    if not isinstance(execution_plan, Mapping):
        raise ValueError("Action Temporal payload is missing execution_plan")
    return ActionRunDispatchRequest(
        tenant_id=str(payload["tenant_id"]),
        run_id=str(payload["run_id"]),
        action_api_name=str(payload["action_api_name"]),
        request_id=str(payload["request_id"]),
        idempotency_key=str(payload["idempotency_key"]),
        execution_plan=dict(execution_plan),
    )


async def _wait_for_action_subprocess(
    communicate: asyncio.Task[tuple[bytes, bytes]],
    cancellation: asyncio.Task[None],
    request: ActionRunDispatchRequest,
    worker_id: str,
) -> tuple[bytes, bytes]:
    while True:
        done, _ = await asyncio.wait({communicate, cancellation}, timeout=1, return_when=asyncio.FIRST_COMPLETED)
        if cancellation in done:
            raise asyncio.CancelledError
        if communicate in done:
            return communicate.result()
        activity.heartbeat({"runId": request.run_id, "workerId": worker_id})


async def _terminate_action_process(process: asyncio.subprocess.Process, grace_seconds: float) -> None:
    if process.returncode is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), timeout=grace_seconds)
    except TimeoutError:
        os.killpg(process.pid, signal.SIGKILL)
        await process.wait()


def _action_subprocess_result(stdout: bytes) -> dict[str, Any]:
    line = next((item for item in reversed(stdout.splitlines()) if item.startswith(ACTION_RUN_RESULT_PREFIX)), None)
    if line is None:
        raise RuntimeError("Action activity subprocess did not return a result")
    value = json.loads(line[len(ACTION_RUN_RESULT_PREFIX) :])
    if not isinstance(value, dict):
        raise RuntimeError("Action activity subprocess returned an invalid result")
    return value


def _action_subprocess_error(stderr: bytes) -> str:
    text = stderr.decode("utf-8", errors="replace").strip()
    return text[-2000:] or "Action activity subprocess failed"
