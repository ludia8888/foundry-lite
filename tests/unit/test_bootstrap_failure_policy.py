from __future__ import annotations

import pytest
from foundry_lite.application import foundry as foundry_module
from foundry_lite.application.dependencies import RuntimeProfile
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import DEFAULT_TENANT_ID
from foundry_lite.security.tenant_context import current_tenant_id, tenant_context


class _MetadataRepository:
    def initialize_schema(self) -> None:
        pass

    def list_tenant_ids(self) -> list[str]:
        return []

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


def test_protected_bootstrap_binds_and_restores_default_tenant_context(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: list[str | None] = []
    foundry = _foundry_with_profile("production")

    class _TenantAwareMetadataRepository(_MetadataRepository):
        def ensure_tenant(self, **_kwargs: object) -> None:
            observed.append(current_tenant_id())

        def ensure_user(self, **_kwargs: object) -> None:
            observed.append(current_tenant_id())

    def record_model_seed(self: FoundryLite, now: str) -> None:
        del self, now
        observed.append(current_tenant_id())

    foundry.metadata_repository = _TenantAwareMetadataRepository()
    monkeypatch.setattr(FoundryLite, "_ensure_demo_model_registry", record_model_seed)

    with tenant_context("tenant-outer"):
        foundry.bootstrap()
        assert current_tenant_id() == "tenant-outer"

    assert observed == [DEFAULT_TENANT_ID, DEFAULT_TENANT_ID, DEFAULT_TENANT_ID]
    assert current_tenant_id() is None


class _RecordingMetadataRepository(_MetadataRepository):
    """Metadata repository that records schema initialization attempts."""

    def __init__(self) -> None:
        self.initialize_schema_calls = 0

    def initialize_schema(self) -> None:
        self.initialize_schema_calls += 1


def test_protected_profile_skips_schema_initialization() -> None:
    """Protected runtimes take their schema from Alembic, not from create_all.

    ``SqlAlchemyMetadataRepository`` is built with ``allow_schema_mutation=False``
    whenever the runtime profile is protected, so ``initialize_schema()`` raises
    ``SchemaMutationDisabledError`` there. Calling it unconditionally in
    ``FoundryLite.__init__`` meant every production/staging API, CLI, and worker
    process raised while constructing ``FoundryLite`` — before ``bootstrap()``
    ever ran. Local profiles must still self-initialize so dev/test keep working.
    """
    for profile in ("production", "prod", "staging", "stage"):
        foundry = _foundry_with_profile(profile)
        repository = _RecordingMetadataRepository()
        foundry.metadata_repository = repository
        foundry._initialize_schema_for_unprotected_profile()
        assert repository.initialize_schema_calls == 0, profile

    for profile in ("local", "dev", "development", "demo", "test"):
        foundry = _foundry_with_profile(profile)
        repository = _RecordingMetadataRepository()
        foundry.metadata_repository = repository
        foundry._initialize_schema_for_unprotected_profile()
        assert repository.initialize_schema_calls == 1, profile
