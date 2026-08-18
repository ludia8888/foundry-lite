from __future__ import annotations

from subprocess import CompletedProcess

import pytest
from foundry_lite_worker import kubernetes_worker_supervisor as supervisor
from foundry_lite_worker.kubernetes_worker_supervisor import supervise


def test_supervisor_repeats_bounded_worker_without_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []

    def run(arguments: tuple[str, ...], *, check: bool, shell: bool) -> CompletedProcess[str]:
        calls.append(arguments)
        assert check is False
        assert shell is False
        return CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr("foundry_lite_worker.kubernetes_worker_supervisor.subprocess.run", run)
    monkeypatch.setattr("foundry_lite_worker.kubernetes_worker_supervisor.time.sleep", lambda _seconds: None)

    assert supervise("foundry_lite_worker.outbox_publisher", interval_seconds=1, max_cycles=2) == 0
    assert len(calls) == 2
    assert calls[0][-2:] == ("-m", "foundry_lite_worker.outbox_publisher")


def test_supervisor_rejects_unregistered_module() -> None:
    with pytest.raises(ValueError, match="worker_module_not_allowed"):
        supervise("arbitrary.module", interval_seconds=1, max_cycles=1)


def test_supervisor_exits_on_failed_cycle_so_kubernetes_observes_it(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sleeps: list[float] = []

    def run(arguments: tuple[str, ...], *, check: bool, shell: bool) -> CompletedProcess[str]:
        return CompletedProcess(arguments, 1, "", "")

    monkeypatch.setattr(supervisor.subprocess, "run", run)
    monkeypatch.setattr(supervisor.time, "sleep", sleeps.append)

    assert supervise("foundry_lite_worker.action_control", interval_seconds=2, max_cycles=2) == 1
    events = [__import__("json").loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [event["event"] for event in events] == ["worker_cycle_failed"]
    assert sleeps == []


@pytest.mark.parametrize(("interval_seconds", "max_cycles"), [(0, 1), (301, 1), (1, -1)])
def test_supervisor_rejects_invalid_loop_bounds(interval_seconds: float, max_cycles: int) -> None:
    with pytest.raises(ValueError, match="worker_supervisor_configuration_invalid"):
        supervise("foundry_lite_worker.outbox_publisher", interval_seconds=interval_seconds, max_cycles=max_cycles)


def test_supervisor_main_returns_safe_configuration_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        supervisor.main(
            [
                "--module",
                "foundry_lite_worker.outbox_publisher",
                "--interval-seconds",
                "0",
                "--max-cycles",
                "1",
            ]
        )
        == 2
    )
    assert "invalid_worker_supervisor_configuration" in capsys.readouterr().out
