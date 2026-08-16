"""Cancellation, payload, and subprocess boundaries for Temporal Action activities."""

from __future__ import annotations

import asyncio
import signal
import sys
from collections.abc import Coroutine
from typing import Any

import pytest
from foundry_lite.application.ports.action_run_orchestrator import (
    ActionRunDispatchRequest,
    ActionRunRetryableFailure,
)
from foundry_lite.infrastructure.adapters import action_temporal_workflows as workflows
from temporalio.exceptions import ApplicationError


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


def _payload(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "action_api_name": "ApproveOrder",
        "request_id": "request-a",
        "idempotency_key": "key-a",
        "execution_plan": {"planHash": "sha256:plan"},
    }
    value.update(overrides)
    return value


def _request() -> ActionRunDispatchRequest:
    return workflows.action_run_dispatch_request_from_payload(_payload())


def test_payload_parser_preserves_exact_nonempty_identity_and_execution_plan() -> None:
    request = workflows.action_run_dispatch_request_from_payload(_payload())
    assert request == ActionRunDispatchRequest(
        tenant_id="tenant-a",
        run_id="run-a",
        action_api_name="ApproveOrder",
        request_id="request-a",
        idempotency_key="key-a",
        execution_plan={"planHash": "sha256:plan"},
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"execution_plan": None},
        {"execution_plan": []},
        {"tenant_id": None},
        {"run_id": 7},
        {"action_api_name": ""},
        {"request_id": "   "},
        {"idempotency_key": False},
    ],
)
def test_payload_parser_never_stringifies_missing_or_wrongly_typed_identity(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="Action Temporal payload"):
        workflows.action_run_dispatch_request_from_payload(_payload(**overrides))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"worker_id": ""},
        {"worker_id": None},
        {"worker_id": "worker", "termination_grace_seconds": 0},
        {"worker_id": "worker", "termination_grace_seconds": "5"},
        {"worker_id": "worker", "termination_grace_seconds": float("nan")},
        {"worker_id": "worker", "activity_subprocess_argv": "python"},
        {"worker_id": "worker", "activity_subprocess_argv": ("python", "")},
    ],
)
def test_activity_configuration_rejects_unbounded_or_ambiguous_process_controls(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="Action activity"):
        workflows.ActionRunActivities(None, **kwargs)  # type: ignore[arg-type]


def test_drive_redacts_permanent_error_but_preserves_explicit_retryable_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activities = workflows.ActionRunActivities(lambda _request, _worker: {}, worker_id="worker-a")

    async def permanent(_request: ActionRunDispatchRequest) -> dict[str, Any]:
        raise ValueError("database_url=postgres://admin:secret@db/orders")

    monkeypatch.setattr(activities, "_drive_with_heartbeats", permanent)
    with pytest.raises(ApplicationError) as exc_info:
        _run(activities.drive(_payload()))
    assert "postgres://" not in str(exc_info.value)
    assert "***MASKED***" in str(exc_info.value)
    assert exc_info.value.non_retryable is True

    async def retryable(_request: ActionRunDispatchRequest) -> dict[str, Any]:
        raise ActionRunRetryableFailure("token=retry-secret")

    monkeypatch.setattr(activities, "_drive_with_heartbeats", retryable)
    with pytest.raises(ActionRunRetryableFailure, match="MASKED") as retry_info:
        _run(activities.drive(_payload()))
    assert "retry-secret" not in str(retry_info.value)


def test_direct_driver_heartbeats_until_completion_and_requires_a_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    heartbeats: list[object] = []
    monkeypatch.setattr(workflows.activity, "heartbeat", heartbeats.append)

    def driver(request: ActionRunDispatchRequest, worker_id: str) -> dict[str, Any]:
        return {"runId": request.run_id, "workerId": worker_id}

    activities = workflows.ActionRunActivities(driver, worker_id="worker-a")
    assert _run(activities._drive_with_heartbeats(_request())) == {"runId": "run-a", "workerId": "worker-a"}
    assert heartbeats or activities._driver is driver

    unavailable = workflows.ActionRunActivities(None, worker_id="worker-a")
    with pytest.raises(RuntimeError, match="not configured"):
        _run(unavailable._drive_with_heartbeats(_request()))


def test_direct_driver_retries_after_one_heartbeat_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    heartbeats: list[object] = []
    waits = 0
    release = asyncio.Event()
    monkeypatch.setattr(workflows.activity, "heartbeat", heartbeats.append)
    original_wait_for = workflows.asyncio.wait_for

    async def controlled_to_thread(function: object, *args: object) -> object:
        await release.wait()
        return function(*args)  # type: ignore[operator]

    async def first_timeout_then_result(awaitable: object, *, timeout: float) -> object:
        nonlocal waits
        waits += 1
        assert timeout == 3
        if waits == 1:
            release.set()
            raise TimeoutError
        return await original_wait_for(awaitable, timeout=timeout)  # type: ignore[arg-type]

    monkeypatch.setattr(workflows.asyncio, "to_thread", controlled_to_thread)
    monkeypatch.setattr(workflows.asyncio, "wait_for", first_timeout_then_result)
    activities = workflows.ActionRunActivities(
        lambda request, worker: {"runId": request.run_id, "workerId": worker},
        worker_id="worker-a",
    )

    assert _run(activities._drive_with_heartbeats(_request())) == {
        "runId": "run-a",
        "workerId": "worker-a",
    }
    assert waits == 2
    assert len(heartbeats) == 2


def test_wait_for_subprocess_prefers_cancellation_and_emits_bounded_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    heartbeats: list[object] = []
    monkeypatch.setattr(workflows.activity, "heartbeat", heartbeats.append)

    async def completed() -> tuple[bytes, bytes]:
        return b"stdout", b"stderr"

    async def never_cancelled() -> None:
        await asyncio.Event().wait()

    async def never_result() -> tuple[bytes, bytes]:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def cancellation_requested() -> None:
        return

    async def success() -> tuple[bytes, bytes]:
        communicate = asyncio.create_task(completed())
        cancellation = asyncio.create_task(never_cancelled())
        try:
            return await workflows._wait_for_action_subprocess(communicate, cancellation, _request(), "worker-a")
        finally:
            cancellation.cancel()
            await asyncio.gather(cancellation, return_exceptions=True)

    assert _run(success()) == (b"stdout", b"stderr")

    async def cancelled() -> None:
        communicate = asyncio.create_task(never_result())
        cancellation = asyncio.create_task(cancellation_requested())
        try:
            with pytest.raises(asyncio.CancelledError):
                await workflows._wait_for_action_subprocess(communicate, cancellation, _request(), "worker-a")
        finally:
            communicate.cancel()
            await asyncio.gather(communicate, cancellation, return_exceptions=True)

    _run(cancelled())


class _Process:
    def __init__(self, *, returncode: int | None = None, pid: int = 41) -> None:
        self.returncode = returncode
        self.pid = pid
        self.wait_count = 0

    async def wait(self) -> int:
        self.wait_count += 1
        return self.returncode or 0


def test_process_termination_is_race_safe_and_escalates_after_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    finished = _Process(returncode=0)
    _run(workflows._terminate_action_process(finished, 1))  # type: ignore[arg-type]
    assert finished.wait_count == 0

    raced = _Process()
    monkeypatch.setattr(workflows.os, "killpg", lambda *_: (_ for _ in ()).throw(ProcessLookupError()))
    _run(workflows._terminate_action_process(raced, 1))  # type: ignore[arg-type]
    assert raced.wait_count == 1

    signals: list[signal.Signals] = []
    stubborn = _Process()
    monkeypatch.setattr(workflows.os, "killpg", lambda _pid, sent: signals.append(sent))

    async def timeout(_awaitable: object, *, timeout: float) -> object:
        assert timeout == 0.5
        _awaitable.close()  # type: ignore[attr-defined]
        raise TimeoutError

    monkeypatch.setattr(workflows.asyncio, "wait_for", timeout)
    _run(workflows._terminate_action_process(stubborn, 0.5))  # type: ignore[arg-type]
    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert stubborn.wait_count == 1


@pytest.mark.parametrize(
    ("stdout", "message"),
    [
        (b"ordinary log", "did not return a result"),
        (workflows.ACTION_RUN_RESULT_PREFIX + b"[]", "invalid result"),
        (workflows.ACTION_RUN_RESULT_PREFIX + b'{"value":NaN}', "invalid JSON"),
    ],
)
def test_subprocess_result_requires_a_framed_json_object(stdout: bytes, message: str) -> None:
    with pytest.raises(RuntimeError, match=message):
        workflows._action_subprocess_result(stdout)


def test_subprocess_result_uses_last_frame_and_stderr_is_bounded_and_redacted() -> None:
    stdout = b"\n".join(
        [
            workflows.ACTION_RUN_RESULT_PREFIX + b'{"status":"old"}',
            b"diagnostic",
            workflows.ACTION_RUN_RESULT_PREFIX + b'{"status":"succeeded"}',
        ]
    )
    assert workflows._action_subprocess_result(stdout) == {"status": "succeeded"}
    assert workflows._action_subprocess_error(b"") == "Action activity subprocess failed"
    assert workflows._action_subprocess_error(b"password=super-secret") == "***MASKED***"
    assert len(workflows._action_subprocess_error(b"x" * 3_000)) == 2_000


def test_real_action_subprocess_reads_exact_payload_and_returns_framed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def never_cancelled() -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(workflows.activity, "wait_for_cancelled", never_cancelled)
    script = (
        "import json,sys; "
        "payload=json.load(sys.stdin); "
        "print('FOUNDRY_LITE_ACTION_RESULT=' + json.dumps({"
        "'runId':payload['run_id'],'workerId':payload['worker_id']}))"
    )
    activities = workflows.ActionRunActivities(
        None,
        worker_id="worker-real",
        activity_subprocess_argv=(sys.executable, "-c", script),
    )

    assert _run(activities._drive_with_heartbeats(_request())) == {
        "runId": "run-a",
        "workerId": "worker-real",
    }


def test_real_action_subprocess_nonzero_exit_redacts_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def never_cancelled() -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(workflows.activity, "wait_for_cancelled", never_cancelled)
    activities = workflows.ActionRunActivities(
        None,
        worker_id="worker-real",
        activity_subprocess_argv=(
            sys.executable,
            "-c",
            "import sys; sys.stdin.read(); sys.stderr.write('password=child-secret'); raise SystemExit(4)",
        ),
    )

    with pytest.raises(RuntimeError, match="MASKED") as exc_info:
        _run(activities._drive_with_heartbeats(_request()))
    assert "child-secret" not in str(exc_info.value)


def test_real_action_subprocess_cancellation_terminates_the_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def cancellation_requested() -> None:
        return

    monkeypatch.setattr(workflows.activity, "wait_for_cancelled", cancellation_requested)
    activities = workflows.ActionRunActivities(
        None,
        worker_id="worker-real",
        activity_subprocess_argv=(sys.executable, "-c", "import signal; signal.pause()"),
        termination_grace_seconds=1,
    )

    with pytest.raises(asyncio.CancelledError):
        _run(activities._drive_with_heartbeats(_request()))
