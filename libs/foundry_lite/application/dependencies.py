"""Application-layer models and helpers for dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from foundry_lite.application.dependency_aip import AipDependencies
from foundry_lite.application.dependency_compat import (
    BundleFactory,
    apply_flat_dependency_overrides,
    assign_dependency_bundles,
    dependency_bundles,
    fill_missing_bundles_from_flat_overrides,
    preserve_media_processor_override,
    required_dependency,
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
    MediaStorageAdapter,
)
from foundry_lite.application.ports import (
    ActionRepository,
    AiEvalRepository,
    AiRunRepository,
    ComputeAdapter,
    ContextProvider,
    DatasetQualityRepository,
    DatasetRepository,
    DatasetStorageAdapter,
    DatasetTransactionRepository,
    DatasetVersionRepository,
    MaterializationRepository,
    MetadataRepository,
    ObjectIndexRepository,
    ObjectIndexRowHashRepository,
    ObjectReadRepository,
    ObjectSetRepository,
    OntologyRepository,
    PipelineRepository,
    ResourceCatalogRepository,
    RuntimeRepository,
    TransactionManager,
    TransformRepository,
)
from foundry_lite.application.ports.backup_artifact_store import BackupArtifactStore
from foundry_lite.application.ports.citation_source import CitationSourceVerifier
from foundry_lite.application.ports.completion_model import CompletionModelAdapter
from foundry_lite.application.ports.connector_adapter import ConnectorAdapter
from foundry_lite.application.ports.connector_registry_repository import ConnectorRegistryRepository
from foundry_lite.application.ports.destructive_development_admin import DestructiveDevelopmentAdmin
from foundry_lite.application.ports.embedding_model import EmbeddingModelAdapter
from foundry_lite.application.ports.erasure_repository import ErasureRepository
from foundry_lite.application.ports.insight_review_repository import InsightReviewRepository
from foundry_lite.application.ports.language_model import GovernedSemanticModelPort, LanguageModelAdapter
from foundry_lite.application.ports.model_registry_repository import ModelRegistryRepository
from foundry_lite.application.ports.oauth_session_repository import OAuthSessionRepository, OAuthTokenIssuer
from foundry_lite.application.ports.ontology_branch_repository import OntologyBranchRepository
from foundry_lite.application.ports.osdk_application_repository import OsdkApplicationRepository
from foundry_lite.application.ports.pipeline_dag_orchestrator import (
    PipelineDagOrchestrator,
    UnavailablePipelineDagOrchestrator,
)
from foundry_lite.application.ports.pipeline_execution_repository import PipelineExecutionRepository
from foundry_lite.application.ports.search_adapter import SearchAdapter
from foundry_lite.application.ports.secret_provider import SecretProvider, SecretVault
from foundry_lite.application.ports.semantic_row_cache_repository import SemanticRowCacheRepository
from foundry_lite.application.ports.source_database_adapter import SourceDatabaseAdapter
from foundry_lite.application.ports.source_management_repository import SourceManagementRepository
from foundry_lite.application.ports.source_registry_repository import SourceRegistryRepository
from foundry_lite.application.ports.source_stream_adapter import SourceStreamAdapter
from foundry_lite.application.ports.stream_adapter import StreamAdapter
from foundry_lite.application.ports.tool_executor import ToolExecutor
from foundry_lite.application.ports.trained_model_inference import TrainedModelInferencePort
from foundry_lite.application.ports.vision_embedding_model import VisionEmbeddingModelAdapter
from foundry_lite.application.ports.workflow_adapter import WorkflowAdapter
from foundry_lite.application.runtime_profile import RuntimeProfile
from foundry_lite.security.policy import PolicyService


@dataclass(frozen=True)
class PathDependencies:
    root: Path
    storage_root: Path


@dataclass(frozen=True)
class SecurityDependencies:
    engine: TransactionManager
    policy: PolicyService
    metadata_repository: MetadataRepository
    destructive_development_admin: DestructiveDevelopmentAdmin
    osdk_application_repository: OsdkApplicationRepository
    oauth_session_repository: OAuthSessionRepository
    oauth_token_issuer: OAuthTokenIssuer
    secret_provider: SecretProvider
    secret_vault: SecretVault


@dataclass(frozen=True)
class ActionDependencies:
    action_repository: ActionRepository


@dataclass(frozen=True)
class DataDependencies:
    ontology_repository: OntologyRepository
    ontology_branch_repository: OntologyBranchRepository
    pipeline_repository: PipelineRepository
    pipeline_execution_repository: PipelineExecutionRepository
    resource_catalog_repository: ResourceCatalogRepository
    transform_repository: TransformRepository
    materialization_repository: MaterializationRepository
    dataset_quality_repository: DatasetQualityRepository
    compute_adapter: ComputeAdapter
    dataset_repository: DatasetRepository
    dataset_transaction_repository: DatasetTransactionRepository
    dataset_version_repository: DatasetVersionRepository
    dataset_storage: DatasetStorageAdapter


@dataclass(frozen=True)
class ObjectDependencies:
    object_index_repository: ObjectIndexRepository
    object_index_row_hash_repository: ObjectIndexRowHashRepository
    object_read_repository: ObjectReadRepository
    object_set_repository: ObjectSetRepository
    search_adapter: SearchAdapter


@dataclass(frozen=True)
class RuntimeDependencies:
    runtime_repository: RuntimeRepository
    erasure_repository: ErasureRepository
    insight_review_repository: InsightReviewRepository
    stream_adapter: StreamAdapter
    workflow_adapter: WorkflowAdapter
    backup_artifact_store: BackupArtifactStore


@dataclass(frozen=True)
class SourceDependencies:
    connector_adapter: ConnectorAdapter
    connector_registry_repository: ConnectorRegistryRepository
    source_registry_repository: SourceRegistryRepository
    source_management_repository: SourceManagementRepository
    source_database_adapter: SourceDatabaseAdapter
    source_stream_adapter: SourceStreamAdapter


@dataclass(frozen=True, init=False)
class CoreDependencies:
    """Dependencies that compose the core facade without hard-coding local infrastructure."""

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
        object.__setattr__(
            self,
            "pipeline_dag_orchestrator",
            pipeline_dag_orchestrator or UnavailablePipelineDagOrchestrator(),
        )
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
    def action_repository(self) -> ActionRepository:
        return self.action.action_repository

    @property
    def ontology_repository(self) -> OntologyRepository:
        return self.data.ontology_repository

    @property
    def ontology_branch_repository(self) -> OntologyBranchRepository:
        return self.data.ontology_branch_repository

    @property
    def pipeline_repository(self) -> PipelineRepository:
        return self.data.pipeline_repository

    @property
    def pipeline_execution_repository(self) -> PipelineExecutionRepository:
        return self.data.pipeline_execution_repository

    @property
    def transform_repository(self) -> TransformRepository:
        return self.data.transform_repository

    @property
    def resource_catalog_repository(self) -> ResourceCatalogRepository:
        return self.data.resource_catalog_repository

    @property
    def materialization_repository(self) -> MaterializationRepository:
        return self.data.materialization_repository

    @property
    def dataset_quality_repository(self) -> DatasetQualityRepository:
        return self.data.dataset_quality_repository

    @property
    def compute_adapter(self) -> ComputeAdapter:
        return self.data.compute_adapter

    @property
    def dataset_repository(self) -> DatasetRepository:
        return self.data.dataset_repository

    @property
    def dataset_transaction_repository(self) -> DatasetTransactionRepository:
        return self.data.dataset_transaction_repository

    @property
    def dataset_version_repository(self) -> DatasetVersionRepository:
        return self.data.dataset_version_repository

    @property
    def dataset_storage(self) -> DatasetStorageAdapter:
        return self.data.dataset_storage

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
    def source_management_repository(self) -> SourceManagementRepository:
        return self.source.source_management_repository

    @property
    def source_database_adapter(self) -> SourceDatabaseAdapter:
        return self.source.source_database_adapter


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
