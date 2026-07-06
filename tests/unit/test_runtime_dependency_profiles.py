from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from foundry_lite.application.dependencies import RuntimeProfile
from foundry_lite.infrastructure.adapters import KafkaStreamAdapter
from foundry_lite.infrastructure.local_runtime import (
    RuntimeAdapterProfiles,
    create_production_core_dependencies,
    create_runtime_core_dependencies,
)


def test_runtime_profile_normalizes_and_marks_protected() -> None:
    assert RuntimeProfile.from_value("prod").is_protected
    assert RuntimeProfile.from_value("demo").is_local_like
    with pytest.raises(ValueError, match="unknown FOUNDRY_LITE_RUNTIME_PROFILE"):
        RuntimeProfile.from_value("cluster")


def test_runtime_adapter_profiles_reads_stream_override() -> None:
    profiles = RuntimeAdapterProfiles.from_env("local", {"FOUNDRY_LITE_STREAM_PROFILE": "kafka"})

    assert profiles.stream == "kafka"
    assert profiles.dataset_storage == "local"


def test_kafka_stream_profile_uses_existing_kafka_adapter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_STREAM_PROFILE", "kafka")
    monkeypatch.setenv(
        "FOUNDRY_LITE_KAFKA_SUBSCRIPTIONS_JSON",
        '[{"streamName":"orders","topic":"orders"}]',
    )

    dependencies = create_runtime_core_dependencies(
        profile=RuntimeProfile.from_value("test"),
        db_url="sqlite:///:memory:",
        storage_root=tmp_path,
    )

    assert isinstance(dependencies.stream_adapter, KafkaStreamAdapter)


def test_flat_compute_adapter_override_preserves_pipeline_repository(tmp_path: Path) -> None:
    dependencies = create_runtime_core_dependencies(
        profile=RuntimeProfile.from_value("test"),
        db_url="sqlite:///:memory:",
        storage_root=tmp_path,
    )

    replaced = replace(dependencies, compute_adapter=dependencies.compute_adapter)

    assert replaced.pipeline_repository is dependencies.pipeline_repository


def test_production_dependencies_reject_local_profiles(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="production runtime requires production adapter profiles"):
        create_production_core_dependencies(
            profile=RuntimeProfile.from_value("production"),
            db_url="sqlite:///:memory:",
            storage_root=tmp_path,
        )
