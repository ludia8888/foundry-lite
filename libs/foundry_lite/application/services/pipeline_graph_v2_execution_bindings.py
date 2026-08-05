"""Explicit dependencies consumed by the Pipeline Graph v2 execution boundary."""

from dataclasses import dataclass

from foundry_lite.application.pipeline_runtime_dependencies import (
    GovernedSemanticModelPort,
    MediaProcessorRegistry,
    PipelineExecutionRepository,
    SecretVault,
    SemanticRowCacheRepository,
    VirtualTableReader,
    VirtualTableRepository,
)
from foundry_lite.application.ports import (
    DatasetRepository,
    DatasetVersionRepository,
    PipelineExecutionLeaseFence,
    TransactionManager,
)
from foundry_lite.application.ports.embedding_model import EmbeddingModelAdapter
from foundry_lite.application.ports.trained_model_inference import TrainedModelInferencePort
from foundry_lite.application.services.pipeline_media_output_port_types import (
    MediaDerivativeRepository,
    MediaRepository,
    MediaStorageAdapter,
)
from foundry_lite.application.services.pipeline_media_output_service_types import (
    MediaCatalogService,
    MediaTransactionService,
    MediaUploadService,
)
from foundry_lite.application.services.pipeline_v2_runtime_dataset import (
    ExactDatasetVersionReader,
)
from foundry_lite.application.services.pipeline_v2_runtime_media import (
    PipelineV2ContentChunking,
    PipelineV2MediaIndexing,
    PipelineV2MediaProcessing,
)
from foundry_lite.application.services.pipeline_v2_runtime_rows import (
    PipelineV2DatasetIngest,
    PipelineV2DatasetRegistry,
)
from foundry_lite.application.services.runtime_evidence_boundary import (
    RuntimeEvidenceBoundary,
)
from foundry_lite.security.policy import PolicyService

__all__ = [
    "ExactDatasetVersionReader",
    "MediaCatalogService",
    "MediaTransactionService",
    "MediaUploadService",
    "PipelineGraphV2ExecutionBindings",
    "PipelineV2ContentChunking",
    "PipelineV2DatasetIngest",
    "PipelineV2DatasetRegistry",
    "PipelineV2MediaIndexing",
    "PipelineV2MediaProcessing",
    "RuntimeEvidenceBoundary",
]


@dataclass(frozen=True, slots=True)
class PipelineGraphV2ExecutionBindings:
    engine: TransactionManager
    policy: PolicyService
    pipeline_execution_repository: PipelineExecutionRepository
    dataset_repository: DatasetRepository
    dataset_version_repository: DatasetVersionRepository
    media_repository: MediaRepository
    media_derivative_repository: MediaDerivativeRepository
    media_storage: MediaStorageAdapter
    media_processor_registry: MediaProcessorRegistry | None
    embedding_model_adapter: EmbeddingModelAdapter
    governed_semantic_model_port: GovernedSemanticModelPort
    trained_model_inference_port: TrainedModelInferencePort
    semantic_row_cache_repository: SemanticRowCacheRepository
    virtual_table_repository: VirtualTableRepository
    virtual_table_reader: VirtualTableReader
    secret_vault: SecretVault
    content_unit_chunking_service: PipelineV2ContentChunking
    dataset_ingest_service: PipelineV2DatasetIngest
    dataset_registry_service: PipelineV2DatasetRegistry
    exact_dataset_version_reader_service: ExactDatasetVersionReader
    media_indexing_service: PipelineV2MediaIndexing
    media_catalog_service: MediaCatalogService
    media_processing_service: PipelineV2MediaProcessing
    media_transaction_service: MediaTransactionService
    media_upload_service: MediaUploadService
    runtime_service: RuntimeEvidenceBoundary
    execution_lease_guard: PipelineExecutionLeaseFence
