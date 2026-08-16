from __future__ import annotations

import logging
from threading import Event
from types import SimpleNamespace

import pytest
from foundry_lite_worker import action_control


def test_action_control_worker_recovers_runs_and_publishes_monitoring_alerts(monkeypatch) -> None:
    stop = Event()
    calls: list[tuple[str, object]] = []
    close_calls: list[str] = []

    class AsyncRuns:
        def recover_all_dispatches(self, *, limit: int) -> None:
            calls.append(("dispatch", limit))

    class DistributedRuns:
        def recover_all_cancellations(self, *, worker_id: str, limit: int) -> None:
            calls.append(("cancel", (worker_id, limit)))

    class MonitoringAlerts:
        def publish_all(self, *, worker_id: str) -> None:
            calls.append(("alert", worker_id))
            stop.set()

    action = SimpleNamespace(
        async_run=AsyncRuns(),
        distributed=DistributedRuns(),
        monitoring_alerts=MonitoringAlerts(),
    )
    fake_foundry = SimpleNamespace(
        _services=SimpleNamespace(action=action),
        close=lambda: close_calls.append("close"),
    )
    monkeypatch.setattr(action_control, "create_runtime_core_dependencies", lambda **_kwargs: object())
    monkeypatch.setattr(action_control, "FoundryLite", lambda **_kwargs: fake_foundry)
    monkeypatch.setenv("FOUNDRY_LITE_WORKER_ID", "action-control-test")

    action_control.run_control_loop(stop)

    assert calls == [
        ("dispatch", 100),
        ("cancel", ("action-control-test", 100)),
        ("alert", "action-control-test"),
    ]
    assert close_calls == ["close"]


def test_action_control_worker_continues_after_failure_without_logging_exception_text(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    stop = Event()
    ticks = 0
    close_calls: list[str] = []

    class AsyncRuns:
        def recover_all_dispatches(self, *, limit: int) -> None:
            nonlocal ticks
            ticks += 1
            if ticks == 1:
                raise RuntimeError("database password=must-not-leak")
            stop.set()

    action = SimpleNamespace(
        async_run=AsyncRuns(),
        distributed=SimpleNamespace(recover_all_cancellations=lambda **_kwargs: None),
        monitoring_alerts=SimpleNamespace(publish_all=lambda **_kwargs: None),
    )
    fake_foundry = SimpleNamespace(
        _services=SimpleNamespace(action=action),
        close=lambda: close_calls.append("close"),
    )
    monkeypatch.setattr(action_control, "create_runtime_core_dependencies", lambda **_kwargs: object())
    monkeypatch.setattr(action_control, "FoundryLite", lambda **_kwargs: fake_foundry)
    monkeypatch.setenv("FOUNDRY_LITE_ACTION_CONTROL_INTERVAL_SECONDS", "0")

    with caplog.at_level(logging.ERROR):
        action_control.run_control_loop(stop)

    assert ticks == 2
    assert "action.control.tick_failed" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "must-not-leak" not in caplog.text
    assert close_calls == ["close"]
