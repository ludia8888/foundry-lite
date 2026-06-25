from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.services.action_service import ActionService
from foundry_lite.application.services.aip.action_proposal import ActionProposalService
from foundry_lite.application.services.aip.approval_execution import ApprovalExecutionService
from foundry_lite.application.services.aip.citation_service import CitationService
from foundry_lite.application.services.aip.eval_service import EvalService
from foundry_lite.application.services.aip.logic_runtime import LogicRuntimeService
from foundry_lite.application.services.aip.model_gateway import ModelGatewayService
from foundry_lite.application.services.aip.tool_broker import ToolBrokerService
from foundry_lite.application.services.base import CoreService, build_service, collaborator_kwargs
from foundry_lite.application.services.dataset_service import DatasetServices
from foundry_lite.application.services.demo_service import DemoService
from foundry_lite.application.services.materialization_service import MaterializationService
from foundry_lite.application.services.media_service import MediaServices
from foundry_lite.application.services.object_service import ObjectServices
from foundry_lite.application.services.ontology_search import OntologySearchService
from foundry_lite.application.services.ontology_service import OntologyService
from foundry_lite.application.services.runtime_bundle import (
    BackupRestoreService,
    ErasureService,
    IcebergMaintenanceService,
    InsightReviewService,
    RecordDlqService,
    RuntimeService,
    WorkflowOrchestrationService,
)
from foundry_lite.application.services.transform_service import TransformService

__all__ = [
    "CoreServices",
    "ActionService",
    "ActionProposalService",
    "ApprovalExecutionService",
    "BackupRestoreService",
    "DemoService",
    "ErasureService",
    "EvalService",
    "InsightReviewService",
    "DatasetServices",
    "IcebergMaintenanceService",
    "MaterializationService",
    "MediaServices",
    "CitationService",
    "LogicRuntimeService",
    "ModelGatewayService",
    "ToolBrokerService",
    "ObjectServices",
    "OntologySearchService",
    "OntologyService",
    "RecordDlqService",
    "RuntimeService",
    "TransformService",
    "WorkflowOrchestrationService",
]


@dataclass(frozen=True)
class CoreServices:
    """Constructor-injected application service graph.

    ``FoundryLite`` delegates to this graph. Each service is a concrete
    object with only the dependencies it declares and explicit collaborator
    service attributes, replacing the previous facade-level MRO.
    """

    action: ActionService
    action_proposal: ActionProposalService
    approval_execution: ApprovalExecutionService
    backup_restore: BackupRestoreService
    dataset: DatasetServices
    demo: DemoService
    erasure: ErasureService
    evals: EvalService
    iceberg_maintenance: IcebergMaintenanceService
    insight_review: InsightReviewService
    materialization: MaterializationService
    media: MediaServices
    citation: CitationService
    logic_runtime: LogicRuntimeService
    model_gateway: ModelGatewayService
    tool_broker: ToolBrokerService
    object_store: ObjectServices
    ontology: OntologyService
    ontology_search: OntologySearchService
    record_dlq: RecordDlqService
    runtime: RuntimeService
    transform: TransformService
    workflow: WorkflowOrchestrationService

    @classmethod
    def create(cls, dependencies: CoreDependencies) -> CoreServices:
        services = _new_core_services(cls, dependencies)
        _bind_core_service_collaborators(services)
        return services


def _new_core_services(service_type: type[CoreServices], dependencies: CoreDependencies) -> CoreServices:
    backup_restore = build_service(BackupRestoreService, dependencies)
    dataset = DatasetServices.create(dependencies)
    demo = build_service(DemoService, dependencies)
    erasure = build_service(ErasureService, dependencies)
    iceberg_maintenance = build_service(IcebergMaintenanceService, dependencies)
    insight_review = build_service(InsightReviewService, dependencies)
    materialization = build_service(MaterializationService, dependencies)
    media = MediaServices.create(dependencies)
    object_store = ObjectServices.create(dependencies)
    ontology = build_service(OntologyService, dependencies)
    ontology_search = build_service(OntologySearchService, dependencies)
    record_dlq = build_service(RecordDlqService, dependencies)
    runtime = build_service(RuntimeService, dependencies)
    transform = build_service(TransformService, dependencies)
    return service_type(
        action=build_service(ActionService, dependencies),
        action_proposal=build_service(ActionProposalService, dependencies),
        approval_execution=build_service(ApprovalExecutionService, dependencies),
        backup_restore=backup_restore,
        dataset=dataset,
        demo=demo,
        erasure=erasure,
        evals=build_service(EvalService, dependencies),
        iceberg_maintenance=iceberg_maintenance,
        insight_review=insight_review,
        materialization=materialization,
        media=media,
        citation=build_service(CitationService, dependencies),
        logic_runtime=build_service(LogicRuntimeService, dependencies),
        model_gateway=build_service(ModelGatewayService, dependencies),
        tool_broker=build_service(ToolBrokerService, dependencies),
        object_store=object_store,
        ontology=ontology,
        ontology_search=ontology_search,
        record_dlq=record_dlq,
        runtime=runtime,
        transform=transform,
        workflow=build_service(WorkflowOrchestrationService, dependencies),
    )


def _bind_core_service_collaborators(services: CoreServices) -> None:
    collaborators = _collaborator_map(services)
    for service in _core_service_items(services):
        service.bind_collaborators(collaborator_kwargs(service, collaborators))


def _core_service_items(services: CoreServices) -> list[CoreService]:
    return [
        services.action,
        services.action_proposal,
        services.approval_execution,
        services.backup_restore,
        *services.dataset.items(),
        services.demo,
        services.erasure,
        services.evals,
        services.iceberg_maintenance,
        services.insight_review,
        services.materialization,
        *services.media.items(),
        services.citation,
        services.logic_runtime,
        services.model_gateway,
        services.tool_broker,
        *services.object_store.items(),
        services.ontology,
        services.ontology_search,
        services.record_dlq,
        services.runtime,
        services.transform,
        services.workflow,
    ]


def _collaborator_map(services: CoreServices) -> dict[str, CoreService]:
    return {
        "action_service": services.action,
        "action_proposal_service": services.action_proposal,
        "approval_execution_service": services.approval_execution,
        "backup_restore_service": services.backup_restore,
        "content_retrieval_service": services.media.retrieval,
        "media_visual_search_service": services.media.visual_search,
        "dataset_ingest_service": services.dataset.ingest,
        "dataset_quality_service": services.dataset.quality,
        "dataset_registry_service": services.dataset.registry,
        "dataset_transaction_service": services.dataset.transaction,
        "dataset_version_service": services.dataset.version,
        "demo_service": services.demo,
        "iceberg_maintenance_service": services.iceberg_maintenance,
        "insight_review_service": services.insight_review,
        "logic_runtime_service": services.logic_runtime,
        "materialization_service": services.materialization,
        "object_indexing_service": services.object_store.indexing,
        "object_links_service": services.object_store.links,
        "object_query_service": services.object_store.query,
        "object_records_service": services.object_store.records,
        "object_search_service": services.object_store.search,
        "object_sets_service": services.object_store.sets,
        "ontology_service": services.ontology,
        "record_dlq_service": services.record_dlq,
        "runtime_service": services.runtime,
        "tool_broker_service": services.tool_broker,
        "transform_service": services.transform,
        "workflow_orchestration_service": services.workflow,
    }
