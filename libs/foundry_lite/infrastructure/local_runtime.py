"""Infrastructure support for local and protected runtime composition."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from foundry_lite.application.dependencies import (
    ActionDependencies,
    AipDependencies,
    CoreDependencies,
    DataDependencies,
    MediaDependencies,
    MediaProcessorRegistry,
    ObjectDependencies,
    PathDependencies,
    RuntimeDependencies,
    RuntimeProfile,
    SecurityDependencies,
    SourceDependencies,
)
from foundry_lite.application.ports import ComputeAdapter
from foundry_lite.application.ports.content_index import ContentIndexAdapter
from foundry_lite.application.ports.dataset_storage import DatasetStorageAdapter
from foundry_lite.application.ports.external_media_reader import ExternalMediaReader
from foundry_lite.application.ports.media_processor import MediaProcessorAdapter
from foundry_lite.application.ports.media_storage import MediaStorageAdapter
from foundry_lite.application.ports.model_registry_repository import ModelCatalogSeed
from foundry_lite.application.ports.ontology_repository import PropertyClassificationRow
from foundry_lite.application.ports.search_adapter import SearchAdapter
from foundry_lite.application.ports.secret_provider import SecretProvider
from foundry_lite.application.ports.stream_adapter import StreamAdapter
from foundry_lite.application.ports.trained_model_inference import TrainedModelInferencePort
from foundry_lite.application.ports.vision_embedding_model import VisionEmbeddingModelAdapter
from foundry_lite.application.ports.workflow_adapter import WorkflowAdapter
from foundry_lite.application.services.media.content_unit_evidence import ContentUnitEvidenceService
from foundry_lite.application.services.object_store.query_cursor import (
    require_object_query_cursor_signing_key_for_runtime,
)
from foundry_lite.application.services.ontology_yaml import action_allowed_roles
from foundry_lite.application.services.runtime_run_cursors import require_operations_cursor_signing_key_for_runtime
from foundry_lite.domain.ontology.datasources import property_datasource_rows
from foundry_lite.infrastructure.action_runtime_dependencies import (
    ConnectorActionEffectExecutor,
    InProcessActionFunctionExecutor,
    LocalActionRunOrchestrator,
    TemporalActionRunConfig,
    TemporalActionRunOrchestrator,
    action_file_scanner_adapter,
    action_notification_recipient_directory_adapter,
)
from foundry_lite.infrastructure.adapters import (
    AnthropicLanguageModel,
    AsrProcessorAdapter,
    AuthoritativeCitationSourceVerifier,
    ContainerCodeExecutionAdapter,
    DuckDBComputeAdapter,
    ElasticsearchAdapter,
    ElasticsearchAdapterConfig,
    ElasticsearchContentIndexAdapter,
    FakeComputeAdapter,
    FakeConnectorAdapter,
    FakeDatasetStorageAdapter,
    FakeLanguageModel,
    FakeSearchAdapter,
    FakeStreamAdapter,
    FakeWorkflowAdapter,
    IcebergDatasetStorageAdapter,
    IcebergDatasetStorageAdapterConfig,
    ImageProcessorAdapter,
    KafkaSourceStreamAdapter,
    KafkaStreamAdapter,
    KafkaStreamAdapterConfig,
    KafkaStreamSubscription,
    LocalBackupArtifactStore,
    LocalCompletionAdapter,
    LocalConnectorAdapter,
    LocalContentIndexAdapter,
    LocalDatasetStorageAdapter,
    LocalEmbeddingAdapter,
    LocalExternalMediaReader,
    LocalMediaStorageAdapter,
    LocalPipelineDagOrchestrator,
    LocalPreviewRendererAdapter,
    LocalSearchAdapter,
    LocalStreamAdapter,
    LocalWorkflowAdapter,
    OcrProcessorAdapter,
    PdfLayoutProcessorAdapter,
    PdfOcrProcessorAdapter,
    PdfTextProcessorAdapter,
    PostgresVirtualTableReader,
    RepositoryModelMediaResolver,
    RestPullConnectorAdapter,
    S3DatasetStorageAdapter,
    S3DatasetStorageAdapterConfig,
    S3ExternalMediaReader,
    S3ExternalMediaReaderConfig,
    S3MediaStorageAdapter,
    S3MediaStorageConfig,
    SparkComputeAdapter,
    SqlAlchemySourceDatabaseAdapter,
    TemporalPipelineDagConfig,
    TemporalPipelineDagOrchestrator,
    TemporalWorkflowAdapter,
    TemporalWorkflowAdapterConfig,
    VideoProbeProcessorAdapter,
    VideoSceneFrameProcessorAdapter,
    VideoSceneVisionProcessorAdapter,
)
from foundry_lite.infrastructure.adapters.asr_processor import _faster_whisper_asr_engine
from foundry_lite.infrastructure.adapters.fake_citation_source_verifier import FakeCitationSourceVerifier
from foundry_lite.infrastructure.adapters.fake_context_provider import FakeContextProvider
from foundry_lite.infrastructure.adapters.fake_tool_executor import FakeToolExecutor
from foundry_lite.infrastructure.adapters.local_embedding import (
    FASTEMBED_MODEL_VERSION,
    _fastembed_embedding_engine,
)
from foundry_lite.infrastructure.adapters.local_prompt_artifact_store import LocalPromptArtifactStore
from foundry_lite.infrastructure.adapters.local_vision_embedding import (
    CLIP_MODEL_VERSION,
    LocalVisionEmbeddingAdapter,
    _fastembed_clip_image_engine,
    _fastembed_clip_text_engine,
)
from foundry_lite.infrastructure.adapters.media_processor_registry import build_default_media_processor_registry
from foundry_lite.infrastructure.adapters.ocr_processor import _tesseract_ocr_engine
from foundry_lite.infrastructure.adapters.video_probe_processor import (
    _ffmpeg_scene_frame_extractor,
    _ffmpeg_scene_frame_paths,
    _ffprobe_video_probe_runner,
)
from foundry_lite.infrastructure.auth import LocalOAuthTokenIssuer
from foundry_lite.infrastructure.postgres_rls import install_postgres_rls_tenant_context
from foundry_lite.infrastructure.repositories import (
    SqlAlchemyActionBranchRepository,
    SqlAlchemyActionExecutionRepository,
    SqlAlchemyActionNotificationPolicyRepository,
    SqlAlchemyActionRepository,
    SqlAlchemyAiEvalRepository,
    SqlAlchemyAiRunRepository,
    SqlAlchemyConnectorRegistryRepository,
    SqlAlchemyDatasetQualityRepository,
    SqlAlchemyDatasetRepository,
    SqlAlchemyDatasetTransactionRepository,
    SqlAlchemyDatasetVersionRepository,
    SqlAlchemyDestructiveDevelopmentAdmin,
    SqlAlchemyErasureRepository,
    SqlAlchemyInsightReviewRepository,
    SqlAlchemyMaterializationRepository,
    SqlAlchemyMediaAccessCacheRepository,
    SqlAlchemyMediaDerivativeRepository,
    SqlAlchemyMediaReferenceBindingRepository,
    SqlAlchemyMediaRepository,
    SqlAlchemyMetadataRepository,
    SqlAlchemyModelRegistryRepository,
    SqlAlchemyOAuthSessionRepository,
    SqlAlchemyObjectIndexRepository,
    SqlAlchemyObjectIndexRowHashRepository,
    SqlAlchemyObjectReadRepository,
    SqlAlchemyObjectSetRepository,
    SqlAlchemyOntologyBranchRepository,
    SqlAlchemyOntologyRepository,
    SqlAlchemyOsdkApplicationRepository,
    SqlAlchemyPipelineExecutionRepository,
    SqlAlchemyPipelineRepository,
    SqlAlchemyResourceCatalogRepository,
    SqlAlchemyRuntimeRepository,
    SqlAlchemySemanticRowCacheRepository,
    SqlAlchemySourceManagementRepository,
    SqlAlchemySourceRegistryRepository,
    SqlAlchemyTransformRepository,
    SqlAlchemyVirtualTableRepository,
)
from foundry_lite.infrastructure.secrets import local_secret_vault_provider, secret_provider_from_env
from foundry_lite.infrastructure.trained_model_runtime_adapters import (
    ContainerTrainedModelInferenceAdapter,
    LocalTrainedModelInferenceAdapter,
)
from foundry_lite.security.policy import ActionRoleProvider, ClassificationProvider, PolicyService

_RUNTIME_PROFILE_ENV = "FOUNDRY_LITE_RUNTIME_PROFILE"
_ALLOW_LOCAL_PROMPT_ARTIFACT_KEY_ENV = "FOUNDRY_LITE_ALLOW_LOCAL_PROMPT_ARTIFACT_KEY"
_KAFKA_SUBSCRIPTIONS_ENV = "FOUNDRY_LITE_KAFKA_SUBSCRIPTIONS_JSON"
_ANTHROPIC_MODEL_ENV = "FOUNDRY_LITE_ANTHROPIC_MODEL"
_ANTHROPIC_AUTH_REFERENCE_ENV = "FOUNDRY_LITE_ANTHROPIC_SECRET_REF"
_ANTHROPIC_REGION_ENV = "FOUNDRY_LITE_ANTHROPIC_REGION"
_DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"
_LOCAL_FAKE_AUTH_REFERENCE = "local-fake-reference"
_PROTECTED_REQUIRED_ADAPTER_PROFILES: Mapping[str, frozenset[str]] = {
    "action_file_scanner": frozenset({"clamav"}),
    "dataset_storage": frozenset({"s3-storage", "iceberg"}),
    "media_storage": frozenset({"s3-media"}),
    "content_index": frozenset({"elasticsearch"}),
    "compute": frozenset({"spark"}),
    "connector": frozenset({"rest"}),
    "search": frozenset({"elasticsearch"}),
    "stream": frozenset({"kafka"}),
    "workflow": frozenset({"temporal"}),
    "language_model": frozenset({"anthropic"}),
}


@dataclass(frozen=True)
class RuntimeAdapterProfiles:
    """Resolved adapter profile names for one runtime composition."""

    dataset_storage: str
    media_storage: str
    media_processor: str
    content_index: str
    compute: str
    connector: str
    search: str
    stream: str
    workflow: str
    external_media: str
    language_model: str
    action_file_scanner: str

    @classmethod
    def from_env(
        cls,
        adapter_profile: str = "local",
        environ: Mapping[str, str] | None = None,
    ) -> RuntimeAdapterProfiles:
        source = os.environ if environ is None else environ
        base = _normalize_adapter_profile(adapter_profile)
        return cls(
            dataset_storage=base,
            media_storage=_env_profile(source, "FOUNDRY_LITE_MEDIA_STORAGE_PROFILE", base),
            media_processor=_env_profile(source, "FOUNDRY_LITE_MEDIA_PROCESSOR_PROFILE", base),
            content_index=_env_profile(source, "FOUNDRY_LITE_CONTENT_INDEX_PROFILE", base),
            compute=_env_profile(source, "FOUNDRY_LITE_COMPUTE_PROFILE", base),
            connector=_env_profile(source, "FOUNDRY_LITE_CONNECTOR_PROFILE", base),
            search=_env_profile(source, "FOUNDRY_LITE_SEARCH_PROFILE", base),
            stream=_env_profile(source, "FOUNDRY_LITE_STREAM_PROFILE", base),
            workflow=_env_profile(source, "FOUNDRY_LITE_WORKFLOW_PROFILE", base),
            external_media=_env_profile(source, "FOUNDRY_LITE_EXTERNAL_MEDIA_PROFILE", base),
            language_model=_env_profile(source, "FOUNDRY_LITE_LANGUAGE_MODEL_PROFILE", base),
            action_file_scanner=_env_profile(source, "FOUNDRY_LITE_ACTION_FILE_SCANNER_PROFILE", "local-signature"),
        )


def _classification_provider(
    engine: Engine, ontology_repository: SqlAlchemyOntologyRepository
) -> ClassificationProvider:
    """Read the active ontology's classified and segment-gated properties for a tenant."""

    def provider(tenant_id: str) -> list[Mapping[str, object]]:
        with engine.begin() as conn:
            classification_rows: list[PropertyClassificationRow] = ontology_repository.active_property_classifications(
                transaction=conn, tenant_id=tenant_id
            )
            datasource_rows = ontology_repository.active_property_datasource_rows(transaction=conn, tenant_id=tenant_id)
        rows: list[Mapping[str, object]] = list(classification_rows)
        rows.extend(property_datasource_rows(datasource_rows))
        return rows

    return provider


def _action_role_provider(engine: Engine, ontology_repository: SqlAlchemyOntologyRepository) -> ActionRoleProvider:
    """Read one action's declared apply roles from the active ontology."""

    def provider(tenant_id: str, action_api_name: str) -> tuple[str, ...] | None:
        with engine.begin() as conn:
            active = ontology_repository.active_ontology_version(transaction=conn, tenant_id=tenant_id)
            if active is None:
                return None
            row = ontology_repository.enabled_action_type_for_version(
                transaction=conn,
                tenant_id=tenant_id,
                ontology_version_id=active["id"],
                api_name=action_api_name,
            )
        if row is None:
            return None
        return action_allowed_roles(row["definition"])

    return provider


def create_local_core_dependencies(
    *,
    db_url: str | None = None,
    storage_root: str | Path | None = None,
    adapter_profile: str = "local",
) -> CoreDependencies:
    """Build the local/test/demo composition root used by CLI, API, tests, and demos."""

    runtime_profile = RuntimeProfile.from_value(os.getenv(_RUNTIME_PROFILE_ENV, "local"))
    if runtime_profile.is_protected:
        raise ValueError(
            "create_local_core_dependencies is limited to local/demo/test runtime profiles; "
            "use create_runtime_core_dependencies or create_production_core_dependencies for protected startup"
        )
    return _create_core_dependencies(
        runtime_profile=runtime_profile,
        db_url=db_url,
        storage_root=storage_root,
        profiles=RuntimeAdapterProfiles.from_env(adapter_profile),
    )


def create_runtime_core_dependencies(
    profile: RuntimeProfile | str | None = None,
    *,
    db_url: str | None = None,
    storage_root: str | Path | None = None,
    adapter_profile: str = "local",
) -> CoreDependencies:
    """Dispatch runtime composition from a normalized runtime profile."""

    runtime_profile = RuntimeProfile.from_value(profile or os.getenv(_RUNTIME_PROFILE_ENV, "local"))
    if runtime_profile.is_protected:
        return create_production_core_dependencies(
            profile=runtime_profile,
            db_url=db_url,
            storage_root=storage_root,
            adapter_profile=adapter_profile,
        )
    return _create_core_dependencies(
        runtime_profile=runtime_profile,
        db_url=db_url,
        storage_root=storage_root,
        profiles=RuntimeAdapterProfiles.from_env(adapter_profile),
    )


def create_production_core_dependencies(
    profile: RuntimeProfile | str | None = None,
    *,
    db_url: str | None = None,
    storage_root: str | Path | None = None,
    adapter_profile: str = "local",
) -> CoreDependencies:
    """Build protected runtime dependencies and reject local-only adapter choices before startup."""

    runtime_profile = RuntimeProfile.from_value(profile or os.getenv(_RUNTIME_PROFILE_ENV, "production"))
    if not runtime_profile.is_protected:
        raise ValueError("create_production_core_dependencies requires a production or staging runtime profile")
    profiles = RuntimeAdapterProfiles.from_env(adapter_profile)
    _reject_protected_local_profiles(profiles)
    return _create_core_dependencies(
        runtime_profile=runtime_profile,
        db_url=db_url,
        storage_root=storage_root,
        profiles=profiles,
    )


def _create_core_dependencies(
    *,
    runtime_profile: RuntimeProfile,
    db_url: str | None,
    storage_root: str | Path | None,
    profiles: RuntimeAdapterProfiles,
) -> CoreDependencies:
    require_object_query_cursor_signing_key_for_runtime()
    require_operations_cursor_signing_key_for_runtime()
    root = Path(storage_root or ".foundry-lite").resolve()
    root.mkdir(parents=True, exist_ok=True)
    object_storage_root = root / "object-storage"
    object_storage_root.mkdir(parents=True, exist_ok=True)
    media_storage_root = root / "media-storage"
    media_storage_root.mkdir(parents=True, exist_ok=True)
    prompt_artifacts_root = root / "prompt-artifacts"
    prompt_artifacts_root.mkdir(parents=True, exist_ok=True)
    backup_artifacts_root = root / "backup-artifacts"
    backup_artifacts_root.mkdir(parents=True, exist_ok=True)

    storage_adapter = _dataset_storage_adapter(profiles.dataset_storage, object_storage_root)
    media_storage = _media_storage_adapter(profiles.media_storage, media_storage_root)
    vision_embedding_model_adapter = LocalVisionEmbeddingAdapter(
        image_engine=_fastembed_clip_image_engine,
        text_engine=_fastembed_clip_text_engine,
        model_version=CLIP_MODEL_VERSION,
    )
    media_processor = _media_processor_adapter(profiles.media_processor, vision_embedding_model_adapter)
    media_processor_registry = _media_processor_registry(
        profiles.media_processor,
        vision_embedding_model_adapter,
    )
    content_index_adapter = _content_index_adapter(profiles.content_index)
    embedding_model_adapter = LocalEmbeddingAdapter(
        embedding_engine=_fastembed_embedding_engine, model_version=FASTEMBED_MODEL_VERSION
    )
    completion_model_adapter = LocalCompletionAdapter()
    # One sandbox serves both callers. A Python transform reaches it through the compute
    # adapter and a Python ontology function through the application layer, and they are the
    # same threat model, so they must not drift onto separate policies.
    code_execution_adapter = ContainerCodeExecutionAdapter(
        is_image_digest_required=runtime_profile.is_protected,
    )
    compute_adapter = _compute_adapter(
        profiles.compute,
        code_execution_adapter=code_execution_adapter,
    )
    env_secret_provider = secret_provider_from_env()
    secret_vault = local_secret_vault_provider(root, fallback=env_secret_provider)
    secret_provider = secret_vault
    connector_adapter = _connector_adapter(profiles.connector, secret_provider)
    search_adapter = _search_adapter(profiles.search)
    stream_adapter = _stream_adapter(profiles.stream)
    workflow_adapter = _workflow_adapter(profiles.workflow)
    pipeline_dag_orchestrator = _pipeline_dag_orchestrator(profiles.workflow)
    action_run_orchestrator = _action_run_orchestrator(profiles.workflow)
    action_function_executor = InProcessActionFunctionExecutor()
    action_file_scanner = action_file_scanner_adapter(profiles.action_file_scanner)
    database_url = db_url or f"sqlite:///{root / 'foundry-lite.db'}"
    engine = create_engine(database_url, future=True)
    install_postgres_rls_tenant_context(engine)
    ontology_repository = SqlAlchemyOntologyRepository(engine)
    connector_registry_repository = SqlAlchemyConnectorRegistryRepository(engine)
    media_repository = SqlAlchemyMediaRepository(engine)
    media_derivative_repository = SqlAlchemyMediaDerivativeRepository(engine)
    policy = PolicyService(
        classification_provider=_classification_provider(engine, ontology_repository),
        action_role_provider=_action_role_provider(engine, ontology_repository),
    )
    content_unit_evidence_service = ContentUnitEvidenceService(
        engine=engine,
        policy=policy,
        media_repository=media_repository,
        media_derivative_repository=media_derivative_repository,
    )
    citation_source_verifier = AuthoritativeCitationSourceVerifier(
        content_unit_evidence_service,
        FakeCitationSourceVerifier(),
    )
    language_model_adapter = _language_model_adapter(
        profiles.language_model,
        secret_provider,
        engine,
        media_repository,
        media_storage,
    )
    notification_policy_repository = SqlAlchemyActionNotificationPolicyRepository(
        engine,
        fallback=action_notification_recipient_directory_adapter(is_protected=runtime_profile.is_protected),
    )
    allow_schema_mutation = not runtime_profile.is_protected
    return CoreDependencies(
        profile=runtime_profile,
        pipeline_dag_orchestrator=pipeline_dag_orchestrator,
        paths=PathDependencies(root=root, storage_root=object_storage_root),
        security=SecurityDependencies(
            engine=engine,
            policy=policy,
            metadata_repository=SqlAlchemyMetadataRepository(
                engine,
                allow_schema_mutation=allow_schema_mutation,
            ),
            destructive_development_admin=SqlAlchemyDestructiveDevelopmentAdmin(
                engine,
                allow_schema_mutation=allow_schema_mutation,
            ),
            osdk_application_repository=SqlAlchemyOsdkApplicationRepository(engine),
            oauth_session_repository=SqlAlchemyOAuthSessionRepository(engine),
            oauth_token_issuer=LocalOAuthTokenIssuer.from_key_path(root / "oauth-private-key.pem"),
            secret_provider=secret_provider,
            secret_vault=secret_vault,
        ),
        action=ActionDependencies(
            action_repository=SqlAlchemyActionRepository(engine),
            action_branch_repository=SqlAlchemyActionBranchRepository(engine),
            action_execution_repository=SqlAlchemyActionExecutionRepository(engine),
            action_effect_executor=ConnectorActionEffectExecutor(
                engine,
                connector_registry_repository,
                secret_provider,
                stream_adapter,
            ),
            action_function_executor=action_function_executor,
            action_run_orchestrator=action_run_orchestrator,
            action_file_scanner=action_file_scanner,
            action_notification_recipient_directory=notification_policy_repository,
            action_notification_policy_repository=notification_policy_repository,
        ),
        data=DataDependencies(
            ontology_repository=ontology_repository,
            ontology_branch_repository=SqlAlchemyOntologyBranchRepository(engine),
            pipeline_repository=SqlAlchemyPipelineRepository(engine),
            pipeline_execution_repository=SqlAlchemyPipelineExecutionRepository(engine),
            resource_catalog_repository=SqlAlchemyResourceCatalogRepository(engine),
            transform_repository=SqlAlchemyTransformRepository(engine),
            materialization_repository=SqlAlchemyMaterializationRepository(engine),
            dataset_quality_repository=SqlAlchemyDatasetQualityRepository(engine),
            compute_adapter=compute_adapter,
            code_execution_adapter=code_execution_adapter,
            dataset_repository=SqlAlchemyDatasetRepository(engine),
            dataset_transaction_repository=SqlAlchemyDatasetTransactionRepository(engine),
            dataset_version_repository=SqlAlchemyDatasetVersionRepository(engine),
            dataset_storage=storage_adapter,
        ),
        object_store=ObjectDependencies(
            object_index_repository=SqlAlchemyObjectIndexRepository(engine),
            object_index_row_hash_repository=SqlAlchemyObjectIndexRowHashRepository(engine),
            object_read_repository=SqlAlchemyObjectReadRepository(engine),
            object_set_repository=SqlAlchemyObjectSetRepository(engine),
            search_adapter=search_adapter,
        ),
        runtime=RuntimeDependencies(
            runtime_repository=SqlAlchemyRuntimeRepository(engine),
            erasure_repository=SqlAlchemyErasureRepository(engine),
            insight_review_repository=SqlAlchemyInsightReviewRepository(engine),
            stream_adapter=stream_adapter,
            workflow_adapter=workflow_adapter,
            backup_artifact_store=LocalBackupArtifactStore(backup_artifacts_root),
        ),
        aip=AipDependencies(
            ai_eval_repository=SqlAlchemyAiEvalRepository(engine),
            ai_run_repository=SqlAlchemyAiRunRepository(engine),
            embedding_model_adapter=embedding_model_adapter,
            completion_model_adapter=completion_model_adapter,
            vision_embedding_model_adapter=vision_embedding_model_adapter,
            language_model_adapter=language_model_adapter,
            model_registry_repository=SqlAlchemyModelRegistryRepository(engine),
            semantic_row_cache_repository=SqlAlchemySemanticRowCacheRepository(engine),
            context_provider=FakeContextProvider(),
            prompt_artifact_store=LocalPromptArtifactStore(
                prompt_artifacts_root,
                secret_provider,
                allow_local_dev_fallback=_allow_local_prompt_artifact_key_from_env(),
            ),
            citation_source_verifier=citation_source_verifier,
            tool_executor=FakeToolExecutor(),
            model_catalog_seed=_language_model_catalog_seed(profiles.language_model),
            trained_model_inference_port=_trained_model_inference_adapter(runtime_profile),
        ),
        media=MediaDependencies(
            media_repository=media_repository,
            media_derivative_repository=media_derivative_repository,
            media_reference_binding_repository=SqlAlchemyMediaReferenceBindingRepository(engine),
            media_access_cache_repository=SqlAlchemyMediaAccessCacheRepository(engine),
            media_storage=media_storage,
            media_processor=media_processor,
            media_processor_registry=media_processor_registry,
            media_preview_renderer=LocalPreviewRendererAdapter(),
            external_media_reader=_external_media_reader(profiles.external_media),
            content_index_adapter=content_index_adapter,
        ),
        source=SourceDependencies(
            connector_adapter=connector_adapter,
            connector_registry_repository=connector_registry_repository,
            source_registry_repository=SqlAlchemySourceRegistryRepository(engine),
            source_management_repository=SqlAlchemySourceManagementRepository(engine),
            source_database_adapter=SqlAlchemySourceDatabaseAdapter(),
            source_stream_adapter=KafkaSourceStreamAdapter(),
            virtual_table_repository=SqlAlchemyVirtualTableRepository(engine),
            virtual_table_reader=PostgresVirtualTableReader(),
        ),
    )


def _language_model_adapter(
    profile: str,
    secret_provider: SecretProvider,
    engine: Engine,
    media_repository: SqlAlchemyMediaRepository,
    media_storage: MediaStorageAdapter,
) -> FakeLanguageModel | AnthropicLanguageModel:
    profile = _env_profile(os.environ, "FOUNDRY_LITE_LANGUAGE_MODEL_PROFILE", profile)
    if profile == "anthropic":
        return AnthropicLanguageModel(
            secret_provider,
            media_resolver=RepositoryModelMediaResolver(engine, media_repository, media_storage),
        )
    if profile in {"local", "fake-storage", "s3-storage", "iceberg", "s3-media", "fake-language-model"}:
        return FakeLanguageModel()
    raise ValueError(f"unknown language-model profile: {profile}")


def _language_model_catalog_seed(profile: str) -> ModelCatalogSeed:
    profile = _env_profile(os.environ, "FOUNDRY_LITE_LANGUAGE_MODEL_PROFILE", profile)
    if profile == "anthropic":
        return _anthropic_model_catalog_seed()
    return _fake_model_catalog_seed()


def _anthropic_model_catalog_seed() -> ModelCatalogSeed:
    provider_model_id = os.getenv(_ANTHROPIC_MODEL_ENV, _DEFAULT_ANTHROPIC_MODEL).strip()
    if not provider_model_id:
        raise ValueError(f"{_ANTHROPIC_MODEL_ENV} cannot be blank")
    return ModelCatalogSeed(
        provider_id="anthropic-direct-provider",
        provider_type="anthropic",
        profile_name="anthropic",
        region=os.getenv(_ANTHROPIC_REGION_ENV, "global").strip() or "global",
        secret_ref=os.getenv(_ANTHROPIC_AUTH_REFERENCE_ENV, "anthropic_api_key").strip() or "anthropic_api_key",
        retention_policy="anthropic_api_account_policy",
        training_policy="anthropic_api_account_policy",
        model_id=f"anthropic:{provider_model_id}",
        provider_model_id=provider_model_id,
        revision=provider_model_id,
        lifecycle="stable",
        capabilities_json={
            "streaming": True,
            "image_input": True,
            "pdf_input": True,
            "structured_outputs": True,
            "sampling_parameters": not provider_model_id.startswith("claude-sonnet-5"),
        },
        context_limit=1_000_000 if provider_model_id.startswith("claude-sonnet-5") else 200_000,
        output_limit=128_000 if provider_model_id.startswith("claude-sonnet-5") else 64_000,
        pricing_json={},
        allowed_classifications=("public", "internal"),
        aliases=("default-completion", "gpt-governed", "document-vlm"),
    )


def _fake_model_catalog_seed() -> ModelCatalogSeed:
    return ModelCatalogSeed(
        provider_id="local-fake-provider",
        provider_type="local-fake",
        profile_name="fake-language-model",
        region="us-east-1",
        secret_ref=_LOCAL_FAKE_AUTH_REFERENCE,
        retention_policy="zero_retention",
        training_policy="no_train",
        model_id="local-fake-model",
        provider_model_id="local-fake-echo",
        revision="2026-06-25",
        lifecycle="stable",
        capabilities_json={
            "streaming": True,
            "native_tools": False,
            "image_input": True,
            "pdf_input": True,
            "structured_outputs": True,
        },
        context_limit=8192,
        output_limit=1024,
        pricing_json={"input_per_1k": 0.002, "output_per_1k": 0.006, "currency": "USD"},
        allowed_classifications=("public", "internal"),
        aliases=("default-completion", "gpt-governed", "document-vlm"),
    )


def _dataset_storage_adapter(storage_profile: str, object_storage_root: Path) -> DatasetStorageAdapter:
    if storage_profile == "local":
        return LocalDatasetStorageAdapter(object_storage_root)
    if storage_profile == "fake-storage":
        return FakeDatasetStorageAdapter(object_storage_root)
    if storage_profile == "s3-storage":
        return S3DatasetStorageAdapter(_s3_storage_config(object_storage_root))
    if storage_profile == "iceberg":
        return IcebergDatasetStorageAdapter(_iceberg_storage_config(object_storage_root))
    raise ValueError(f"unknown adapter profile for dataset storage: {storage_profile}")


def _media_storage_adapter(media_profile: str, media_storage_root: Path) -> MediaStorageAdapter:
    if media_profile == "s3-media":
        return S3MediaStorageAdapter(_s3_media_storage_config())
    if media_profile in {"local", "fake-storage", "s3-storage", "iceberg"}:
        return LocalMediaStorageAdapter(media_storage_root)
    raise ValueError(f"unknown media storage profile: {media_profile}")


def _external_media_reader(external_media_profile: str) -> ExternalMediaReader:
    if external_media_profile == "s3-external":
        return S3ExternalMediaReader(_s3_external_media_reader_config())
    return LocalExternalMediaReader()


def _s3_external_media_reader_config() -> S3ExternalMediaReaderConfig:
    return S3ExternalMediaReaderConfig(
        endpoint_url=os.getenv("FOUNDRY_LITE_S3_ENDPOINT_URL"),
        access_key_id=os.getenv("FOUNDRY_LITE_S3_ACCESS_KEY_ID"),
        secret_access_key=os.getenv("FOUNDRY_LITE_S3_SECRET_ACCESS_KEY"),
        region_name=os.getenv("FOUNDRY_LITE_S3_REGION", "us-east-1"),
    )


def _media_processor_adapter(
    processor_profile: str, vision_embedding_model: VisionEmbeddingModelAdapter
) -> MediaProcessorAdapter:
    processor_profile = _env_profile(os.environ, "FOUNDRY_LITE_MEDIA_PROCESSOR_PROFILE", processor_profile)
    if processor_profile == "image-pillow":
        return ImageProcessorAdapter()
    if processor_profile == "ffprobe":
        return VideoProbeProcessorAdapter(probe_runner=_ffprobe_video_probe_runner)
    if processor_profile == "video-scene-frames":
        return VideoSceneFrameProcessorAdapter(scene_frame_extractor=_ffmpeg_scene_frame_extractor)
    if processor_profile == "video-scene-vision":
        return VideoSceneVisionProcessorAdapter(
            vision_embedding_model, scene_frame_path_extractor=_ffmpeg_scene_frame_paths
        )
    if processor_profile == "ocr-tesseract":
        return OcrProcessorAdapter(ocr_engine=_tesseract_ocr_engine)
    if processor_profile == "asr-whisper":
        return AsrProcessorAdapter(asr_engine=_faster_whisper_asr_engine)
    if processor_profile == "pdf-layout-pypdf":
        return PdfLayoutProcessorAdapter()
    if processor_profile == "pdf-ocr-tesseract-poppler":
        return PdfOcrProcessorAdapter()
    if processor_profile in {"local", "fake-storage", "s3-storage", "iceberg", "s3-media", "pdf-pypdf"}:
        return PdfTextProcessorAdapter()
    raise ValueError(f"unknown media processor profile: {processor_profile}")


def _media_processor_registry(
    processor_profile: str,
    vision_embedding_model: VisionEmbeddingModelAdapter,
) -> MediaProcessorRegistry | None:
    if processor_profile in {"local", "fake-storage", "s3-storage", "iceberg", "s3-media"}:
        return build_default_media_processor_registry(vision_embedding_model)
    return None


def _content_index_adapter(index_profile: str) -> ContentIndexAdapter:
    if index_profile == "elasticsearch":
        endpoint = os.getenv("FOUNDRY_LITE_ELASTICSEARCH_URL", "http://localhost:9200")
        return ElasticsearchContentIndexAdapter(ElasticsearchAdapterConfig(endpoint=endpoint))
    if index_profile in {"local", "fake-storage", "s3-storage", "iceberg", "s3-media"}:
        return LocalContentIndexAdapter()
    raise ValueError(f"unknown content index profile: {index_profile}")


def _s3_media_storage_config() -> S3MediaStorageConfig:
    return S3MediaStorageConfig(
        bucket=os.environ["FOUNDRY_LITE_S3_BUCKET"],
        endpoint_url=os.getenv("FOUNDRY_LITE_S3_ENDPOINT_URL"),
        access_key_id=os.getenv("FOUNDRY_LITE_S3_ACCESS_KEY_ID"),
        secret_access_key=os.getenv("FOUNDRY_LITE_S3_SECRET_ACCESS_KEY"),
        region_name=os.getenv("FOUNDRY_LITE_S3_REGION", "us-east-1"),
        prefix=f"{os.getenv('FOUNDRY_LITE_S3_PREFIX', 'foundry-lite')}/media",
        should_create_bucket_if_missing=os.getenv("FOUNDRY_LITE_S3_CREATE_BUCKET", "1") != "0",
    )


def _compute_adapter(
    compute_profile: str,
    *,
    code_execution_adapter: ContainerCodeExecutionAdapter,
) -> ComputeAdapter:
    compute_profile = _env_profile(os.environ, "FOUNDRY_LITE_COMPUTE_PROFILE", compute_profile)
    if compute_profile == "fake-storage":
        return FakeComputeAdapter()
    if compute_profile == "spark":
        return SparkComputeAdapter(code_execution_adapter=code_execution_adapter)
    if compute_profile in {"local", "s3-storage", "iceberg"}:
        return DuckDBComputeAdapter(code_execution_adapter=code_execution_adapter)
    raise ValueError(f"unknown compute profile: {compute_profile}")


def _trained_model_inference_adapter(runtime_profile: RuntimeProfile) -> TrainedModelInferencePort:
    default_profile = "local" if runtime_profile.is_local_like else "container"
    profile = _env_profile(os.environ, "FOUNDRY_LITE_TRAINED_MODEL_PROFILE", default_profile)
    if profile == "local" and runtime_profile.is_local_like:
        return LocalTrainedModelInferenceAdapter()
    if profile == "container":
        return ContainerTrainedModelInferenceAdapter(
            is_image_digest_required=runtime_profile.is_protected,
        )
    raise ValueError("protected runtimes require FOUNDRY_LITE_TRAINED_MODEL_PROFILE=container")


def _connector_adapter(
    connector_profile: str,
    secret_provider: SecretProvider,
) -> LocalConnectorAdapter | RestPullConnectorAdapter | FakeConnectorAdapter:
    connector_profile = _env_profile(os.environ, "FOUNDRY_LITE_CONNECTOR_PROFILE", connector_profile)
    if connector_profile == "rest":
        return RestPullConnectorAdapter(secret_provider=secret_provider)
    if connector_profile in {"local", "s3-storage", "iceberg"}:
        return LocalConnectorAdapter()
    if connector_profile == "fake-storage":
        return FakeConnectorAdapter()
    raise ValueError(f"unknown connector profile: {connector_profile}")


def _search_adapter(search_profile: str) -> SearchAdapter:
    search_profile = _env_profile(os.environ, "FOUNDRY_LITE_SEARCH_PROFILE", search_profile)
    if search_profile in {"local", "s3-storage", "iceberg"}:
        return LocalSearchAdapter()
    if search_profile == "fake-storage":
        return FakeSearchAdapter()
    if search_profile == "elasticsearch":
        endpoint = os.getenv("FOUNDRY_LITE_ELASTICSEARCH_URL", "http://localhost:9200")
        return ElasticsearchAdapter(ElasticsearchAdapterConfig(endpoint=endpoint))
    raise ValueError(f"unknown search adapter profile: {search_profile}")


def _stream_adapter(stream_profile: str) -> StreamAdapter:
    stream_profile = _env_profile(os.environ, "FOUNDRY_LITE_STREAM_PROFILE", stream_profile)
    if stream_profile in {"local", "s3-storage", "iceberg"}:
        return LocalStreamAdapter()
    if stream_profile == "fake-storage":
        return FakeStreamAdapter()
    if stream_profile == "kafka":
        return KafkaStreamAdapter(_kafka_stream_config())
    raise ValueError(f"unknown adapter profile for stream: {stream_profile}")


def _workflow_adapter(workflow_profile: str) -> WorkflowAdapter:
    workflow_profile = _env_profile(os.environ, "FOUNDRY_LITE_WORKFLOW_PROFILE", workflow_profile)
    if workflow_profile == "temporal":
        return TemporalWorkflowAdapter(_temporal_workflow_config())
    if workflow_profile in {"local", "s3-storage", "iceberg"}:
        return LocalWorkflowAdapter()
    if workflow_profile == "fake-storage":
        return FakeWorkflowAdapter()
    raise ValueError(f"unknown workflow profile: {workflow_profile}")


def _pipeline_dag_orchestrator(
    workflow_profile: str,
) -> LocalPipelineDagOrchestrator | TemporalPipelineDagOrchestrator:
    workflow_profile = _env_profile(os.environ, "FOUNDRY_LITE_WORKFLOW_PROFILE", workflow_profile)
    if workflow_profile == "temporal":
        return TemporalPipelineDagOrchestrator(
            TemporalPipelineDagConfig(
                address=os.getenv("FOUNDRY_LITE_TEMPORAL_ADDRESS", "localhost:7233"),
                namespace=os.getenv("FOUNDRY_LITE_TEMPORAL_NAMESPACE", "default"),
                task_queue=os.getenv("FOUNDRY_LITE_PIPELINE_DAG_TASK_QUEUE", "foundry-lite-pipeline-dag"),
                execution_timeout_seconds=int(os.getenv("FOUNDRY_LITE_PIPELINE_DAG_EXECUTION_TIMEOUT", "86400")),
            )
        )
    return LocalPipelineDagOrchestrator()


def _action_run_orchestrator(
    workflow_profile: str,
) -> LocalActionRunOrchestrator | TemporalActionRunOrchestrator:
    workflow_profile = _env_profile(os.environ, "FOUNDRY_LITE_WORKFLOW_PROFILE", workflow_profile)
    if workflow_profile == "temporal":
        return TemporalActionRunOrchestrator(
            TemporalActionRunConfig(
                address=os.getenv("FOUNDRY_LITE_TEMPORAL_ADDRESS", "localhost:7233"),
                namespace=os.getenv("FOUNDRY_LITE_TEMPORAL_NAMESPACE", "default"),
                task_queue=os.getenv("FOUNDRY_LITE_ACTION_RUN_TASK_QUEUE", "foundry-lite-action-runs"),
            )
        )
    return LocalActionRunOrchestrator()


def _schema_mutation_allowed_from_env() -> bool:
    runtime_profile = RuntimeProfile.from_value(os.getenv(_RUNTIME_PROFILE_ENV, "local"))
    return not runtime_profile.is_protected


def _allow_local_prompt_artifact_key_from_env() -> bool:
    return os.getenv(_ALLOW_LOCAL_PROMPT_ARTIFACT_KEY_ENV, "").strip().casefold() in {"1", "true", "yes"}


def _temporal_workflow_config() -> TemporalWorkflowAdapterConfig:
    return TemporalWorkflowAdapterConfig(
        address=os.getenv("FOUNDRY_LITE_TEMPORAL_ADDRESS", "localhost:7233"),
        namespace=os.getenv("FOUNDRY_LITE_TEMPORAL_NAMESPACE", "default"),
        task_queue=os.getenv("FOUNDRY_LITE_TEMPORAL_TASK_QUEUE", "foundry-lite"),
        execution_timeout_seconds=int(os.getenv("FOUNDRY_LITE_TEMPORAL_EXECUTION_TIMEOUT", "300")),
    )


def _kafka_stream_config() -> KafkaStreamAdapterConfig:
    return KafkaStreamAdapterConfig(
        bootstrap_servers=os.getenv("FOUNDRY_LITE_KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
        subscriptions=_kafka_stream_subscriptions(),
        consumer_group=os.getenv("FOUNDRY_LITE_KAFKA_CONSUMER_GROUP", "foundry-lite-archive"),
        poll_timeout_seconds=float(os.getenv("FOUNDRY_LITE_KAFKA_POLL_TIMEOUT_SECONDS", "1.0")),
        max_empty_polls=int(os.getenv("FOUNDRY_LITE_KAFKA_MAX_EMPTY_POLLS", "1")),
        producer_flush_timeout_seconds=float(os.getenv("FOUNDRY_LITE_KAFKA_PRODUCER_FLUSH_TIMEOUT_SECONDS", "5.0")),
    )


def _kafka_stream_subscriptions() -> tuple[KafkaStreamSubscription, ...]:
    raw = os.getenv(_KAFKA_SUBSCRIPTIONS_ENV, "").strip()
    if not raw:
        return ()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{_KAFKA_SUBSCRIPTIONS_ENV} must be valid JSON") from exc
    if not isinstance(parsed, list):
        raise ValueError(f"{_KAFKA_SUBSCRIPTIONS_ENV} must be a JSON array")
    return tuple(_kafka_stream_subscription(item) for item in parsed)


def _kafka_stream_subscription(item: object) -> KafkaStreamSubscription:
    subscription = _kafka_subscription_mapping(item)
    stream_name = _required_subscription_string(subscription, "stream_name", "streamName")
    topic = _required_subscription_string(subscription, "topic")
    partition = _subscription_partition(subscription)
    tenant_id = _subscription_string_value(subscription, "default_tenant_id", "defaultTenantId")
    if tenant_id is None:
        return KafkaStreamSubscription(stream_name=stream_name, topic=topic, partition=partition)
    return KafkaStreamSubscription(
        stream_name=stream_name,
        topic=topic,
        partition=partition,
        default_tenant_id=tenant_id,
    )


def _kafka_subscription_mapping(item: object) -> Mapping[str, object]:
    if not isinstance(item, Mapping):
        raise ValueError(f"{_KAFKA_SUBSCRIPTIONS_ENV} entries must be JSON objects")
    return item


def _required_subscription_string(item: Mapping[str, object], *keys: str) -> str:
    value = _subscription_string_value(item, *keys)
    if value:
        return value
    raise ValueError(f"{_KAFKA_SUBSCRIPTIONS_ENV} entries require {' or '.join(keys)}")


def _subscription_string_value(item: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = item.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValueError(f"{_KAFKA_SUBSCRIPTIONS_ENV} {key} must be a string")
        return value
    return None


def _subscription_partition(item: Mapping[str, object]) -> int:
    partition = item.get("partition", 0)
    if not isinstance(partition, int):
        raise ValueError(f"{_KAFKA_SUBSCRIPTIONS_ENV} partition must be an integer")
    return partition


def _s3_storage_config(object_storage_root: Path) -> S3DatasetStorageAdapterConfig:
    return S3DatasetStorageAdapterConfig(
        bucket=os.environ["FOUNDRY_LITE_S3_BUCKET"],
        endpoint_url=os.getenv("FOUNDRY_LITE_S3_ENDPOINT_URL"),
        access_key_id=os.getenv("FOUNDRY_LITE_S3_ACCESS_KEY_ID"),
        secret_access_key=os.getenv("FOUNDRY_LITE_S3_SECRET_ACCESS_KEY"),
        region_name=os.getenv("FOUNDRY_LITE_S3_REGION", "us-east-1"),
        prefix=os.getenv("FOUNDRY_LITE_S3_PREFIX", "foundry-lite"),
        cache_root=object_storage_root / "_s3-cache",
        should_create_bucket_if_missing=os.getenv("FOUNDRY_LITE_S3_CREATE_BUCKET", "1") != "0",
    )


def _iceberg_storage_config(object_storage_root: Path) -> IcebergDatasetStorageAdapterConfig:
    return IcebergDatasetStorageAdapterConfig(
        catalog_uri=os.environ["FOUNDRY_LITE_ICEBERG_CATALOG_URI"],
        warehouse=os.environ["FOUNDRY_LITE_ICEBERG_WAREHOUSE"],
        namespace=os.getenv("FOUNDRY_LITE_ICEBERG_NAMESPACE", "foundry_lite"),
        cache_root=object_storage_root / "_iceberg-cache",
        s3_endpoint_url=os.getenv("FOUNDRY_LITE_S3_ENDPOINT_URL"),
        s3_access_key_id=os.getenv("FOUNDRY_LITE_S3_ACCESS_KEY_ID"),
        s3_secret_access_key=os.getenv("FOUNDRY_LITE_S3_SECRET_ACCESS_KEY"),
        s3_region=os.getenv("FOUNDRY_LITE_S3_REGION", "us-east-1"),
    )


def _normalize_adapter_profile(value: str | None) -> str:
    normalized = (value or "local").strip().lower().replace("_", "-")
    return normalized or "local"


def _env_profile(source: Mapping[str, str], key: str, default: str) -> str:
    return _normalize_adapter_profile(source.get(key, default))


def _reject_protected_local_profiles(profiles: RuntimeAdapterProfiles) -> None:
    invalid: list[str] = []
    for field_name, allowed in _PROTECTED_REQUIRED_ADAPTER_PROFILES.items():
        selected = getattr(profiles, field_name)
        if selected not in allowed:
            invalid.append(f"{field_name}={selected} (allowed: {', '.join(sorted(allowed))})")
    if invalid:
        raise ValueError(
            "production runtime requires production adapter profiles; "
            "configure protected adapter env overrides before startup: " + "; ".join(invalid)
        )
