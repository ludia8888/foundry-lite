"""Temporal workflow and activity for fenced Action execution."""

from __future__ import annotations

import asyncio
import json
import math
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
from foundry_lite.domain.error_redaction import scrub_error_text

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
        self._worker_id = _validated_worker_id(worker_id)
        self._activity_subprocess_argv = _validated_subprocess_argv(activity_subprocess_argv)
        self._termination_grace_seconds = _validated_termination_grace(termination_grace_seconds)

    @activity.defn(name="drive_action_run")
    async def drive(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self._drive_with_heartbeats(action_run_dispatch_request_from_payload(payload))
        except ActionRunRetryableFailure as exc:
            raise ActionRunRetryableFailure(scrub_error_text(str(exc))) from exc
        except Exception as exc:
            raise ApplicationError(scrub_error_text(str(exc)), type=exc.__class__.__name__, non_retryable=True) from exc

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
        communicate = asyncio.create_task(process.communicate(json.dumps(payload, allow_nan=False).encode()))
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


def _validated_worker_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Action activity worker_id must be non-empty")
    return value


def _validated_termination_grace(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("Action activity termination grace must be finite and positive")
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Action activity termination grace must be finite and positive")
    return float(value)


def _validated_subprocess_argv(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        raise ValueError("Action activity subprocess argv must contain non-empty text")
    if any(not isinstance(item, str) or not item for item in value):
        raise ValueError("Action activity subprocess argv must contain non-empty text")
    return tuple(value)


def action_run_dispatch_request_from_payload(payload: Mapping[str, Any]) -> ActionRunDispatchRequest:
    execution_plan = payload.get("execution_plan")
    if not isinstance(execution_plan, Mapping):
        raise ValueError("Action Temporal payload is missing execution_plan")
    return ActionRunDispatchRequest(
        tenant_id=_required_payload_text(payload, "tenant_id"),
        run_id=_required_payload_text(payload, "run_id"),
        action_api_name=_required_payload_text(payload, "action_api_name"),
        request_id=_required_payload_text(payload, "request_id"),
        idempotency_key=_required_payload_text(payload, "idempotency_key"),
        execution_plan=dict(execution_plan),
    )


def _required_payload_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Action Temporal payload {key} must be non-empty text")
    return value


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
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        await process.wait()
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=grace_seconds)
    except TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()


def _action_subprocess_result(stdout: bytes) -> dict[str, Any]:
    line = next((item for item in reversed(stdout.splitlines()) if item.startswith(ACTION_RUN_RESULT_PREFIX)), None)
    if line is None:
        raise RuntimeError("Action activity subprocess did not return a result")
    try:
        value = json.loads(
            line[len(ACTION_RUN_RESULT_PREFIX) :],
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"invalid JSON constant {value}")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError("Action activity subprocess returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Action activity subprocess returned an invalid result")
    return value


def _action_subprocess_error(stderr: bytes) -> str:
    text = stderr.decode("utf-8", errors="replace").strip()
    return scrub_error_text(text[-2000:]) if text else "Action activity subprocess failed"
