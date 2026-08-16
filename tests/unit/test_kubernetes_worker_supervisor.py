from __future__ import annotations

from subprocess import CompletedProcess

import pytest
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
