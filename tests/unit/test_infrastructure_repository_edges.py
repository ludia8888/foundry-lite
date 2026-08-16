from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from foundry_lite.application.dependencies import RuntimeProfile
from foundry_lite.application.ports.action_repository import ActionRunRecord
from foundry_lite.application.services.object_store.query_cursor import (
    CURSOR_SIGNING_KEY_ENV,
    CURSOR_SIGNING_KEY_ID_ENV,
)
from foundry_lite.application.services.runtime_run_cursors import (
    OPERATIONS_CURSOR_SIGNING_KEY_ENV,
    OPERATIONS_CURSOR_SIGNING_KEY_ID_ENV,
)
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure.adapters.container_code_execution import (
    ContainerCodeExecutionAdapter,
)
from foundry_lite.infrastructure.local_runtime import (
    _ALLOW_LOCAL_PROMPT_ARTIFACT_KEY_ENV,
    RuntimeAdapterProfiles,
    _allow_local_prompt_artifact_key_from_env,
    _compute_adapter,
    _connector_adapter,
    _create_core_dependencies,
    _dataset_storage_adapter,
    _search_adapter,
    _stream_adapter,
    _workflow_adapter,
)
from foundry_lite.infrastructure.repositories import SchemaMutationDisabledError
from foundry_lite.infrastructure.repositories.action_repository import SqlAlchemyActionRepository
from foundry_lite.infrastructure.repositories.dataset_transaction_repository import _webhook_event_matches
from foundry_lite.infrastructure.repositories.object_change_sequence import next_object_change_sequence
from foundry_lite.infrastructure.secrets.env import EnvSecretProvider


@dataclass
class _FakeResult:
    scalar: str | None = None
    row: tuple[int] | None = None

    def scalar_one_or_none(self) -> str | None:
        return self.scalar

    def first(self) -> tuple[int] | None:
        return self.row


class _FakeTransaction:
    def __init__(self, dialect_name: str, result: _FakeResult | None = None) -> None:
        self.dialect = SimpleNamespace(name=dialect_name)
        self.result = result or _FakeResult()

    def execute(self, statement: object) -> _FakeResult:
        del statement
        return self.result


def test_runtime_adapter_factories_fail_closed_for_unknown_profiles(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown adapter profile"):
        _dataset_storage_adapter("typo-profile", tmp_path)
    with pytest.raises(ValueError, match="unknown compute profile"):
        _compute_adapter("typo-profile", code_execution_adapter=ContainerCodeExecutionAdapter())
    with pytest.raises(ValueError, match="unknown connector profile"):
        _connector_adapter("typo-profile", EnvSecretProvider())
    with pytest.raises(ValueError, match="unknown adapter profile"):
        _stream_adapter("typo-profile")
    with pytest.raises(ValueError, match="unknown workflow profile"):
        _workflow_adapter("typo-profile")


def test_runtime_search_adapter_selects_elasticsearch_and_rejects_unknown_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_SEARCH_PROFILE", "elasticsearch")
    adapter = _search_adapter("local")
    assert adapter.profile_name == "elasticsearch"

    monkeypatch.setenv("FOUNDRY_LITE_SEARCH_PROFILE", "missing-search")
    with pytest.raises(ValueError, match="unknown search adapter profile"):
        _search_adapter("local")


def test_runtime_workflow_adapter_selects_temporal_and_rejects_unknown_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_WORKFLOW_PROFILE", "temporal")
    adapter = _workflow_adapter("local")
    assert adapter.profile_name == "temporal"

    monkeypatch.setenv("FOUNDRY_LITE_WORKFLOW_PROFILE", "missing-workflow")
    with pytest.raises(ValueError, match="unknown workflow profile"):
        _workflow_adapter("local")


def test_prompt_artifact_local_dev_key_requires_explicit_env_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ALLOW_LOCAL_PROMPT_ARTIFACT_KEY_ENV, raising=False)
    assert not _allow_local_prompt_artifact_key_from_env()

    monkeypatch.setenv(_ALLOW_LOCAL_PROMPT_ARTIFACT_KEY_ENV, "true")
    assert _allow_local_prompt_artifact_key_from_env()


def _set_protected_adapter_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_ACTION_FILE_SCANNER_PROFILE", "clamav")
    monkeypatch.setenv("FOUNDRY_LITE_MEDIA_STORAGE_PROFILE", "s3-media")
    monkeypatch.setenv("FOUNDRY_LITE_CONTENT_INDEX_PROFILE", "elasticsearch")
    monkeypatch.setenv("FOUNDRY_LITE_COMPUTE_PROFILE", "spark")
    monkeypatch.setenv("FOUNDRY_LITE_CONNECTOR_PROFILE", "rest")
    monkeypatch.setenv("FOUNDRY_LITE_SEARCH_PROFILE", "elasticsearch")
    monkeypatch.setenv("FOUNDRY_LITE_STREAM_PROFILE", "kafka")
    monkeypatch.setenv("FOUNDRY_LITE_WORKFLOW_PROFILE", "temporal")
    monkeypatch.setenv("FOUNDRY_LITE_LANGUAGE_MODEL_PROFILE", "anthropic")
    monkeypatch.setenv("FOUNDRY_LITE_S3_BUCKET", "foundry-lite-test")
    monkeypatch.setenv(
        "FOUNDRY_LITE_CODE_EXECUTION_IMAGE",
        f"registry.example/foundry-python@sha256:{'a' * 64}",
    )
    monkeypatch.setenv(
        "FOUNDRY_LITE_TRAINED_MODEL_IMAGE",
        f"registry.example/foundry-model@sha256:{'b' * 64}",
    )


def _set_protected_governed_release_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_GITHUB_RELEASE_REPOSITORY_ID", "1234")
    monkeypatch.setenv("FOUNDRY_LITE_GITHUB_RELEASE_OWNER", "example")
    monkeypatch.setenv("FOUNDRY_LITE_GITHUB_RELEASE_REPOSITORY", "foundry-lite")
    monkeypatch.setenv("FOUNDRY_LITE_GITHUB_RELEASE_TOKEN_SECRET_REF", "github-token")
    monkeypatch.setenv("FOUNDRY_LITE_SECRET_GITHUB_TOKEN", "test-github-token")
    monkeypatch.setenv("FOUNDRY_LITE_RENDER_RELEASE_SERVICE_ID", "srv-example-service")
    monkeypatch.setenv("FOUNDRY_LITE_RENDER_RELEASE_TOKEN_SECRET_REF", "render-token")
    monkeypatch.setenv("FOUNDRY_LITE_SECRET_RENDER_TOKEN", "test-render-token")


def _create_protected_dependencies_for_cursor_test(tmp_path: Path) -> None:
    _create_core_dependencies(
        runtime_profile=RuntimeProfile.from_value("production"),
        db_url="sqlite:///:memory:",
        storage_root=tmp_path,
        profiles=RuntimeAdapterProfiles.from_env("local", {}),
    )


def test_protected_runtime_profile_disables_create_all_schema_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_RUNTIME_PROFILE", "production")
    monkeypatch.setenv(CURSOR_SIGNING_KEY_ENV, "production-cursor-secret")
    monkeypatch.setenv(CURSOR_SIGNING_KEY_ID_ENV, "prod-key-2026-06")
    monkeypatch.setenv(OPERATIONS_CURSOR_SIGNING_KEY_ENV, "production-operations-cursor-secret")
    monkeypatch.setenv(OPERATIONS_CURSOR_SIGNING_KEY_ID_ENV, "prod-operations-key-2026-06")
    monkeypatch.setenv(
        "FOUNDRY_LITE_CODE_EXECUTION_IMAGE",
        f"registry.example/foundry-python@sha256:{'a' * 64}",
    )
    monkeypatch.setenv(
        "FOUNDRY_LITE_TRAINED_MODEL_IMAGE",
        f"registry.example/foundry-model@sha256:{'b' * 64}",
    )
    _set_protected_governed_release_targets(monkeypatch)
    dependencies = _create_core_dependencies(
        runtime_profile=RuntimeProfile.from_value("production"),
        db_url="sqlite:///:memory:",
        storage_root=tmp_path / "prod",
        profiles=RuntimeAdapterProfiles.from_env("local", {}),
    )

    with pytest.raises(SchemaMutationDisabledError, match="run Alembic migrations"):
        dependencies.metadata_repository.initialize_schema()
    with pytest.raises(SchemaMutationDisabledError, match="run Alembic migrations"):
        dependencies.destructive_development_admin.reset_schema()
    assert not hasattr(dependencies.metadata_repository, "reset_schema")


def test_protected_runtime_profile_requires_object_query_cursor_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_RUNTIME_PROFILE", "production")
    monkeypatch.delenv(CURSOR_SIGNING_KEY_ENV, raising=False)

    with pytest.raises(ValidationFailed, match="cursor signing key"):
        _create_protected_dependencies_for_cursor_test(tmp_path / "missing-cursor-secret")


def test_protected_runtime_profile_requires_object_query_cursor_key_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_RUNTIME_PROFILE", "production")
    monkeypatch.setenv(CURSOR_SIGNING_KEY_ENV, "production-cursor-secret")
    monkeypatch.delenv(CURSOR_SIGNING_KEY_ID_ENV, raising=False)

    with pytest.raises(ValidationFailed, match="cursor signing key id"):
        _create_protected_dependencies_for_cursor_test(tmp_path / "missing-cursor-key-id")


def test_protected_runtime_profile_requires_operations_cursor_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_RUNTIME_PROFILE", "production")
    monkeypatch.setenv(CURSOR_SIGNING_KEY_ENV, "production-cursor-secret")
    monkeypatch.setenv(CURSOR_SIGNING_KEY_ID_ENV, "prod-key-2026-06")
    monkeypatch.setenv(OPERATIONS_CURSOR_SIGNING_KEY_ID_ENV, "prod-operations-key-2026-06")
    monkeypatch.delenv(OPERATIONS_CURSOR_SIGNING_KEY_ENV, raising=False)

    with pytest.raises(ValidationFailed, match="operations cursor signing key"):
        _create_protected_dependencies_for_cursor_test(tmp_path / "missing-operations-cursor-secret")


def test_object_change_sequence_rejects_unknown_dialect() -> None:
    with pytest.raises(RuntimeError, match="unsupported object change sequence dialect"):
        next_object_change_sequence(_FakeTransaction("unknown"), "tenant-demo")


def test_object_change_sequence_requires_returned_counter_row() -> None:
    transaction = _FakeTransaction("sqlite", _FakeResult(row=None))

    with pytest.raises(RuntimeError, match="allocation returned no row"):
        next_object_change_sequence(transaction, "tenant-demo")


def test_action_idempotency_insert_requires_persisted_winner(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = SqlAlchemyActionRepository(None)  # type: ignore[arg-type]
    monkeypatch.setattr(repository, "action_run_by_idempotency", lambda **_: None)
    transaction = _FakeTransaction("generic", _FakeResult(scalar="different-run"))

    with pytest.raises(RuntimeError, match="no persisted winner"):
        repository.insert_action_run_or_get_existing(transaction=transaction, record=_action_run_record())


def test_webhook_event_matcher_rejects_malformed_metadata() -> None:
    assert _webhook_event_matches(None, "mock_saas", "orders", "evt-1") is False
    assert _webhook_event_matches({"webhookEvent": "evt-1"}, "mock_saas", "orders", "evt-1") is False


def _action_run_record() -> ActionRunRecord:
    return ActionRunRecord(
        action_run_id="arun_loser",
        tenant_id="tenant-demo",
        action_type_id="atype_approve",
        action_type_api_name="approveOrder",
        actor_user_id="actor-demo",
        target_object_type_id="ot_order",
        target_object_type_api_name="Order",
        target_object_id="O-1",
        expected_object_version=1,
        parameters={"status": "APPROVED"},
        status="received",
        idempotency_key="idem-1",
        request_fingerprint="fingerprint-1",
        result=None,
        error=None,
        created_at="2026-06-10T00:00:00Z",
        completed_at=None,
    )
