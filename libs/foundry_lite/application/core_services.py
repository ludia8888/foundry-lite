from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.services.action_service import ActionService
from foundry_lite.application.services.base import CoreService, build_service, collaborator_kwargs
from foundry_lite.application.services.dataset_service import DatasetServices
from foundry_lite.application.services.demo_service import DemoService
from foundry_lite.application.services.materialization_service import MaterializationService
from foundry_lite.application.services.object_service import ObjectServices
from foundry_lite.application.services.ontology_service import OntologyService
from foundry_lite.application.services.runtime_bundle import (
    BackupRestoreService,
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
    "BackupRestoreService",
    "DemoService",
    "InsightReviewService",
    "DatasetServices",
    "IcebergMaintenanceService",
    "MaterializationService",
    "ObjectServices",
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
    backup_restore: BackupRestoreService
    dataset: DatasetServices
    demo: DemoService
    iceberg_maintenance: IcebergMaintenanceService
    insight_review: InsightReviewService
    materialization: MaterializationService
    object_store: ObjectServices
    ontology: OntologyService
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
    action = build_service(ActionService, dependencies)
    backup_restore = build_service(BackupRestoreService, dependencies)
    dataset = DatasetServices.create(dependencies)
    demo = build_service(DemoService, dependencies)
    iceberg_maintenance = build_service(IcebergMaintenanceService, dependencies)
    insight_review = build_service(InsightReviewService, dependencies)
    materialization = build_service(MaterializationService, dependencies)
    object_store = ObjectServices.create(dependencies)
    ontology = build_service(OntologyService, dependencies)
    record_dlq = build_service(RecordDlqService, dependencies)
    runtime = build_service(RuntimeService, dependencies)
    transform = build_service(TransformService, dependencies)
    workflow = build_service(WorkflowOrchestrationService, dependencies)
    return service_type(
        action=action,
        backup_restore=backup_restore,
        dataset=dataset,
        demo=demo,
        iceberg_maintenance=iceberg_maintenance,
        insight_review=insight_review,
        materialization=materialization,
        object_store=object_store,
        ontology=ontology,
        record_dlq=record_dlq,
        runtime=runtime,
        transform=transform,
        workflow=workflow,
    )


def _bind_core_service_collaborators(services: CoreServices) -> None:
    service_items = [
        services.action,
        services.backup_restore,
        *services.dataset.items(),
        services.demo,
        services.iceberg_maintenance,
        services.insight_review,
        services.materialization,
        *services.object_store.items(),
        services.ontology,
        services.record_dlq,
        services.runtime,
        services.transform,
        services.workflow,
    ]
    collaborators = _collaborator_map(
        services.action,
        services.backup_restore,
        services.dataset,
        services.demo,
        services.iceberg_maintenance,
        services.materialization,
        services.object_store,
        services.ontology,
        services.record_dlq,
        services.runtime,
        services.transform,
        services.workflow,
    )
    for service in service_items:
        service.bind_collaborators(collaborator_kwargs(service, collaborators))


def _collaborator_map(
    action: ActionService,
    backup_restore: BackupRestoreService,
    dataset: DatasetServices,
    demo: DemoService,
    iceberg_maintenance: IcebergMaintenanceService,
    materialization: MaterializationService,
    object_store: ObjectServices,
    ontology: OntologyService,
    record_dlq: RecordDlqService,
    runtime: RuntimeService,
    transform: TransformService,
    workflow: WorkflowOrchestrationService,
) -> dict[str, CoreService]:
    return {
        "action_service": action,
        "backup_restore_service": backup_restore,
        "dataset_ingest_service": dataset.ingest,
        "dataset_quality_service": dataset.quality,
        "dataset_registry_service": dataset.registry,
        "dataset_transaction_service": dataset.transaction,
        "dataset_version_service": dataset.version,
        "demo_service": demo,
        "iceberg_maintenance_service": iceberg_maintenance,
        "materialization_service": materialization,
        "object_indexing_service": object_store.indexing,
        "object_links_service": object_store.links,
        "object_query_service": object_store.query,
        "object_records_service": object_store.records,
        "object_search_service": object_store.search,
        "object_sets_service": object_store.sets,
        "ontology_service": ontology,
        "record_dlq_service": record_dlq,
        "runtime_service": runtime,
        "transform_service": transform,
        "workflow_orchestration_service": workflow,
    }
