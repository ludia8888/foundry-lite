from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from foundry_lite.application.ports import (
    ActionRepository,
    AiRunRepository,
    ComputeAdapter,
    DatasetQualityRepository,
    DatasetRepository,
    DatasetStorageAdapter,
    DatasetTransactionRepository,
    DatasetVersionRepository,
    MaterializationRepository,
    MetadataRepository,
    ObjectIndexRepository,
    ObjectReadRepository,
    ObjectSetRepository,
    OntologyRepository,
    RuntimeRepository,
    TransactionManager,
    TransformRepository,
)
from foundry_lite.application.ports.citation_source import CitationSourceVerifier
from foundry_lite.application.ports.completion_model import CompletionModelAdapter
from foundry_lite.application.ports.connector_adapter import ConnectorAdapter
from foundry_lite.application.ports.content_index import ContentIndexAdapter
from foundry_lite.application.ports.destructive_development_admin import DestructiveDevelopmentAdmin
from foundry_lite.application.ports.embedding_model import EmbeddingModelAdapter
from foundry_lite.application.ports.erasure_repository import ErasureRepository
from foundry_lite.application.ports.external_media_reader import ExternalMediaReader
from foundry_lite.application.ports.insight_review_repository import InsightReviewRepository
from foundry_lite.application.ports.language_model import LanguageModelAdapter
from foundry_lite.application.ports.media_access_cache_repository import MediaAccessCacheRepository
from foundry_lite.application.ports.media_derivative_repository import MediaDerivativeRepository
from foundry_lite.application.ports.media_preview_renderer import MediaPreviewRendererAdapter
from foundry_lite.application.ports.media_processor import MediaProcessorAdapter
from foundry_lite.application.ports.media_reference_binding_repository import MediaReferenceBindingRepository
from foundry_lite.application.ports.media_repository import MediaRepository
from foundry_lite.application.ports.media_storage import MediaStorageAdapter
from foundry_lite.application.ports.model_registry_repository import ModelRegistryRepository
from foundry_lite.application.ports.search_adapter import SearchAdapter
from foundry_lite.application.ports.secret_provider import SecretProvider
from foundry_lite.application.ports.stream_adapter import StreamAdapter
from foundry_lite.application.ports.tool_executor import ToolExecutor
from foundry_lite.application.ports.vision_embedding_model import VisionEmbeddingModelAdapter
from foundry_lite.application.ports.workflow_adapter import WorkflowAdapter
from foundry_lite.security.policy import PolicyService


@dataclass(frozen=True)
class CoreDependencies:
    """Dependencies that compose the core facade without hard-coding local infrastructure."""

    root: Path
    storage_root: Path
    engine: TransactionManager
    policy: PolicyService
    action_repository: ActionRepository
    ai_run_repository: AiRunRepository
    ontology_repository: OntologyRepository
    transform_repository: TransformRepository
    materialization_repository: MaterializationRepository
    dataset_quality_repository: DatasetQualityRepository
    compute_adapter: ComputeAdapter
    connector_adapter: ConnectorAdapter
    metadata_repository: MetadataRepository
    destructive_development_admin: DestructiveDevelopmentAdmin
    dataset_repository: DatasetRepository
    dataset_transaction_repository: DatasetTransactionRepository
    dataset_version_repository: DatasetVersionRepository
    insight_review_repository: InsightReviewRepository
    object_index_repository: ObjectIndexRepository
    object_read_repository: ObjectReadRepository
    object_set_repository: ObjectSetRepository
    runtime_repository: RuntimeRepository
    erasure_repository: ErasureRepository
    media_repository: MediaRepository
    media_derivative_repository: MediaDerivativeRepository
    media_reference_binding_repository: MediaReferenceBindingRepository
    media_access_cache_repository: MediaAccessCacheRepository
    dataset_storage: DatasetStorageAdapter
    media_storage: MediaStorageAdapter
    media_processor: MediaProcessorAdapter
    media_preview_renderer: MediaPreviewRendererAdapter
    external_media_reader: ExternalMediaReader
    content_index_adapter: ContentIndexAdapter
    embedding_model_adapter: EmbeddingModelAdapter
    completion_model_adapter: CompletionModelAdapter
    vision_embedding_model_adapter: VisionEmbeddingModelAdapter
    language_model_adapter: LanguageModelAdapter
    model_registry_repository: ModelRegistryRepository
    search_adapter: SearchAdapter
    secret_provider: SecretProvider
    citation_source_verifier: CitationSourceVerifier
    stream_adapter: StreamAdapter
    tool_executor: ToolExecutor
    workflow_adapter: WorkflowAdapter
