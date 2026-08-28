"""Application-layer models and helpers for dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, cast

from foundry_lite.application.dependency_action import (
    ActionBranchRepository,
    ActionDependencies,
    ActionDependencyAccessors,
    ActionEffectExecutor,
    ActionExecutionRepository,
    ActionFileScanner,
    ActionFunctionExecutor,
    ActionNotificationPolicyRepository,
    ActionNotificationRecipientDirectory,
    ActionRepository,
    ActionRunOrchestrator,
)
from foundry_lite.application.dependency_aip import AipDependencies
from foundry_lite.application.dependency_aip_ports import (
    CitationSourceVerifier,
    CompletionModelAdapter,
    EmbeddingModelAdapter,
    GovernedSemanticModelPort,
    LanguageModelAdapter,
    ModelRegistryRepository,
    SemanticRowCacheRepository,
    ToolExecutor,
    TrainedModelInferencePort,
    VisionEmbeddingModelAdapter,
)
from foundry_lite.application.dependency_compat import (
    BundleFactory,
    apply_flat_dependency_overrides,
    assign_dependency_bundles,
    dependency_bundles,
    fill_missing_bundles_from_flat_overrides,
    preserve_media_processor_override,
    required_dependency,
)
from foundry_lite.application.dependency_core import (
    DestructiveDevelopmentAdmin,
    McpRateLimiter,
    MetadataRepository,
    OAuthSessionRepository,
    OAuthTokenIssuer,
    ObjectDependencies,
    ObjectIndexRepository,
    ObjectIndexRowHashRepository,
    ObjectReadRepository,
    ObjectSetRepository,
    OsdkApplicationRepository,
    OsdkDownloadTokenSigner,
    OsdkReleaseArtifactStore,
    Path,
    PathDependencies,
    PolicyService,
    SearchAdapter,
    SecretProvider,
    SecretVault,
    SecurityDependencies,
    TransactionManager,
)
from foundry_lite.application.dependency_data import (
    CodeExecutionAdapter,
    ComputeAdapter,
    DataDependencies,
    DataDependencyAccessors,
    DatasetQualityRepository,
    DatasetRepository,
    DatasetStorageAdapter,
    DatasetTransactionRepository,
    DatasetVersionRepository,
    MaterializationRepository,
    OntologyBranchRepository,
    OntologyDefinitionReader,
    OntologyRepository,
    PipelineExecutionRepository,
    PipelineRepository,
    ResourceCatalogRepository,
    TransformRepository,
    TransformSourceStore,
)
from foundry_lite.application.dependency_media import (
    ContentIndexAdapter,
    ExternalMediaReader,
    MediaAccessCacheRepository,
    MediaDependencies,
    MediaDerivativeRepository,
    MediaPreviewRendererAdapter,
    MediaProcessorAdapter,
    MediaProcessorRegistry,
    MediaReferenceBindingRepository,
    MediaRepository,
    MediaSourceWorkspace,
    MediaStorageAdapter,
)
from foundry_lite.application.dependency_release import (
    GovernedReleaseDeliveryConfig,
    GovernedReleaseDependencies,
    GovernedReleaseDependencyAccessors,
    GovernedReleaseLiveAttestationRepository,
    GovernedReleaseLiveAuthority,
    InfrastructureDeploymentAdapter,
    ReleaseDeliveryRepository,
    SourceControlReleasePort,
)
from foundry_lite.application.dependency_release import (
    GovernedReleaseMcpAuthority as GovernedReleaseMcpAuthority,
)
from foundry_lite.application.dependency_source import SourceDependencies, SourceUploadStagingStore
from foundry_lite.application.ports import (
    AiEvalRepository,
    AiRunRepository,
    ContextProvider,
    RuntimeRepository,
)
from foundry_lite.application.ports.backup_artifact_store import BackupArtifactStore
from foundry_lite.application.ports.connector_adapter import ConnectorAdapter
from foundry_lite.application.ports.connector_registry_repository import ConnectorRegistryRepository
from foundry_lite.application.ports.erasure_repository import ErasureRepository
from foundry_lite.application.ports.insight_review_repository import InsightReviewRepository
from foundry_lite.application.ports.pipeline_dag_orchestrator import (
    PipelineDagOrchestrator,
    UnavailablePipelineDagOrchestrator,
)
from foundry_lite.application.ports.source_database_adapter import SourceDatabaseAdapter
from foundry_lite.application.ports.source_management_repository import SourceManagementRepository
from foundry_lite.application.ports.source_registry_repository import SourceRegistryRepository
from foundry_lite.application.ports.source_stream_adapter import SourceStreamAdapter
from foundry_lite.application.ports.stream_adapter import StreamAdapter
from foundry_lite.application.ports.virtual_table import VirtualTableReader, VirtualTableRepository
from foundry_lite.application.ports.workflow_adapter import WorkflowAdapter
from foundry_lite.application.runtime_profile import RuntimeProfile


@dataclass(frozen=True)
class RuntimeDependencies:
    runtime_repository: RuntimeRepository
    erasure_repository: ErasureRepository
    insight_review_repository: InsightReviewRepository
    stream_adapter: StreamAdapter
    workflow_adapter: WorkflowAdapter
    backup_artifact_store: BackupArtifactStore


@dataclass(frozen=True, init=False)
class CoreDependencies(ActionDependencyAccessors, DataDependencyAccessors, GovernedReleaseDependencyAccessors):
    """Dependencies that compose the core facade without hard-coding local infrastructure."""

    action_repository: ActionRepository
    action_branch_repository: ActionBranchRepository
    action_execution_repository: ActionExecutionRepository
    action_effect_executor: ActionEffectExecutor
    action_file_scanner: ActionFileScanner
    action_function_executor: ActionFunctionExecutor
    action_run_orchestrator: ActionRunOrchestrator
    action_notification_policy_repository: ActionNotificationPolicyRepository
    action_notification_recipient_directory: ActionNotificationRecipientDirectory
    ontology_repository: OntologyRepository
    ontology_branch_repository: OntologyBranchRepository
    pipeline_repository: PipelineRepository
    pipeline_execution_repository: PipelineExecutionRepository
    transform_repository: TransformRepository
    resource_catalog_repository: ResourceCatalogRepository
    materialization_repository: MaterializationRepository
    dataset_quality_repository: DatasetQualityRepository
    compute_adapter: ComputeAdapter
    code_execution_adapter: CodeExecutionAdapter
    dataset_repository: DatasetRepository
    dataset_transaction_repository: DatasetTransactionRepository
    dataset_version_repository: DatasetVersionRepository
    dataset_storage: DatasetStorageAdapter
    transform_source_store: TransformSourceStore
    ontology_definition_reader: OntologyDefinitionReader
    governed_release_delivery_config: ClassVar[GovernedReleaseDeliveryConfig]
    source_control_release_adapter: ClassVar[SourceControlReleasePort]
    infrastructure_deployment_adapter: ClassVar[InfrastructureDeploymentAdapter]
    release_delivery_repository: ClassVar[ReleaseDeliveryRepository]
    governed_release_live_attestation_repository: ClassVar[GovernedReleaseLiveAttestationRepository]
    governed_release_live_authority: ClassVar[GovernedReleaseLiveAuthority]
    paths: PathDependencies
    security: SecurityDependencies
    action: ActionDependencies
    data: DataDependencies
    object_store: ObjectDependencies
    runtime: RuntimeDependencies
    aip: AipDependencies
    media: MediaDependencies
    source: SourceDependencies
    pipeline_dag_orchestrator: PipelineDagOrchestrator
    profile: RuntimeProfile = RuntimeProfile()

    def __init__(
        self,
        *,
        paths: PathDependencies | None = None,
        security: SecurityDependencies | None = None,
        action: ActionDependencies | None = None,
        data: DataDependencies | None = None,
        object_store: ObjectDependencies | None = None,
        runtime: RuntimeDependencies | None = None,
        aip: AipDependencies | None = None,
        media: MediaDependencies | None = None,
        source: SourceDependencies | None = None,
        pipeline_dag_orchestrator: PipelineDagOrchestrator | None = None,
        profile: RuntimeProfile | str | None = None,
        **flat_overrides: object,
    ) -> None:
        bundles = dependency_bundles(
            paths=paths,
            security=security,
            action=action,
            data=data,
            object_store=object_store,
            runtime=runtime,
            aip=aip,
            media=media,
            source=source,
        )
        preserve_media_processor_override(flat_overrides)
        fill_missing_bundles_from_flat_overrides(bundles, flat_overrides, _CORE_DEPENDENCY_BUNDLE_TYPES)
        apply_flat_dependency_overrides(bundles, flat_overrides, _CORE_DEPENDENCY_BUNDLE_TYPES)
        assign_dependency_bundles(self, bundles)
        orchestrator = pipeline_dag_orchestrator or UnavailablePipelineDagOrchestrator()
        object.__setattr__(self, "pipeline_dag_orchestrator", orchestrator)
        object.__setattr__(self, "profile", RuntimeProfile.from_value(profile))

    @property
    def root(self) -> Path:
        return self.paths.root

    @property
    def storage_root(self) -> Path:
        return self.paths.storage_root

    @property
    def engine(self) -> TransactionManager:
        return self.security.engine

    @property
    def policy(self) -> PolicyService:
        return self.security.policy

    @property
    def metadata_repository(self) -> MetadataRepository:
        return self.security.metadata_repository

    @property
    def destructive_development_admin(self) -> DestructiveDevelopmentAdmin:
        return self.security.destructive_development_admin

    @property
    def osdk_application_repository(self) -> OsdkApplicationRepository:
        return self.security.osdk_application_repository

    @property
    def osdk_download_token_signer(self) -> OsdkDownloadTokenSigner:
        return self.security.osdk_download_token_signer

    @property
    def osdk_release_artifact_store(self) -> OsdkReleaseArtifactStore:
        return self.security.osdk_release_artifact_store

    @property
    def oauth_session_repository(self) -> OAuthSessionRepository:
        return self.security.oauth_session_repository

    @property
    def oauth_token_issuer(self) -> OAuthTokenIssuer:
        return self.security.oauth_token_issuer

    @property
    def secret_provider(self) -> SecretProvider:
        return self.security.secret_provider

    @property
    def secret_vault(self) -> SecretVault:
        return self.security.secret_vault

    @property
    def mcp_rate_limiter(self) -> McpRateLimiter:
        return self.security.mcp_rate_limiter

    @property
    def object_index_repository(self) -> ObjectIndexRepository:
        return self.object_store.object_index_repository

    @property
    def object_index_row_hash_repository(self) -> ObjectIndexRowHashRepository:
        return self.object_store.object_index_row_hash_repository

    @property
    def object_read_repository(self) -> ObjectReadRepository:
        return self.object_store.object_read_repository

    @property
    def object_set_repository(self) -> ObjectSetRepository:
        return self.object_store.object_set_repository

    @property
    def search_adapter(self) -> SearchAdapter:
        return self.object_store.search_adapter

    @property
    def runtime_repository(self) -> RuntimeRepository:
        return self.runtime.runtime_repository

    @property
    def erasure_repository(self) -> ErasureRepository:
        return self.runtime.erasure_repository

    @property
    def insight_review_repository(self) -> InsightReviewRepository:
        return self.runtime.insight_review_repository

    @property
    def stream_adapter(self) -> StreamAdapter:
        return self.runtime.stream_adapter

    @property
    def source_stream_adapter(self) -> SourceStreamAdapter:
        return self.source.source_stream_adapter

    @property
    def workflow_adapter(self) -> WorkflowAdapter:
        return self.runtime.workflow_adapter

    @property
    def backup_artifact_store(self) -> BackupArtifactStore:
        return self.runtime.backup_artifact_store

    @property
    def ai_eval_repository(self) -> AiEvalRepository:
        return self.aip.ai_eval_repository

    @property
    def ai_run_repository(self) -> AiRunRepository:
        return self.aip.ai_run_repository

    def _governed_release_dependencies(self) -> GovernedReleaseDependencies:
        return self.aip.governed_release

    @property
    def embedding_model_adapter(self) -> EmbeddingModelAdapter:
        return self.aip.embedding_model_adapter

    @property
    def completion_model_adapter(self) -> CompletionModelAdapter:
        return self.aip.completion_model_adapter

    @property
    def vision_embedding_model_adapter(self) -> VisionEmbeddingModelAdapter:
        return self.aip.vision_embedding_model_adapter

    @property
    def language_model_adapter(self) -> LanguageModelAdapter:
        return self.aip.language_model_adapter

    @property
    def governed_semantic_model_port(self) -> GovernedSemanticModelPort:
        return required_dependency(self.aip.governed_semantic_model_port, "governed semantic model port unavailable")

    @property
    def trained_model_inference_port(self) -> TrainedModelInferencePort:
        return required_dependency(self.aip.trained_model_inference_port, "trained model inference port unavailable")

    @property
    def model_registry_repository(self) -> ModelRegistryRepository:
        return self.aip.model_registry_repository

    @property
    def semantic_row_cache_repository(self) -> SemanticRowCacheRepository:
        return self.aip.semantic_row_cache_repository

    @property
    def context_provider(self) -> ContextProvider:
        return self.aip.context_provider

    @property
    def prompt_artifact_store(self) -> object:
        return self.aip.prompt_artifact_store

    @property
    def citation_source_verifier(self) -> CitationSourceVerifier:
        return self.aip.citation_source_verifier

    @property
    def tool_executor(self) -> ToolExecutor:
        return self.aip.tool_executor

    @property
    def media_repository(self) -> MediaRepository:
        return self.media.media_repository

    @property
    def media_derivative_repository(self) -> MediaDerivativeRepository:
        return self.media.media_derivative_repository

    @property
    def media_reference_binding_repository(self) -> MediaReferenceBindingRepository:
        return self.media.media_reference_binding_repository

    @property
    def media_access_cache_repository(self) -> MediaAccessCacheRepository:
        return self.media.media_access_cache_repository

    @property
    def media_storage(self) -> MediaStorageAdapter:
        return self.media.media_storage

    @property
    def media_source_workspace(self) -> MediaSourceWorkspace:
        return self.media.media_source_workspace

    @property
    def media_processor(self) -> MediaProcessorAdapter:
        return self.media.media_processor

    @property
    def media_processor_registry(self) -> MediaProcessorRegistry | None:
        return self.media.media_processor_registry

    @property
    def media_preview_renderer(self) -> MediaPreviewRendererAdapter:
        return self.media.media_preview_renderer

    @property
    def external_media_reader(self) -> ExternalMediaReader:
        return self.media.external_media_reader

    @property
    def content_index_adapter(self) -> ContentIndexAdapter:
        return self.media.content_index_adapter

    @property
    def connector_adapter(self) -> ConnectorAdapter:
        return self.source.connector_adapter

    @property
    def connector_registry_repository(self) -> ConnectorRegistryRepository:
        return self.source.connector_registry_repository

    @property
    def source_registry_repository(self) -> SourceRegistryRepository:
        return self.source.source_registry_repository

    @property
    def source_upload_staging_store(self) -> SourceUploadStagingStore:
        return self.source.source_upload_staging_store

    @property
    def source_management_repository(self) -> SourceManagementRepository:
        return self.source.source_management_repository

    @property
    def source_database_adapter(self) -> SourceDatabaseAdapter:
        return self.source.source_database_adapter

    @property
    def virtual_table_repository(self) -> VirtualTableRepository:
        return self.source.virtual_table_repository

    @property
    def virtual_table_reader(self) -> VirtualTableReader:
        return self.source.virtual_table_reader


_CORE_DEPENDENCY_BUNDLE_TYPES: dict[str, BundleFactory] = {
    "paths": cast(BundleFactory, PathDependencies),
    "security": cast(BundleFactory, SecurityDependencies),
    "action": cast(BundleFactory, ActionDependencies),
    "data": cast(BundleFactory, DataDependencies),
    "object_store": cast(BundleFactory, ObjectDependencies),
    "runtime": cast(BundleFactory, RuntimeDependencies),
    "aip": cast(BundleFactory, AipDependencies),
    "media": cast(BundleFactory, MediaDependencies),
    "source": cast(BundleFactory, SourceDependencies),
}
