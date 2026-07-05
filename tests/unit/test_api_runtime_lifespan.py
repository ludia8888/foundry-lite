from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient
from foundry_lite_api import main, runtime


def test_runtime_import_stays_uninitialized() -> None:
    runtime.reset_api_runtime_for_tests()

    assert not runtime.is_api_runtime_initialized()


def test_initialize_api_runtime_builds_once(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    runtime.reset_api_runtime_for_tests()
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


def test_fastapi_lifespan_initializes_and_instruments(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    runtime.reset_api_runtime_for_tests()
    fake_engine = object()
    initialized = SimpleNamespace(foundry=SimpleNamespace(engine=fake_engine), auth_provider=object())
    engines: list[object] = []

    monkeypatch.setattr(main.runtime, "initialize_api_runtime", lambda: initialized)
    monkeypatch.setattr(main, "instrument_sqlalchemy_engine", engines.append)

    with TestClient(main.app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}

    assert engines == [fake_engine]
