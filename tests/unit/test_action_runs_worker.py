"""Temporal Action-run worker entrypoint.

This entrypoint had no test. The part worth pinning is the activity subprocess boundary: the
parent reads the child's stdout, so the result must be framed with the agreed prefix and must
be the only thing the child emits on that stream. A malformed payload has to fail loudly at
the boundary rather than being driven as if it were a request.

`run_worker` itself is not exercised here — it needs a live Temporal server and belongs to the
runtime lane (`quality:action-temporal-*`).
"""

from __future__ import annotations

import io
import json
import sys
from typing import Any

import pytest
from foundry_lite.infrastructure.adapters.action_temporal_workflows import ACTION_RUN_RESULT_PREFIX
from foundry_lite_worker import action_runs


class _RecordingDistributed:
    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.result = result if result is not None else {"status": "SUCCEEDED"}
        self.calls: list[tuple[object, str]] = []

    def drive(self, request: object, *, worker_id: str) -> dict[str, Any]:
        self.calls.append((request, worker_id))
        return self.result


class _StubFoundry:
    def __init__(self, distributed: _RecordingDistributed) -> None:
        class _Action:
            distributed = None

        class _Services:
            action = _Action()

        _Action.distributed = distributed  # type: ignore[assignment]
        self._services = _Services()


@pytest.fixture
def driven(monkeypatch: pytest.MonkeyPatch) -> _RecordingDistributed:
    recorded = _RecordingDistributed()
    monkeypatch.setattr(action_runs, "create_runtime_core_dependencies", lambda **_: object())
    monkeypatch.setattr(action_runs, "FoundryLite", lambda **_: _StubFoundry(recorded))
    monkeypatch.setattr(action_runs, "action_run_dispatch_request_from_payload", lambda payload: payload)
    return recorded


def _run_activity_with(payload: object, monkeypatch: pytest.MonkeyPatch) -> bytes:
    stdin = io.BytesIO(json.dumps(payload).encode())
    stdout = io.BytesIO()
    monkeypatch.setattr(sys, "stdin", type("_In", (), {"buffer": stdin})())
    monkeypatch.setattr(sys, "stdout", type("_Out", (), {"buffer": stdout})())
    action_runs.run_activity()
    return stdout.getvalue()


def test_activity_drives_the_request_with_the_worker_identity(
    driven: _RecordingDistributed, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run_activity_with({"worker_id": "pod-7", "action_run_id": "run-1"}, monkeypatch)

    assert len(driven.calls) == 1
    request, worker_id = driven.calls[0]
    assert worker_id == "pod-7"
    assert request == {"action_run_id": "run-1"}, "worker_id is transport, not part of the request"


def test_activity_frames_its_result_with_the_agreed_prefix(
    driven: _RecordingDistributed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The parent scans stdout for this prefix; an unframed result is invisible to it."""
    driven.result = {"status": "SUCCEEDED", "actionRunId": "run-1"}

    written = _run_activity_with({"worker_id": "pod-7"}, monkeypatch)

    assert written.startswith(ACTION_RUN_RESULT_PREFIX)
    body = written[len(ACTION_RUN_RESULT_PREFIX) :].strip()
    assert json.loads(body) == {"status": "SUCCEEDED", "actionRunId": "run-1"}


def test_activity_result_is_deterministically_ordered(
    driven: _RecordingDistributed, monkeypatch: pytest.MonkeyPatch
) -> None:
    driven.result = {"z": 1, "a": 2, "m": 3}

    written = _run_activity_with({"worker_id": "pod-7"}, monkeypatch)

    body = written[len(ACTION_RUN_RESULT_PREFIX) :].strip().decode()
    assert body == '{"a": 2, "m": 3, "z": 1}'


@pytest.mark.parametrize("payload", [[], "text", 3, None])
def test_activity_rejects_a_payload_that_is_not_an_object(
    driven: _RecordingDistributed, monkeypatch: pytest.MonkeyPatch, payload: object
) -> None:
    with pytest.raises(ValueError, match="must be an object"):
        _run_activity_with(payload, monkeypatch)

    assert driven.calls == [], "a malformed payload must never reach the run driver"


def test_activity_requires_a_worker_id(driven: _RecordingDistributed, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(KeyError):
        _run_activity_with({"action_run_id": "run-1"}, monkeypatch)

    assert driven.calls == []


def test_main_runs_the_activity_when_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(action_runs, "run_activity", lambda: calls.append("activity"))
    monkeypatch.setattr(action_runs.asyncio, "run", lambda _coro: calls.append("worker"))
    monkeypatch.setattr(sys, "argv", ["action_runs.py", "activity"])

    action_runs.main()

    assert calls == ["activity"]


def test_main_starts_the_temporal_worker_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(action_runs, "run_activity", lambda: calls.append("activity"))

    def _run(coro: object) -> None:
        coro.close()  # type: ignore[attr-defined]
        calls.append("worker")

    monkeypatch.setattr(action_runs.asyncio, "run", _run)
    monkeypatch.setattr(sys, "argv", ["action_runs.py"])

    action_runs.main()

    assert calls == ["worker"]
