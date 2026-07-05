from __future__ import annotations

import pytest
from foundry_lite.application import foundry as foundry_module
from foundry_lite.application.dependencies import RuntimeProfile
from foundry_lite.application.foundry import FoundryLite


class _MetadataRepository:
    def initialize_schema(self) -> None:
        pass

    def ensure_tenant(self, **_kwargs: object) -> None:
        pass

    def ensure_user(self, **_kwargs: object) -> None:
        pass


def _foundry_with_profile(profile: str) -> FoundryLite:
    foundry = object.__new__(FoundryLite)
    foundry.metadata_repository = _MetadataRepository()
    foundry.runtime_profile = RuntimeProfile.from_value(profile)
    return foundry


def test_local_bootstrap_logs_model_registry_seed_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_seed(self: FoundryLite, now: str) -> None:
        raise RuntimeError(f"seed boom at {now}")

    warnings: list[tuple[str, dict[str, object]]] = []

    def capture_warning(message: str, *args: object, **kwargs: object) -> None:
        del args
        warnings.append((message, kwargs))

    monkeypatch.setattr(FoundryLite, "_ensure_demo_model_registry", fail_seed)
    monkeypatch.setattr(foundry_module._LOGGER, "warning", capture_warning)

    _foundry_with_profile("local").bootstrap()

    assert len(warnings) == 1
    message, kwargs = warnings[0]
    assert "Demo model registry seed failed" in message
    assert kwargs["exc_info"] is True
    assert kwargs["extra"] == {
        "request_id": "bootstrap",
        "runtime_profile": "local",
        "tenant_id": "tenant-demo",
    }


def test_protected_bootstrap_model_registry_failure_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_seed(self: FoundryLite, now: str) -> None:
        raise RuntimeError(f"seed boom at {now}")

    monkeypatch.setattr(FoundryLite, "_ensure_demo_model_registry", fail_seed)

    with pytest.raises(RuntimeError, match="seed boom"):
        _foundry_with_profile("production").bootstrap()
