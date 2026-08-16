"""Explicit ownership and shutdown contracts for the Foundry runtime root."""

from __future__ import annotations

from dataclasses import replace

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.infrastructure.adapters import LocalStreamAdapter
from foundry_lite.infrastructure.adapters.action_run_orchestrator import LocalActionRunOrchestrator
from foundry_lite.infrastructure.adapters.pipeline_dag_orchestrator import LocalPipelineDagOrchestrator
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies


class _TrackedStream(LocalStreamAdapter):
    def __init__(self, events: list[str], *, should_fail: bool = False) -> None:
        super().__init__()
        self._events = events
        self._should_fail = should_fail

    def close(self) -> None:
        self._events.append("stream")
        if self._should_fail:
            raise RuntimeError("stream close failed")


class _TrackedEngine:
    def __init__(self, events: list[str], *, failures: int = 0) -> None:
        self._events = events
        self._failures = failures

    def dispose(self) -> None:
        self._events.append("engine")
        if self._failures > 0:
            self._failures -= 1
            raise RuntimeError("engine close failed")


class _TrackedPipelineOrchestrator(LocalPipelineDagOrchestrator):
    def __init__(self, events: list[str], *, failures: int = 0) -> None:
        super().__init__()
        self._events = events
        self._failures = failures

    def close(self) -> None:
        self._events.append("pipeline")
        if self._failures > 0:
            self._failures -= 1
            raise RuntimeError("pipeline close failed")
        super().close()


class _TrackedActionOrchestrator(LocalActionRunOrchestrator):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self._events = events

    def close(self) -> None:
        self._events.append("action")
        super().close()


def _foundry_with_tracked_stream(  # type: ignore[no-untyped-def]
    tmp_path,
    events: list[str],
    *,
    should_fail: bool = False,
    is_stream_adapter_owned: bool = True,
    is_engine_owned: bool = True,
):
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "runtime")
    stream = _TrackedStream(events, should_fail=should_fail)
    foundry = FoundryLite(
        dependencies=replace(dependencies, stream_adapter=stream),
        is_stream_adapter_owned=is_stream_adapter_owned,
        is_engine_owned=is_engine_owned,
    )
    foundry.engine = _TrackedEngine(events)  # type: ignore[assignment]
    return foundry


def test_foundry_close_releases_stream_before_database_engine(tmp_path) -> None:  # type: ignore[no-untyped-def]
    events: list[str] = []
    foundry = _foundry_with_tracked_stream(tmp_path, events)

    foundry.close()
    foundry.close()

    assert events == ["stream", "engine"]


def test_foundry_close_stops_local_orchestrators_before_stream_and_database(tmp_path) -> None:  # type: ignore[no-untyped-def]
    events: list[str] = []
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "runtime")
    dependencies = replace(
        dependencies,
        pipeline_dag_orchestrator=_TrackedPipelineOrchestrator(events),
        action_run_orchestrator=_TrackedActionOrchestrator(events),
        stream_adapter=_TrackedStream(events),
        engine=_TrackedEngine(events),  # type: ignore[arg-type]
    )
    foundry = FoundryLite(dependencies=dependencies)

    foundry.close()
    foundry.close()

    assert events == ["pipeline", "action", "stream", "engine"]


def test_foundry_close_defers_stream_and_database_until_orchestrator_stops(tmp_path) -> None:  # type: ignore[no-untyped-def]
    events: list[str] = []
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "runtime")
    dependencies = replace(
        dependencies,
        pipeline_dag_orchestrator=_TrackedPipelineOrchestrator(events, failures=1),
        action_run_orchestrator=_TrackedActionOrchestrator(events),
        stream_adapter=_TrackedStream(events),
        engine=_TrackedEngine(events),  # type: ignore[arg-type]
    )
    foundry = FoundryLite(dependencies=dependencies)

    with pytest.raises(RuntimeError, match="pipeline close failed"):
        foundry.close()
    assert events == ["pipeline", "action"]

    foundry.close()
    assert events == ["pipeline", "action", "pipeline", "stream", "engine"]


def test_foundry_close_disposes_database_even_when_stream_shutdown_fails(tmp_path) -> None:  # type: ignore[no-untyped-def]
    events: list[str] = []
    foundry = _foundry_with_tracked_stream(tmp_path, events, should_fail=True)

    with pytest.raises(RuntimeError, match="stream close failed"):
        foundry.close()

    assert events == ["stream", "engine"]


def test_foundry_close_can_preserve_caller_owned_stream_while_disposing_engine(tmp_path) -> None:  # type: ignore[no-untyped-def]
    events: list[str] = []
    foundry = _foundry_with_tracked_stream(tmp_path, events)

    foundry.close(should_close_stream=False)
    foundry.close()

    assert events == ["engine", "stream"]


def test_foundry_close_preserves_declared_caller_owned_stream_by_default(tmp_path) -> None:  # type: ignore[no-untyped-def]
    events: list[str] = []
    foundry = _foundry_with_tracked_stream(tmp_path, events, is_stream_adapter_owned=False)

    foundry.close()

    assert events == ["engine"]


def test_foundry_close_preserves_declared_caller_owned_engine(tmp_path) -> None:  # type: ignore[no-untyped-def]
    events: list[str] = []
    foundry = _foundry_with_tracked_stream(tmp_path, events, is_engine_owned=False)

    foundry.close()

    assert events == ["stream"]


def test_foundry_initialization_failure_releases_stream_and_database(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    events: list[str] = []
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "runtime")
    dependencies = replace(
        dependencies,
        stream_adapter=_TrackedStream(events),
        engine=_TrackedEngine(events),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(FoundryLite, "bootstrap", lambda _self: (_ for _ in ()).throw(ValueError("bootstrap failed")))

    with pytest.raises(ValueError, match="bootstrap failed"):
        FoundryLite(dependencies=dependencies)

    assert events == ["stream", "engine"]


def test_foundry_initialization_retains_primary_error_when_cleanup_fails(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    events: list[str] = []
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "runtime")
    dependencies = replace(
        dependencies,
        stream_adapter=_TrackedStream(events, should_fail=True),
        engine=_TrackedEngine(events),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(FoundryLite, "bootstrap", lambda _self: (_ for _ in ()).throw(ValueError("bootstrap failed")))

    with pytest.raises(ValueError, match="bootstrap failed") as caught:
        FoundryLite(dependencies=dependencies)

    assert caught.value.__notes__ == ["stream cleanup also failed (RuntimeError)"]
    assert events == ["stream", "engine"]


def test_foundry_initialization_failure_still_closes_resources_after_orchestrator_error(
    monkeypatch,
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    events: list[str] = []
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "runtime")
    dependencies = replace(
        dependencies,
        pipeline_dag_orchestrator=_TrackedPipelineOrchestrator(events, failures=1),
        action_run_orchestrator=_TrackedActionOrchestrator(events),
        stream_adapter=_TrackedStream(events),
        engine=_TrackedEngine(events),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(FoundryLite, "bootstrap", lambda _self: (_ for _ in ()).throw(ValueError("bootstrap failed")))

    with pytest.raises(ValueError, match="bootstrap failed") as caught:
        FoundryLite(dependencies=dependencies)

    assert caught.value.__notes__ == ["pipeline orchestrator cleanup also failed (RuntimeError)"]
    assert events == ["pipeline", "action", "stream", "engine"]


def test_foundry_initialization_failure_preserves_caller_owned_stream(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    events: list[str] = []
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "runtime")
    dependencies = replace(
        dependencies,
        stream_adapter=_TrackedStream(events),
        engine=_TrackedEngine(events),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(FoundryLite, "bootstrap", lambda _self: (_ for _ in ()).throw(ValueError("bootstrap failed")))

    with pytest.raises(ValueError, match="bootstrap failed"):
        FoundryLite(dependencies=dependencies, is_stream_adapter_owned=False)

    assert events == ["engine"]


def test_foundry_initialization_failure_preserves_caller_owned_engine(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    events: list[str] = []
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "runtime")
    dependencies = replace(
        dependencies,
        stream_adapter=_TrackedStream(events),
        engine=_TrackedEngine(events),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(FoundryLite, "bootstrap", lambda _self: (_ for _ in ()).throw(ValueError("bootstrap failed")))

    with pytest.raises(ValueError, match="bootstrap failed"):
        FoundryLite(dependencies=dependencies, is_engine_owned=False)

    assert events == ["stream"]


def test_foundry_close_retries_only_resources_that_failed(tmp_path) -> None:  # type: ignore[no-untyped-def]
    events: list[str] = []
    foundry = _foundry_with_tracked_stream(tmp_path, events)
    foundry.engine = _TrackedEngine(events, failures=1)  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="engine close failed"):
        foundry.close()
    foundry.close()

    assert events == ["stream", "engine", "engine"]


def test_foundry_close_retains_stream_error_when_engine_cleanup_also_fails(tmp_path) -> None:  # type: ignore[no-untyped-def]
    events: list[str] = []
    foundry = _foundry_with_tracked_stream(tmp_path, events, should_fail=True)
    foundry.engine = _TrackedEngine(events, failures=1)  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="stream close failed") as caught:
        foundry.close()

    assert caught.value.__notes__ == ["database cleanup also failed (RuntimeError)"]
    assert events == ["stream", "engine"]


def test_foundry_context_preserves_workload_error_when_cleanup_also_fails(tmp_path) -> None:  # type: ignore[no-untyped-def]
    events: list[str] = []
    foundry = _foundry_with_tracked_stream(tmp_path, events, should_fail=True)

    with pytest.raises(ValueError, match="workload failed") as caught:
        with foundry:
            raise ValueError("workload failed")

    assert caught.value.__notes__ == ["runtime cleanup also failed (RuntimeError)"]
    assert events == ["stream", "engine"]


def test_foundry_context_surfaces_cleanup_error_without_workload_error(tmp_path) -> None:  # type: ignore[no-untyped-def]
    events: list[str] = []
    foundry = _foundry_with_tracked_stream(tmp_path, events, should_fail=True)

    with pytest.raises(RuntimeError, match="stream close failed"):
        with foundry:
            pass


def test_foundry_finally_close_preserves_active_workload_error(tmp_path) -> None:  # type: ignore[no-untyped-def]
    events: list[str] = []
    foundry = _foundry_with_tracked_stream(tmp_path, events, should_fail=True)

    with pytest.raises(ValueError, match="workload failed") as caught:
        try:
            raise ValueError("workload failed")
        finally:
            foundry.close()

    assert caught.value.__notes__ == ["runtime cleanup also failed (RuntimeError)"]


def test_foundry_close_with_primary_error_preserves_primary_failure(tmp_path) -> None:  # type: ignore[no-untyped-def]
    events: list[str] = []
    foundry = _foundry_with_tracked_stream(tmp_path, events, should_fail=True)
    primary = ValueError("configuration failed")

    foundry.close(primary_error=primary)

    assert primary.__notes__ == ["runtime cleanup also failed (RuntimeError)"]
