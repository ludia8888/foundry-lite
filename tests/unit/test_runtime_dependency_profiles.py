from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from foundry_lite.application.dependencies import RuntimeProfile
from foundry_lite.infrastructure import local_runtime as runtime
from foundry_lite.infrastructure.adapters import (
    AnthropicLanguageModel,
    KafkaStreamAdapter,
)
from foundry_lite.infrastructure.adapters.container_trained_model_inference import (
    ContainerTrainedModelInferenceAdapter,
)
from foundry_lite.infrastructure.adapters.local_trained_model_inference import (
    LocalTrainedModelInferenceAdapter,
)
from foundry_lite.infrastructure.adapters.ocr_processor import OcrProcessorAdapter
from foundry_lite.infrastructure.local_runtime import (
    RuntimeAdapterProfiles,
    _trained_model_inference_adapter,
    create_production_core_dependencies,
    create_runtime_core_dependencies,
)


def test_runtime_profile_normalizes_and_marks_protected() -> None:
    assert RuntimeProfile.from_value("prod").is_protected
    assert RuntimeProfile.from_value("demo").is_local_like
    with pytest.raises(ValueError, match="unknown FOUNDRY_LITE_RUNTIME_PROFILE"):
        RuntimeProfile.from_value("cluster")


def test_trained_model_profile_uses_local_only_for_local_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FOUNDRY_LITE_TRAINED_MODEL_PROFILE", raising=False)
    assert isinstance(
        _trained_model_inference_adapter(RuntimeProfile.from_value("local")),
        LocalTrainedModelInferenceAdapter,
    )

    monkeypatch.setenv("FOUNDRY_LITE_TRAINED_MODEL_PROFILE", "container")
    assert isinstance(
        _trained_model_inference_adapter(RuntimeProfile.from_value("local")),
        ContainerTrainedModelInferenceAdapter,
    )


def test_protected_trained_model_profile_requires_digest_pinned_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_TRAINED_MODEL_PROFILE", "container")
    monkeypatch.setenv("FOUNDRY_LITE_TRAINED_MODEL_IMAGE", "registry.example/model:latest")
    with pytest.raises(ValueError, match="sha256 digest"):
        _trained_model_inference_adapter(RuntimeProfile.from_value("production"))

    monkeypatch.setenv(
        "FOUNDRY_LITE_TRAINED_MODEL_IMAGE",
        f"registry.example/model@sha256:{'a' * 64}",
    )
    assert isinstance(
        _trained_model_inference_adapter(RuntimeProfile.from_value("production")),
        ContainerTrainedModelInferenceAdapter,
    )


def test_runtime_adapter_profiles_reads_stream_override() -> None:
    profiles = RuntimeAdapterProfiles.from_env(
        "local",
        {
            "FOUNDRY_LITE_STREAM_PROFILE": "kafka",
            "FOUNDRY_LITE_LANGUAGE_MODEL_PROFILE": "anthropic",
        },
    )

    assert profiles.stream == "kafka"
    assert profiles.language_model == "anthropic"
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


def test_anthropic_language_model_profile_is_explicit_and_catalog_pinned(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_LANGUAGE_MODEL_PROFILE", "anthropic")
    monkeypatch.setenv("FOUNDRY_LITE_ANTHROPIC_MODEL", "claude-sonnet-5")

    dependencies = create_runtime_core_dependencies(
        profile=RuntimeProfile.from_value("test"),
        db_url="sqlite:///:memory:",
        storage_root=tmp_path,
    )

    assert isinstance(dependencies.language_model_adapter, AnthropicLanguageModel)
    assert dependencies.aip.model_catalog_seed is not None
    assert dependencies.aip.model_catalog_seed.provider_model_id == "claude-sonnet-5"
    assert dependencies.aip.model_catalog_seed.secret_ref == "anthropic_api_key"


def test_configured_media_processor_is_not_shadowed_by_default_registry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_MEDIA_PROCESSOR_PROFILE", "ocr-tesseract")

    dependencies = create_runtime_core_dependencies(
        profile=RuntimeProfile.from_value("test"),
        db_url="sqlite:///:memory:",
        storage_root=tmp_path,
    )

    assert isinstance(dependencies.media_processor, OcrProcessorAdapter)
    assert dependencies.media_processor_registry is None


def test_flat_compute_adapter_override_preserves_pipeline_repository(tmp_path: Path) -> None:
    dependencies = create_runtime_core_dependencies(
        profile=RuntimeProfile.from_value("test"),
        db_url="sqlite:///:memory:",
        storage_root=tmp_path,
    )

    replaced = replace(dependencies, compute_adapter=dependencies.compute_adapter)

    assert replaced.pipeline_repository is dependencies.pipeline_repository
    assert replaced.pipeline_execution_repository is dependencies.pipeline_execution_repository


def test_flat_media_registry_override_preserves_media_dependencies(tmp_path: Path) -> None:
    dependencies = create_runtime_core_dependencies(
        profile=RuntimeProfile.from_value("test"),
        db_url="sqlite:///:memory:",
        storage_root=tmp_path,
    )

    replaced = replace(dependencies, media_processor_registry=dependencies.media_processor_registry)

    assert replaced.media_processor_registry is dependencies.media_processor_registry
    assert replaced.media_repository is dependencies.media_repository


def test_flat_legacy_media_processor_override_clears_shadowing_registry(tmp_path: Path) -> None:
    dependencies = create_runtime_core_dependencies(
        profile=RuntimeProfile.from_value("test"),
        db_url="sqlite:///:memory:",
        storage_root=tmp_path,
    )

    replaced = replace(dependencies, media_processor=dependencies.media_processor)

    assert replaced.media_processor is dependencies.media_processor
    assert replaced.media_processor_registry is None


def test_flat_connector_override_preserves_source_stream_adapter(tmp_path: Path) -> None:
    dependencies = create_runtime_core_dependencies(
        profile=RuntimeProfile.from_value("test"),
        db_url="sqlite:///:memory:",
        storage_root=tmp_path,
    )

    replaced = replace(dependencies, connector_adapter=dependencies.connector_adapter)

    assert replaced.source_stream_adapter is dependencies.source_stream_adapter


def test_production_dependencies_reject_local_profiles(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="production runtime requires production adapter profiles"):
        create_production_core_dependencies(
            profile=RuntimeProfile.from_value("production"),
            db_url="sqlite:///:memory:",
            storage_root=tmp_path,
        )


def test_runtime_composition_entrypoints_reject_wrong_profile_direction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_RUNTIME_PROFILE", "production")
    with pytest.raises(ValueError, match="limited to local"):
        runtime.create_local_core_dependencies(storage_root=tmp_path)

    with pytest.raises(ValueError, match="requires a production"):
        create_production_core_dependencies(
            profile="local",
            db_url="sqlite:///:memory:",
            storage_root=tmp_path,
        )


def test_runtime_profile_factories_reject_unknown_profiles_and_blank_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="language-model"):
        runtime._language_model_adapter(
            "unknown",
            SimpleNamespace(),
            SimpleNamespace(),
            SimpleNamespace(),
            SimpleNamespace(),
        )
    with pytest.raises(ValueError, match="media storage"):
        runtime._media_storage_adapter("unknown", tmp_path)
    with pytest.raises(ValueError, match="media processor"):
        runtime._media_processor_adapter("unknown", SimpleNamespace())
    with pytest.raises(ValueError, match="content index"):
        runtime._content_index_adapter("unknown")
    with pytest.raises(ValueError, match="compute profile"):
        runtime._compute_adapter("unknown")
    monkeypatch.setenv("FOUNDRY_LITE_TRAINED_MODEL_PROFILE", "local")
    with pytest.raises(ValueError, match="protected runtimes"):
        runtime._trained_model_inference_adapter(RuntimeProfile.from_value("production"))

    monkeypatch.setenv("FOUNDRY_LITE_ANTHROPIC_MODEL", " ")
    with pytest.raises(ValueError, match="cannot be blank"):
        runtime._anthropic_model_catalog_seed()


def test_runtime_optional_adapter_profiles_build_from_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_S3_BUCKET", "bucket")
    monkeypatch.setattr(runtime, "S3MediaStorageAdapter", lambda config: config)
    monkeypatch.setattr(runtime, "S3ExternalMediaReader", lambda config: config)
    monkeypatch.setattr(runtime, "VideoSceneFrameProcessorAdapter", lambda **kwargs: kwargs)
    monkeypatch.setattr(runtime, "ElasticsearchContentIndexAdapter", lambda config: config)

    media = runtime._media_storage_adapter("s3-media", tmp_path)
    external = runtime._external_media_reader("s3-external")
    processor = runtime._media_processor_adapter("video-scene-frames", SimpleNamespace())
    content_index = runtime._content_index_adapter("elasticsearch")
    connector = runtime._connector_adapter("rest", SimpleNamespace())

    assert media.bucket == "bucket"
    assert external.region_name == "us-east-1"
    assert "scene_frame_extractor" in processor
    assert content_index.endpoint == "http://localhost:9200"
    assert connector is not None

    s3_config = runtime._s3_media_storage_config()
    external_config = runtime._s3_external_media_reader_config()
    assert s3_config.bucket == "bucket"
    assert external_config.region_name == "us-east-1"


def test_schema_mutation_policy_follows_runtime_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_RUNTIME_PROFILE", "production")
    assert runtime._schema_mutation_allowed_from_env() is False
    monkeypatch.setenv("FOUNDRY_LITE_RUNTIME_PROFILE", "test")
    assert runtime._schema_mutation_allowed_from_env() is True


@pytest.mark.parametrize(
    "raw",
    [
        "{",
        "{}",
        "[1]",
        '[{"streamName":"orders"}]',
        '[{"streamName":1,"topic":"orders"}]',
        '[{"streamName":"orders","topic":"orders","partition":"0"}]',
    ],
)
def test_kafka_subscription_config_rejects_malformed_json_contract(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_KAFKA_SUBSCRIPTIONS_JSON", raw)
    with pytest.raises(ValueError):
        runtime._kafka_stream_subscriptions()


def test_kafka_subscription_config_supports_empty_and_default_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FOUNDRY_LITE_KAFKA_SUBSCRIPTIONS_JSON", raising=False)
    assert runtime._kafka_stream_subscriptions() == ()

    monkeypatch.setenv(
        "FOUNDRY_LITE_KAFKA_SUBSCRIPTIONS_JSON",
        '[{"streamName":"orders","topic":"orders","partition":2},'
        '{"stream_name":"events","topic":"events","defaultTenantId":"tenant-a"}]',
    )
    subscriptions = runtime._kafka_stream_subscriptions()
    assert subscriptions[0].partition == 2
    assert subscriptions[0].default_tenant_id == "tenant-demo"
    assert subscriptions[1].default_tenant_id == "tenant-a"
