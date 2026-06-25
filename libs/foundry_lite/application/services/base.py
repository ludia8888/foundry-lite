from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.ports import (
    ActionRepository,
    ComputeAdapter,
    ConnectorAdapter,
    DatasetQualityRepository,
    DatasetRepository,
    DatasetStorageAdapter,
    DatasetTransactionRepository,
    DatasetVersionRepository,
    MaterializationRepository,
    ObjectIndexRepository,
    ObjectReadRepository,
    ObjectSetRepository,
    OntologyRepository,
    RuntimeRepository,
    StreamAdapter,
    TransactionManager,
    TransformRepository,
    WorkflowAdapter,
)
from foundry_lite.application.ports.completion_model import CompletionModelAdapter
from foundry_lite.application.ports.content_index import ContentIndexAdapter
from foundry_lite.application.ports.embedding_model import EmbeddingModelAdapter
from foundry_lite.application.ports.erasure_repository import ErasureRepository
from foundry_lite.application.ports.external_media_reader import ExternalMediaReader
from foundry_lite.application.ports.insight_review_repository import InsightReviewRepository
from foundry_lite.application.ports.media_access_cache_repository import MediaAccessCacheRepository
from foundry_lite.application.ports.media_derivative_repository import MediaDerivativeRepository
from foundry_lite.application.ports.media_preview_renderer import MediaPreviewRendererAdapter
from foundry_lite.application.ports.media_processor import MediaProcessorAdapter
from foundry_lite.application.ports.media_reference_binding_repository import MediaReferenceBindingRepository
from foundry_lite.application.ports.media_repository import MediaRepository
from foundry_lite.application.ports.media_storage import MediaStorageAdapter
from foundry_lite.application.ports.search_adapter import SearchAdapter
from foundry_lite.application.ports.vision_embedding_model import VisionEmbeddingModelAdapter
from foundry_lite.observability.tracing import trace_direct_public_methods
from foundry_lite.security.policy import PolicyService

CollaboratorMap = Mapping[str, object]

SERVICE_COLLABORATORS: Mapping[str, str] = {
    "action_service": "ActionService",
    "backup_restore_service": "BackupRestoreService",
    "content_retrieval_service": "DefaultContentRetrievalService",
    "media_visual_search_service": "MediaVisualSearchService",
    "dataset_ingest_service": "DatasetIngestService",
    "dataset_quality_service": "DatasetQualityService",
    "dataset_registry_service": "DatasetRegistryService",
    "dataset_transaction_service": "DatasetTransactionService",
    "dataset_version_service": "DatasetVersionService",
    "demo_service": "DemoService",
    "iceberg_maintenance_service": "IcebergMaintenanceService",
    "materialization_service": "MaterializationService",
    "object_indexing_service": "ObjectIndexingService",
    "object_links_service": "ObjectLinksService",
    "object_query_service": "ObjectQueryService",
    "object_records_service": "ObjectRecordsService",
    "object_search_service": "ObjectSearchService",
    "object_sets_service": "ObjectSetsService",
    "ontology_service": "OntologyService",
    "record_dlq_service": "RecordDlqService",
    "runtime_service": "RuntimeService",
    "transform_service": "TransformService",
    "workflow_orchestration_service": "WorkflowOrchestrationService",
}


class CoreService:
    """Base class for constructor-injected application services.

    Each concrete service declares only the infrastructure dependencies it
    directly uses through ``required_dependencies`` and only the service
    collaborators it directly calls through ``required_collaborators``.

    This keeps ``FoundryLite`` as a thin facade rather than a multiple
    inheritance host, while making service coupling visible in code.
    """

    required_dependencies: ClassVar[tuple[str, ...]] = ()
    required_collaborators: ClassVar[tuple[str, ...]] = ()

    root: Path
    storage_root: Path
    compute_adapter: ComputeAdapter
    connector_adapter: ConnectorAdapter
    dataset_repository: DatasetRepository
    dataset_transaction_repository: DatasetTransactionRepository
    dataset_version_repository: DatasetVersionRepository
    insight_review_repository: InsightReviewRepository
    object_index_repository: ObjectIndexRepository
    object_read_repository: ObjectReadRepository
    object_set_repository: ObjectSetRepository
    runtime_repository: RuntimeRepository
    erasure_repository: ErasureRepository
    search_adapter: SearchAdapter
    stream_adapter: StreamAdapter
    workflow_adapter: WorkflowAdapter
    dataset_storage: DatasetStorageAdapter
    media_repository: MediaRepository
    media_derivative_repository: MediaDerivativeRepository
    media_reference_binding_repository: MediaReferenceBindingRepository
    media_access_cache_repository: MediaAccessCacheRepository
    media_storage: MediaStorageAdapter
    media_processor: MediaProcessorAdapter
    media_preview_renderer: MediaPreviewRendererAdapter
    external_media_reader: ExternalMediaReader
    content_index_adapter: ContentIndexAdapter
    embedding_model_adapter: EmbeddingModelAdapter
    completion_model_adapter: CompletionModelAdapter
    vision_embedding_model_adapter: VisionEmbeddingModelAdapter
    engine: TransactionManager
    policy: PolicyService
    action_repository: ActionRepository
    ontology_repository: OntologyRepository
    transform_repository: TransformRepository
    materialization_repository: MaterializationRepository
    dataset_quality_repository: DatasetQualityRepository

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        trace_direct_public_methods(cls)

    def __init__(self, **dependencies: object) -> None:
        expected = set(self.required_dependencies)
        provided = set(dependencies)
        missing = sorted(expected - provided)
        unexpected = sorted(provided - expected)
        if missing or unexpected:
            raise TypeError(
                f"{self.__class__.__name__} dependency mismatch: missing={missing}, unexpected={unexpected}"
            )
        for name, value in dependencies.items():
            setattr(self, name, value)

    def bind_collaborators(self, collaborators: CollaboratorMap) -> None:
        expected = set(self.required_collaborators)
        provided = set(collaborators)
        missing = sorted(expected - provided)
        unexpected = sorted(provided - expected)
        if missing or unexpected:
            raise TypeError(
                f"{self.__class__.__name__} collaborator mismatch: missing={missing}, unexpected={unexpected}"
            )
        for name, collaborator in collaborators.items():
            setattr(self, name, collaborator)


def dependency_kwargs(service_type: type[CoreService], dependencies: CoreDependencies) -> dict[str, object]:
    return {name: getattr(dependencies, name) for name in service_type.required_dependencies}


def build_service[ServiceT: CoreService](service_type: type[ServiceT], dependencies: CoreDependencies) -> ServiceT:
    return service_type(**dependency_kwargs(service_type, dependencies))


def collaborator_kwargs(service: CoreService, collaborators: CollaboratorMap) -> dict[str, object]:
    return {name: collaborators[name] for name in service.required_collaborators}
