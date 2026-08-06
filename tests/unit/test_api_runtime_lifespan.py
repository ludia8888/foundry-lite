from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from foundry_lite_api import main, runtime


@pytest.fixture(autouse=True)
def _reset_runtime_state():
    runtime.reset_api_runtime_for_tests()
    yield
    runtime.reset_api_runtime_for_tests()


def test_runtime_import_stays_uninitialized() -> None:
    assert not runtime.is_api_runtime_initialized()


def test_initialize_api_runtime_builds_once(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[object] = []

    class _Foundry:
        engine = object()

        def __init__(self, *, dependencies: object) -> None:
            calls.append(dependencies)

    monkeypatch.setattr(runtime, "FoundryLite", _Foundry)
    monkeypatch.setattr(runtime, "create_runtime_core_dependencies", lambda **kwargs: SimpleNamespace(kwargs=kwargs))
    monkeypatch.setattr(runtime, "auth_provider_from_env", lambda source=None: SimpleNamespace(name="auth"))

    first = runtime.initialize_api_runtime({})
    second = runtime.initialize_api_runtime({})

    assert first is second
    assert len(calls) == 1


def test_reset_disposes_the_engine_before_clearing_the_runtime(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Dropping the runtime without disposing the engine leaks its connection pool.

    Nothing asserted this. Every test that reset a runtime built one whose engine was a bare
    ``object()``, which has no ``dispose``, so the disposing branch was only ever reached when
    an unrelated test left a real engine behind in the process-global singleton. That made the
    line look covered in a single-process run and uncovered the moment the suite was sharded --
    the coverage was a test-isolation leak, not a test.
    """
    disposals: list[str] = []

    class _Engine:
        @staticmethod
        def dispose() -> None:
            disposals.append("engine")

    class _Foundry:
        engine = _Engine()

        def __init__(self, *, dependencies: object) -> None:
            del dependencies

    monkeypatch.setattr(runtime, "FoundryLite", _Foundry)
    monkeypatch.setattr(runtime, "create_runtime_core_dependencies", lambda **kwargs: SimpleNamespace(kwargs=kwargs))
    monkeypatch.setattr(runtime, "auth_provider_from_env", lambda source=None: SimpleNamespace(name="auth"))

    runtime.initialize_api_runtime({})
    assert runtime.is_api_runtime_initialized()

    runtime.reset_api_runtime_for_tests()

    assert disposals == ["engine"]
    assert not runtime.is_api_runtime_initialized()


def test_fastapi_lifespan_initializes_and_instruments(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    fake_engine = object()
    pipelines = SimpleNamespace(recover_preview_runs=lambda: {"processed": 0})
    initialized = SimpleNamespace(
        foundry=SimpleNamespace(engine=fake_engine, pipelines=pipelines),
        auth_provider=object(),
    )
    engines: list[object] = []

    monkeypatch.setattr(main.runtime, "initialize_api_runtime", lambda: initialized)
    monkeypatch.setattr(main, "instrument_sqlalchemy_engine", engines.append)

    with TestClient(main.app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}

    assert engines == [fake_engine]


def test_preview_recovery_loop_dispatches_durable_work(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []
    foundry = SimpleNamespace(
        pipelines=SimpleNamespace(recover_preview_runs=lambda: calls.append("recover")),
    )

    async def _to_thread(function):  # type: ignore[no-untyped-def]
        return function()

    async def _stop_after_first_scan(_seconds):  # type: ignore[no-untyped-def]
        raise asyncio.CancelledError

    monkeypatch.setattr(main.asyncio, "to_thread", _to_thread)
    monkeypatch.setattr(main.asyncio, "sleep", _stop_after_first_scan)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main._recover_pipeline_previews(foundry))

    assert calls == ["recover"]


def test_preview_recovery_loop_logs_failure_and_keeps_scanning(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    events: list[tuple[str, dict[str, object]]] = []

    async def _failed_scan(_function):  # type: ignore[no-untyped-def]
        raise RuntimeError("database temporarily unavailable")

    async def _stop_after_failure(_seconds):  # type: ignore[no-untyped-def]
        raise asyncio.CancelledError

    def _capture_event(_logger, event, **fields):  # type: ignore[no-untyped-def]
        events.append((event, fields))

    monkeypatch.setattr(main.asyncio, "to_thread", _failed_scan)
    monkeypatch.setattr(main.asyncio, "sleep", _stop_after_failure)
    monkeypatch.setattr(main, "log_event", _capture_event)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            main._recover_pipeline_previews(
                SimpleNamespace(pipelines=SimpleNamespace(recover_preview_runs=lambda: None))
            )
        )

    assert events == [
        (
            "pipeline.preview.recovery_failed",
            {
                "request_id": "req-pipeline-preview-recovery",
                "tenant_id": "system",
                "error_type": "RuntimeError",
            },
        )
    ]


def test_preview_recovery_loop_propagates_task_cancellation(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def _cancelled_scan(_function):  # type: ignore[no-untyped-def]
        raise asyncio.CancelledError

    monkeypatch.setattr(main.asyncio, "to_thread", _cancelled_scan)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            main._recover_pipeline_previews(
                SimpleNamespace(pipelines=SimpleNamespace(recover_preview_runs=lambda: None))
            )
        )
