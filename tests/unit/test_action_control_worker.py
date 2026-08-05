from __future__ import annotations

from threading import Event
from types import SimpleNamespace

from foundry_lite_worker import action_control


def test_action_control_worker_recovers_runs_and_publishes_monitoring_alerts(monkeypatch) -> None:
    stop = Event()
    calls: list[tuple[str, object]] = []

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
    fake_foundry = SimpleNamespace(_services=SimpleNamespace(action=action))
    monkeypatch.setattr(action_control, "create_runtime_core_dependencies", lambda **_kwargs: object())
    monkeypatch.setattr(action_control, "FoundryLite", lambda **_kwargs: fake_foundry)
    monkeypatch.setenv("FOUNDRY_LITE_WORKER_ID", "action-control-test")

    action_control.run_control_loop(stop)

    assert calls == [
        ("dispatch", 100),
        ("cancel", ("action-control-test", 100)),
        ("alert", "action-control-test"),
    ]
