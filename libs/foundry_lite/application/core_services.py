"""Application-layer models and helpers for core services."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TypedDict

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.model_gateway_bridge import GovernedSemanticModelBridge
from foundry_lite.application.services.action_effect_delivery_service import ActionEffectDeliveryService
from foundry_lite.application.services.action_services import ActionServices
from foundry_lite.application.services.aip.action_proposal import ActionProposalService
from foundry_lite.application.services.aip.agent_runtime import AgentRuntimeService
from foundry_lite.application.services.aip.approval_execution import ApprovalExecutionService
from foundry_lite.application.services.aip.builder_runtime import BuilderRuntimeService
from foundry_lite.application.services.aip.citation_service import CitationService
from foundry_lite.application.services.aip.context_compiler import ContextCompilerService
from foundry_lite.application.services.aip.eval_service import EvalService
from foundry_lite.application.services.aip.logic_runtime import LogicRuntimeService
from foundry_lite.application.services.aip.model_gateway import ModelGatewayService
from foundry_lite.application.services.aip.prompt_artifact_service import PromptArtifactService
from foundry_lite.application.services.aip.tool_broker import ToolBrokerService
from foundry_lite.application.services.aip.visual_builder import VisualBuilderService
from foundry_lite.application.services.backup_restore_services import BackupRestoreServices
from foundry_lite.application.services.base import CoreService, build_service, collaborator_kwargs
from foundry_lite.application.services.connector_onboarding_service import ConnectorOnboardingService
from foundry_lite.application.services.dataset_service import DatasetServices
from foundry_lite.application.services.demo_service import DemoService
from foundry_lite.application.services.function_execution_service import FunctionExecutionService
from foundry_lite.application.services.materialization_service import MaterializationService
from foundry_lite.application.services.media_service import MediaServices
from foundry_lite.application.services.object_service import ObjectServices
from foundry_lite.application.services.ontology_search import OntologySearchService
from foundry_lite.application.services.ontology_service import OntologyService
from foundry_lite.application.services.ontology_services import OntologyServices
from foundry_lite.application.services.osdk_application_service import OsdkApplicationService
from foundry_lite.application.services.osdk_application_services import OsdkApplicationServices
from foundry_lite.application.services.osdk_oauth_session_service import OsdkOAuthSessionService
from foundry_lite.application.services.pipeline_services import PipelineServices
from foundry_lite.application.services.runtime_bundle import (
    ErasureService,
    IcebergMaintenanceService,
    InsightReviewService,
    OutboxPublisherService,
    RecordDlqService,
    ResourceCatalogService,
    RuntimeService,
    WorkflowOrchestrationService,
)
from foundry_lite.application.services.runtime_evidence_service import RuntimeEvidenceService
from foundry_lite.application.services.source_services import (
    SourceCdcObjectIndexService,
    SourceConnectionTestService,
    SourceLifecycleService,
    SourceManagementService,
    SourceOnboardingService,
    SourceSchedulerService,
)
from foundry_lite.application.services.transform_service import TransformService
from foundry_lite.application.services.transform_services import TransformServices

__all__ = [
    "CoreServices",
    "ActionServices",
    "ActionEffectDeliveryService",
    "AgentRuntimeService",
    "ActionProposalService",
    "ApprovalExecutionService",
    "BackupRestoreServices",
    "BuilderRuntimeService",
    "ConnectorOnboardingService",
    "ContextCompilerService",
    "DemoService",
    "ErasureService",
    "EvalService",
    "FunctionExecutionService",
    "InsightReviewService",
    "DatasetServices",
    "IcebergMaintenanceService",
    "MaterializationService",
    "MediaServices",
    "CitationService",
    "LogicRuntimeService",
    "ModelGatewayService",
    "PromptArtifactService",
    "ToolBrokerService",
    "VisualBuilderService",
    "ObjectServices",
    "OntologySearchService",
    "OntologyService",
    "OntologyServices",
    "OsdkApplicationService",
    "OsdkApplicationServices",
    "OsdkOAuthSessionService",
    "PipelineServices",
    "OutboxPublisherService",
    "RecordDlqService",
    "ResourceCatalogService",
    "RuntimeEvidenceService",
    "RuntimeService",
    "SourceCdcObjectIndexService",
    "SourceConnectionTestService",
    "SourceManagementService",
    "SourceLifecycleService",
    "SourceOnboardingService",
    "SourceSchedulerService",
    "TransformService",
    "TransformServices",
    "WorkflowOrchestrationService",
]


class _SharedCoreServices(TypedDict):
    backup_restore: BackupRestoreServices
    dataset: DatasetServices
    iceberg_maintenance: IcebergMaintenanceService
    insight_review: InsightReviewService
    media: MediaServices
    object_store: ObjectServices
    source_management: SourceManagementService
    source_connection_test: SourceConnectionTestService
    source_lifecycle: SourceLifecycleService
    source_cdc_object_index: SourceCdcObjectIndexService
    source_scheduler: SourceSchedulerService


@dataclass(frozen=True)
class CoreServices:
    """Constructor-injected application service graph.

    ``FoundryLite`` delegates to this graph. Each service is a concrete
    object with only the dependencies it declares and explicit collaborator
    service attributes, replacing the previous facade-level MRO.
    """

    action: ActionServices
    action_effects: ActionEffectDeliveryService
    agent_runtime: AgentRuntimeService
    action_proposal: ActionProposalService
    approval_execution: ApprovalExecutionService
    backup_restore: BackupRestoreServices
    builder_runtime: BuilderRuntimeService
    connector_onboarding: ConnectorOnboardingService
    source_management: SourceManagementService
    source_connection_test: SourceConnectionTestService
    source_lifecycle: SourceLifecycleService
    source_cdc_object_index: SourceCdcObjectIndexService
    source_scheduler: SourceSchedulerService
    source_onboarding: SourceOnboardingService
    context_compiler: ContextCompilerService
    dataset: DatasetServices
    demo: DemoService
    erasure: ErasureService
    evals: EvalService
    function_execution: FunctionExecutionService
    iceberg_maintenance: IcebergMaintenanceService
    insight_review: InsightReviewService
    materialization: MaterializationService
    media: MediaServices
    citation: CitationService
    logic_runtime: LogicRuntimeService
    model_gateway: ModelGatewayService
    prompt_artifact: PromptArtifactService
    tool_broker: ToolBrokerService
    visual_builder: VisualBuilderService
    object_store: ObjectServices
    ontology: OntologyServices
    ontology_search: OntologySearchService
    osdk_applications: OsdkApplicationServices
    osdk_oauth_sessions: OsdkOAuthSessionService
    pipelines: PipelineServices
    outbox_publisher: OutboxPublisherService
    record_dlq: RecordDlqService
    resources: ResourceCatalogService
    runtime_evidence: RuntimeEvidenceService
    runtime: RuntimeService
    transform: TransformServices
    workflow: WorkflowOrchestrationService

    @classmethod
    def create(cls, dependencies: CoreDependencies) -> CoreServices:
        services = _new_core_services(dependencies)
        _bind_runtime_evidence_boundary(services)
        _bind_core_service_collaborators(services)
        return services


def _new_core_services(dependencies: CoreDependencies) -> CoreServices:
    shared = _shared_core_services(dependencies)
    model_gateway = build_service(ModelGatewayService, dependencies)
    pipeline_dependencies = _pipeline_dependencies(dependencies, model_gateway)
    return _compose_core_services(dependencies, shared, model_gateway, pipeline_dependencies)


def _shared_core_services(dependencies: CoreDependencies) -> _SharedCoreServices:
    return {
        "backup_restore": BackupRestoreServices.create(dependencies),
        "dataset": DatasetServices.create(dependencies),
        "iceberg_maintenance": build_service(IcebergMaintenanceService, dependencies),
        "insight_review": build_service(InsightReviewService, dependencies),
        "media": MediaServices.create(dependencies),
        "object_store": ObjectServices.create(dependencies),
        "source_cdc_object_index": build_service(SourceCdcObjectIndexService, dependencies),
        "source_management": build_service(SourceManagementService, dependencies),
        "source_connection_test": build_service(SourceConnectionTestService, dependencies),
        "source_lifecycle": build_service(SourceLifecycleService, dependencies),
        "source_scheduler": build_service(SourceSchedulerService, dependencies),
    }


# fmt: off
def _compose_core_services(dependencies: CoreDependencies, shared: _SharedCoreServices,
                           model_gateway: ModelGatewayService,
                           pipeline_dependencies: CoreDependencies) -> CoreServices:
    return CoreServices(
        action=ActionServices.create(dependencies),
        action_effects=build_service(ActionEffectDeliveryService, dependencies),
        agent_runtime=build_service(AgentRuntimeService, dependencies), backup_restore=shared["backup_restore"],
        action_proposal=build_service(ActionProposalService, dependencies), insight_review=shared["insight_review"],
        approval_execution=build_service(ApprovalExecutionService, dependencies), object_store=shared["object_store"],
        builder_runtime=build_service(BuilderRuntimeService, dependencies),
        connector_onboarding=build_service(ConnectorOnboardingService, dependencies),
        source_management=shared["source_management"], source_lifecycle=shared["source_lifecycle"],
        source_connection_test=shared["source_connection_test"],
        source_cdc_object_index=shared["source_cdc_object_index"],
        source_scheduler=shared["source_scheduler"],
        source_onboarding=build_service(SourceOnboardingService, dependencies),
        context_compiler=build_service(ContextCompilerService, dependencies),
        dataset=shared["dataset"], demo=build_service(DemoService, dependencies),
        erasure=build_service(ErasureService, dependencies), evals=build_service(EvalService, dependencies),
        function_execution=build_service(FunctionExecutionService, dependencies),
        iceberg_maintenance=shared["iceberg_maintenance"],
        materialization=build_service(MaterializationService, dependencies),
        media=shared["media"], citation=build_service(CitationService, dependencies),
        logic_runtime=build_service(LogicRuntimeService, dependencies),
        model_gateway=model_gateway, prompt_artifact=build_service(PromptArtifactService, dependencies),
        tool_broker=build_service(ToolBrokerService, dependencies),
        visual_builder=build_service(VisualBuilderService, dependencies),
        ontology=OntologyServices.create(dependencies),
        ontology_search=build_service(OntologySearchService, dependencies),
        osdk_applications=OsdkApplicationServices.create(dependencies),
        osdk_oauth_sessions=build_service(OsdkOAuthSessionService, dependencies),
        pipelines=PipelineServices.create(pipeline_dependencies),
        outbox_publisher=build_service(OutboxPublisherService, dependencies),
        record_dlq=build_service(RecordDlqService, dependencies),
        resources=build_service(ResourceCatalogService, dependencies),
        runtime_evidence=build_service(RuntimeEvidenceService, dependencies),
        runtime=build_service(RuntimeService, dependencies),
        transform=TransformServices.create(dependencies),
        workflow=build_service(WorkflowOrchestrationService, dependencies), )
# fmt: on


def _pipeline_dependencies(
    dependencies: CoreDependencies,
    model_gateway: ModelGatewayService,
) -> CoreDependencies:
    aip = replace(
        dependencies.aip,
        governed_semantic_model_port=GovernedSemanticModelBridge(model_gateway),
    )
    return CoreDependencies(
        paths=dependencies.paths,
        security=dependencies.security,
        action=dependencies.action,
        data=dependencies.data,
        object_store=dependencies.object_store,
        runtime=dependencies.runtime,
        aip=aip,
        media=dependencies.media,
        source=dependencies.source,
        pipeline_dag_orchestrator=dependencies.pipeline_dag_orchestrator,
        profile=dependencies.profile,
    )


def _bind_runtime_evidence_boundary(services: CoreServices) -> None:
    services.runtime.evidence_service = services.runtime_evidence


def _bind_core_service_collaborators(services: CoreServices) -> None:
    collaborators = _collaborator_map(services)
    for service in _core_service_items(services):
        service.bind_collaborators(collaborator_kwargs(service, collaborators))


def _core_service_items(services: CoreServices) -> list[CoreService]:
    return [
        *_action_service_items(services),
        services.agent_runtime,
        services.action_proposal,
        services.approval_execution,
        *services.backup_restore.items(),
        services.builder_runtime,
        services.connector_onboarding,
        *_source_service_items(services),
        services.context_compiler,
        *services.dataset.items(),
        services.demo,
        services.erasure,
        services.evals,
        services.function_execution,
        services.iceberg_maintenance,
        services.insight_review,
        services.materialization,
        *services.media.items(),
        services.citation,
        services.logic_runtime,
        services.model_gateway,
        services.prompt_artifact,
        services.tool_broker,
        services.visual_builder,
        *services.object_store.items(),
        *services.ontology.items(),
        services.ontology_search,
        *services.osdk_applications.items(),
        services.osdk_oauth_sessions,
        *services.pipelines.items(),
        services.outbox_publisher,
        services.record_dlq,
        services.resources,
        services.runtime_evidence,
        services.runtime,
        *services.transform.items(),
        services.workflow,
    ]


def _action_service_items(services: CoreServices) -> list[CoreService]:
    return [*services.action.items(), services.action_effects]


def _source_service_items(services: CoreServices) -> list[CoreService]:
    return [
        services.source_management,
        services.source_connection_test,
        services.source_lifecycle,
        services.source_cdc_object_index,
        services.source_scheduler,
        services.source_onboarding,
    ]


def _collaborator_map(services: CoreServices) -> dict[str, CoreService]:
    return {
        **_primary_collaborator_map(services),
        **_media_collaborator_map(services),
        **_data_collaborator_map(services),
        **_object_collaborator_map(services),
        **_operations_collaborator_map(services),
    }


def _primary_collaborator_map(services: CoreServices) -> dict[str, CoreService]:
    return {
        "action_apply_service": services.action.apply,
        "action_async_run_service": services.action.async_run,
        "action_batch_apply_service": services.action.batch_apply,
        "action_definition_service": services.action.definition,
        "action_effect_delivery_service": services.action_effects,
        "action_log_revert_service": services.action.log_revert,
        "action_planning_service": services.action.planning,
        "action_service": services.action.entrypoint,
        "action_validation_service": services.action.validation,
        "action_writeback_service": services.action.writeback,
        "agent_runtime_service": services.agent_runtime,
        "action_proposal_service": services.action_proposal,
        "approval_execution_service": services.approval_execution,
        "backup_restore_artifact_execution_service": services.backup_restore.artifact_execution,
        "backup_restore_artifact_restore_service": services.backup_restore.artifact_restore,
        "backup_restore_artifact_service": services.backup_restore.artifact,
        "backup_restore_mode_service": services.backup_restore.mode,
        "backup_restore_preflight_service": services.backup_restore.preflight,
        "backup_restore_service": services.backup_restore.entrypoint,
        "backup_restore_validation_service": services.backup_restore.validation,
        "builder_runtime_service": services.builder_runtime,
        "connector_onboarding_service": services.connector_onboarding,
        "source_management_service": services.source_management,
        "source_onboarding_service": services.source_onboarding,
        "citation_service": services.citation,
        "context_compiler_service": services.context_compiler,
        "demo_service": services.demo,
        "function_execution_service": services.function_execution,
        "iceberg_maintenance_service": services.iceberg_maintenance,
        "insight_review_service": services.insight_review,
        "logic_runtime_service": services.logic_runtime,
        "model_gateway_service": services.model_gateway,
        "prompt_artifact_service": services.prompt_artifact,
        "tool_broker_service": services.tool_broker,
        "visual_builder_service": services.visual_builder,
    }


def _media_collaborator_map(services: CoreServices) -> dict[str, CoreService]:
    return {
        "content_unit_chunking_service": services.media.chunking,
        "content_retrieval_service": services.media.retrieval,
        "media_catalog_service": services.media.catalog,
        "media_indexing_service": services.media.indexing,
        "media_processing_service": services.media.processing,
        "media_visual_search_service": services.media.visual_search,
        "media_transaction_service": services.media.transaction,
        "media_upload_service": services.media.upload,
    }


def _data_collaborator_map(services: CoreServices) -> dict[str, CoreService]:
    return {
        "dataset_ingest_service": services.dataset.ingest,
        "dataset_quality_api_service": services.dataset.quality_api,
        "dataset_quality_contract_service": services.dataset.quality_contract,
        "dataset_quality_runtime_service": services.dataset.quality_runtime,
        "dataset_quality_service": services.dataset.quality_runtime,
        "dataset_registry_service": services.dataset.registry,
        "dataset_transaction_service": services.dataset.transaction,
        "dataset_version_service": services.dataset.version,
        "exact_dataset_version_reader_service": services.pipelines.dataset_reader,
        "materialization_service": services.materialization,
        "transform_service": services.transform.entrypoint,
        "transform_definition_service": services.transform.definition,
        "transform_dlq_replay_service": services.transform.dlq_replay,
        "transform_graph_service": services.transform.graph,
        "transform_run_service": services.transform.run,
        "transform_scheduler_service": services.transform.scheduler,
        "pipeline_async_run_service": services.pipelines.async_run,
        "pipeline_catalog_service": services.pipelines.catalog,
        "pipeline_compiler_service": services.pipelines.compiler,
        "pipeline_definition_service": services.pipelines.definition,
        "pipeline_deployment_service": services.pipelines.deployment,
        "pipeline_governance_service": services.pipelines.governance,
        "pipeline_graph_v2_execution_service": services.pipelines.graph_v2_execution,
        "pipeline_graph_v2_run_coordinator_service": services.pipelines.graph_v2_run_coordinator,
        "pipeline_graph_validation_service": services.pipelines.graph_validation,
        "pipeline_preview_service": services.pipelines.preview,
        "pipeline_run_service": services.pipelines.run,
        "pipeline_scheduler_service": services.pipelines.scheduler,
    }


def _object_collaborator_map(services: CoreServices) -> dict[str, CoreService]:
    return {
        "object_cdc_indexing_service": services.object_store.indexing_services.cdc,
        "object_indexing_service": services.object_store.indexing,
        "object_index_rebuild_service": services.object_store.indexing_services.rebuild,
        "object_index_record_mutation_service": services.object_store.indexing_services.record_mutations,
        "object_index_shadow_service": services.object_store.indexing_services.shadow,
        "object_link_indexing_service": services.object_store.indexing_services.links,
        "object_links_service": services.object_store.links,
        "object_ontology_reindex_service": services.object_store.indexing_services.ontology_reindex,
        "object_query_service": services.object_store.query,
        "object_records_service": services.object_store.records,
        "object_search_service": services.object_store.search,
        "object_sets_service": services.object_store.sets,
        "object_subscription_service": services.object_store.subscriptions,
        "ontology_activation_service": services.ontology.activation,
        "ontology_branch_service": services.ontology.branches,
        "ontology_catalog_service": services.ontology.catalog,
        "ontology_insights_service": services.ontology.insights,
        "ontology_lookup_service": services.ontology.lookup,
        "ontology_proposal_service": services.ontology.proposals,
        "ontology_reindex_contract_service": services.ontology.reindex_contract,
        "ontology_service": services.ontology.entrypoint,
        "osdk_application_client_service": services.osdk_applications.client,
        "osdk_application_idempotency_service": services.osdk_applications.idempotency,
        "osdk_application_scope_service": services.osdk_applications.scope,
        "osdk_application_service": services.osdk_applications.entrypoint,
        "osdk_application_sdk_service": services.osdk_applications.sdk,
        "osdk_oauth_session_service": services.osdk_oauth_sessions,
    }


def _operations_collaborator_map(services: CoreServices) -> dict[str, CoreService]:
    return {
        "outbox_publisher_service": services.outbox_publisher,
        "record_dlq_service": services.record_dlq,
        "runtime_service": services.runtime,
        "workflow_orchestration_service": services.workflow,
    }
