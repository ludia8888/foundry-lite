"""Application service helpers for base workflows."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.ports import (
    ActionRepository,
    AiEvalRepository,
    AiRunRepository,
    ComputeAdapter,
    ConnectorAdapter,
    ConnectorRegistryRepository,
    DatasetQualityRepository,
    DatasetRepository,
    DatasetStorageAdapter,
    DatasetTransactionRepository,
    DatasetVersionRepository,
    MaterializationRepository,
    ObjectIndexRepository,
    ObjectIndexRowHashRepository,
    ObjectReadRepository,
    ObjectSetRepository,
    OntologyRepository,
    RuntimeRepository,
    StreamAdapter,
    TransactionManager,
    TransformRepository,
    WorkflowAdapter,
)
from foundry_lite.application.ports.backup_artifact_store import BackupArtifactStore
from foundry_lite.application.ports.citation_source import CitationSourceVerifier
from foundry_lite.application.ports.completion_model import CompletionModelAdapter
from foundry_lite.application.ports.content_index import ContentIndexAdapter
from foundry_lite.application.ports.context_provider import ContextProvider
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
from foundry_lite.application.ports.oauth_session_repository import OAuthSessionRepository, OAuthTokenIssuer
from foundry_lite.application.ports.ontology_branch_repository import OntologyBranchRepository
from foundry_lite.application.ports.osdk_application_repository import OsdkApplicationRepository
from foundry_lite.application.ports.pipeline_repository import PipelineRepository
from foundry_lite.application.ports.search_adapter import SearchAdapter
from foundry_lite.application.ports.secret_provider import SecretProvider, SecretVault
from foundry_lite.application.ports.source_database_adapter import SourceDatabaseAdapter
from foundry_lite.application.ports.source_management_repository import SourceManagementRepository
from foundry_lite.application.ports.source_registry_repository import SourceRegistryRepository
from foundry_lite.application.ports.tool_executor import ToolExecutor
from foundry_lite.application.ports.vision_embedding_model import VisionEmbeddingModelAdapter
from foundry_lite.observability.tracing import trace_direct_public_methods
from foundry_lite.security.policy import PolicyService
from foundry_lite.security.tenant_context import bind_tenant_context_public_methods

CollaboratorMap = Mapping[str, object]

SERVICE_COLLABORATORS: Mapping[str, str] = {
    "action_apply_service": "ActionApplyService",
    "action_batch_apply_service": "ActionBatchApplyService",
    "action_service": "ActionService",
    "action_validation_service": "ActionValidationService",
    "action_writeback_service": "ActionWritebackService",
    "agent_runtime_service": "AgentRuntimeService",
    "action_proposal_service": "ActionProposalService",
    "approval_execution_service": "ApprovalExecutionService",
    "backup_restore_artifact_execution_service": "BackupRestoreArtifactExecutionService",
    "backup_restore_artifact_restore_service": "BackupRestoreArtifactRestoreService",
    "backup_restore_artifact_service": "BackupRestoreArtifactService",
    "backup_restore_mode_service": "BackupRestoreModeService",
    "backup_restore_preflight_service": "BackupRestorePreflightService",
    "backup_restore_service": "BackupRestoreService",
    "backup_restore_validation_service": "BackupRestoreValidationService",
    "builder_runtime_service": "BuilderRuntimeService",
    "citation_service": "CitationService",
    "connector_onboarding_service": "ConnectorOnboardingService",
    "source_management_service": "SourceManagementService",
    "source_onboarding_service": "SourceOnboardingService",
    "context_compiler_service": "ContextCompilerService",
    "content_retrieval_service": "DefaultContentRetrievalService",
    "media_visual_search_service": "MediaVisualSearchService",
    "media_transaction_service": "MediaTransactionService",
    "media_upload_service": "MediaUploadService",
    "dataset_ingest_service": "DatasetIngestService",
    "dataset_quality_api_service": "DatasetQualityApiService",
    "dataset_quality_contract_service": "DatasetQualityContractService",
    "dataset_quality_runtime_service": "DatasetQualityRuntimeService",
    "dataset_quality_service": "DatasetQualityRuntimeService",
    "dataset_registry_service": "DatasetRegistryService",
    "dataset_transaction_service": "DatasetTransactionService",
    "dataset_version_service": "DatasetVersionService",
    "demo_service": "DemoService",
    "function_execution_service": "FunctionExecutionService",
    "iceberg_maintenance_service": "IcebergMaintenanceService",
    "insight_review_service": "InsightReviewService",
    "logic_runtime_service": "LogicRuntimeService",
    "model_gateway_service": "ModelGatewayService",
    "prompt_artifact_service": "PromptArtifactService",
    "materialization_service": "MaterializationService",
    "object_cdc_indexing_service": "ObjectCdcIndexingService",
    "object_indexing_service": "ObjectIndexingService",
    "object_index_rebuild_service": "ObjectIndexRebuildService",
    "object_index_record_mutation_service": "ObjectIndexRecordMutationService",
    "object_index_shadow_service": "ObjectIndexShadowService",
    "object_link_indexing_service": "ObjectLinkIndexingService",
    "object_links_service": "ObjectLinksService",
    "object_ontology_reindex_service": "ObjectOntologyReindexService",
    "object_query_service": "ObjectQueryService",
    "object_records_service": "ObjectRecordsService",
    "object_search_service": "ObjectSearchService",
    "object_sets_service": "ObjectSetsService",
    "object_subscription_service": "ObjectSubscriptionService",
    "osdk_application_client_service": "OsdkApplicationClientService",
    "osdk_application_idempotency_service": "OsdkApplicationIdempotencyService",
    "osdk_application_scope_service": "OsdkApplicationScopeService",
    "ontology_activation_service": "OntologyActivationService",
    "ontology_branch_service": "OntologyBranchService",
    "ontology_catalog_service": "OntologyCatalogService",
    "ontology_insights_service": "OntologyInsightsService",
    "ontology_lookup_service": "OntologyLookupService",
    "ontology_proposal_service": "OntologyProposalService",
    "ontology_reindex_contract_service": "OntologyReindexContractService",
    "ontology_service": "OntologyService",
    "osdk_application_service": "OsdkApplicationService",
    "osdk_application_sdk_service": "OsdkApplicationSdkService",
    "osdk_oauth_session_service": "OsdkOAuthSessionService",
    "outbox_publisher_service": "OutboxPublisherService",
    "pipeline_compiler_service": "PipelineCompilerService",
    "pipeline_definition_service": "PipelineDefinitionService",
    "pipeline_governance_service": "PipelineGovernanceService",
    "pipeline_graph_validation_service": "PipelineGraphValidationService",
    "pipeline_run_service": "PipelineRunService",
    "record_dlq_service": "RecordDlqService",
    "runtime_service": "RuntimeService",
    "tool_broker_service": "ToolBrokerService",
    "transform_definition_service": "TransformDefinitionService",
    "transform_dlq_replay_service": "TransformDlqReplayService",
    "transform_graph_service": "TransformGraphService",
    "transform_run_service": "TransformRunService",
    "transform_scheduler_service": "TransformSchedulerService",
    "transform_service": "TransformService",
    "visual_builder_service": "VisualBuilderService",
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
    backup_artifact_store: BackupArtifactStore
    compute_adapter: ComputeAdapter
    connector_adapter: ConnectorAdapter
    connector_registry_repository: ConnectorRegistryRepository
    source_management_repository: SourceManagementRepository
    source_registry_repository: SourceRegistryRepository
    dataset_repository: DatasetRepository
    dataset_transaction_repository: DatasetTransactionRepository
    dataset_version_repository: DatasetVersionRepository
    insight_review_repository: InsightReviewRepository
    object_index_repository: ObjectIndexRepository
    object_index_row_hash_repository: ObjectIndexRowHashRepository
    object_read_repository: ObjectReadRepository
    object_set_repository: ObjectSetRepository
    osdk_application_repository: OsdkApplicationRepository
    oauth_session_repository: OAuthSessionRepository
    oauth_token_issuer: OAuthTokenIssuer
    runtime_repository: RuntimeRepository
    erasure_repository: ErasureRepository
    search_adapter: SearchAdapter
    secret_provider: SecretProvider
    secret_vault: SecretVault
    source_database_adapter: SourceDatabaseAdapter
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
    language_model_adapter: LanguageModelAdapter
    model_registry_repository: ModelRegistryRepository
    context_provider: ContextProvider
    citation_source_verifier: CitationSourceVerifier
    prompt_artifact_store: object
    tool_executor: ToolExecutor
    engine: TransactionManager
    policy: PolicyService
    insight_review_service: object
    action_repository: ActionRepository
    ai_eval_repository: AiEvalRepository
    ai_run_repository: AiRunRepository
    ontology_repository: OntologyRepository
    ontology_branch_repository: OntologyBranchRepository
    pipeline_repository: PipelineRepository
    transform_repository: TransformRepository
    materialization_repository: MaterializationRepository
    dataset_quality_repository: DatasetQualityRepository

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        bind_tenant_context_public_methods(cls, include_inherited=False)
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
