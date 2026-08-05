"""Durable after-commit Action effect worker loop.

This worker had no test at all. Its job is to keep delivering effects while the process is
alive, which makes two behaviours load-bearing and easy to get wrong: a provider outage in one
tick must not end the loop, and the env-driven lease/concurrency knobs must stay inside bounds
an operator cannot exceed by typo. Both are asserted here rather than left to a live run.
"""

from __future__ import annotations

import signal
from threading import Event
from typing import Any

import pytest
from foundry_lite_worker import action_effects


class _RecordingEffects:
    def __init__(self, failures: int = 0) -> None:
        self.calls: list[dict[str, Any]] = []
        self.failures = failures

    def deliver_all(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        if len(self.calls) <= self.failures:
            raise RuntimeError("provider outage")


class _StubFoundry:
    def __init__(self, effects: _RecordingEffects) -> None:
        class _Services:
            action_effects = effects

        self._services = _Services()


class _StopAfter(Event):
    """Stop event that reports "keep going" for a fixed number of checks."""

    def __init__(self, ticks: int) -> None:
        super().__init__()
        self._remaining = ticks

    def is_set(self) -> bool:
        if self._remaining <= 0:
            return True
        self._remaining -= 1
        return False

    def wait(self, timeout: float | None = None) -> bool:  # type: ignore[override]
        return False


@pytest.fixture
def effects(monkeypatch: pytest.MonkeyPatch) -> _RecordingEffects:
    recorded = _RecordingEffects()
    monkeypatch.setattr(action_effects, "create_runtime_core_dependencies", lambda **_: object())
    monkeypatch.setattr(action_effects, "FoundryLite", lambda **_: _StubFoundry(recorded))
    for name in (
        "FOUNDRY_LITE_ACTION_EFFECT_INTERVAL_SECONDS",
        "FOUNDRY_LITE_ACTION_EFFECT_LEASE_SECONDS",
        "FOUNDRY_LITE_ACTION_EFFECT_CONCURRENCY",
        "FOUNDRY_LITE_WORKER_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    return recorded


def test_loop_exits_immediately_when_stop_is_already_set(effects: _RecordingEffects) -> None:
    stop = Event()
    stop.set()

    action_effects.run_effect_loop(stop)

    assert effects.calls == [], "a stopped worker must not deliver a tick"


def test_each_tick_delivers_with_the_worker_identity_and_bounded_batch(effects: _RecordingEffects) -> None:
    action_effects.run_effect_loop(_StopAfter(2))

    assert len(effects.calls) == 2
    assert effects.calls[0]["worker_id"] == "action-effects"
    assert effects.calls[0]["limit"] == 100


def test_a_provider_outage_in_one_tick_does_not_end_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A durable worker that dies on the first provider error stops delivering everything else."""
    recorded = _RecordingEffects(failures=1)
    reported: list[str] = []
    monkeypatch.setattr(action_effects, "create_runtime_core_dependencies", lambda **_: object())
    monkeypatch.setattr(action_effects, "FoundryLite", lambda **_: _StubFoundry(recorded))
    # Assert on the logger call rather than captured output: the worker's reporting contract is
    # that it logs the failure, and that must hold regardless of how the run configures handlers.
    monkeypatch.setattr(action_effects._LOGGER, "exception", lambda message, *args: reported.append(message % args))

    action_effects.run_effect_loop(_StopAfter(3))

    assert len(recorded.calls) == 3, "the loop must keep ticking after a failed delivery"
    assert reported and "action.effects.tick_failed" in reported[0], "a swallowed error must still be reported"


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [("0", 1), ("1", 1), ("30", 30), ("99999", 3600)],
)
def test_lease_seconds_is_clamped_to_a_usable_range(
    effects: _RecordingEffects, monkeypatch: pytest.MonkeyPatch, env_value: str, expected: int
) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_ACTION_EFFECT_LEASE_SECONDS", env_value)

    action_effects.run_effect_loop(_StopAfter(1))

    assert effects.calls[0]["lease_seconds"] == expected


@pytest.mark.parametrize(("env_value", "expected"), [("0", 1), ("4", 4), ("1000", 32)])
def test_concurrency_is_clamped_to_a_usable_range(
    effects: _RecordingEffects, monkeypatch: pytest.MonkeyPatch, env_value: str, expected: int
) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_ACTION_EFFECT_CONCURRENCY", env_value)

    action_effects.run_effect_loop(_StopAfter(1))

    assert effects.calls[0]["concurrency"] == expected


def test_worker_id_comes_from_the_environment(effects: _RecordingEffects, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_WORKER_ID", "effects-pod-3")

    action_effects.run_effect_loop(_StopAfter(1))

    assert effects.calls[0]["worker_id"] == "effects-pod-3"


def test_interval_never_drops_below_one_second(monkeypatch: pytest.MonkeyPatch) -> None:
    """A zero interval would turn the durable worker into a busy loop against the database."""
    waits: list[float | None] = []

    class _RecordingStop(Event):
        def __init__(self) -> None:
            super().__init__()
            self._seen = 0

        def is_set(self) -> bool:
            self._seen += 1
            return self._seen > 1

        def wait(self, timeout: float | None = None) -> bool:  # type: ignore[override]
            waits.append(timeout)
            return False

    monkeypatch.setenv("FOUNDRY_LITE_ACTION_EFFECT_INTERVAL_SECONDS", "0")
    monkeypatch.setattr(action_effects, "create_runtime_core_dependencies", lambda **_: object())
    monkeypatch.setattr(action_effects, "FoundryLite", lambda **_: _StubFoundry(_RecordingEffects()))

    action_effects.run_effect_loop(_RecordingStop())

    assert waits == [1.0]


def test_signal_handlers_request_a_graceful_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    installed: dict[int, object] = {}
    monkeypatch.setattr(signal, "signal", lambda num, handler: installed.__setitem__(num, handler))
    stop = Event()

    action_effects._install_signal_handlers(stop)

    assert set(installed) == {signal.SIGTERM, signal.SIGINT}
    installed[signal.SIGTERM](signal.SIGTERM, None)  # type: ignore[operator]
    assert stop.is_set(), "SIGTERM must ask the loop to finish, not kill it mid-delivery"


def test_main_installs_handlers_before_entering_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    monkeypatch.setattr(action_effects, "_install_signal_handlers", lambda _stop: order.append("handlers"))
    monkeypatch.setattr(action_effects, "run_effect_loop", lambda _stop: order.append("loop"))

    action_effects.main()

    assert order == ["handlers", "loop"]
