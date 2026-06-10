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
from foundry_lite.application.services.runtime_service import RuntimeService
from foundry_lite.application.services.transform_service import TransformService

__all__ = [
    "CoreServices",
    "ActionService",
    "DemoService",
    "DatasetServices",
    "MaterializationService",
    "ObjectServices",
    "OntologyService",
    "RuntimeService",
    "TransformService",
]


@dataclass(frozen=True)
class CoreServices:
    """Constructor-injected application service graph.

    ``FoundryLiteCore`` delegates to this graph. Each service is a concrete
    object with only the dependencies it declares and explicit collaborator
    service attributes, replacing the previous facade-level MRO.
    """

    action: ActionService
    dataset: DatasetServices
    demo: DemoService
    materialization: MaterializationService
    object_store: ObjectServices
    ontology: OntologyService
    runtime: RuntimeService
    transform: TransformService

    @classmethod
    def create(cls, dependencies: CoreDependencies) -> CoreServices:
        action = build_service(ActionService, dependencies)
        dataset = DatasetServices.create(dependencies)
        demo = build_service(DemoService, dependencies)
        materialization = build_service(MaterializationService, dependencies)
        object_store = ObjectServices.create(dependencies)
        ontology = build_service(OntologyService, dependencies)
        runtime = build_service(RuntimeService, dependencies)
        transform = build_service(TransformService, dependencies)
        services = [
            action,
            *dataset.items(),
            demo,
            materialization,
            *object_store.items(),
            ontology,
            runtime,
            transform,
        ]
        collaborators = _collaborator_map(
            action, dataset, demo, materialization, object_store, ontology, runtime, transform
        )
        for service in services:
            service.bind_collaborators(collaborator_kwargs(service, collaborators))
        return cls(
            action=action,
            dataset=dataset,
            demo=demo,
            materialization=materialization,
            object_store=object_store,
            ontology=ontology,
            runtime=runtime,
            transform=transform,
        )


def _collaborator_map(
    action: ActionService,
    dataset: DatasetServices,
    demo: DemoService,
    materialization: MaterializationService,
    object_store: ObjectServices,
    ontology: OntologyService,
    runtime: RuntimeService,
    transform: TransformService,
) -> dict[str, CoreService]:
    return {
        "action_service": action,
        "dataset_ingest_service": dataset.ingest,
        "dataset_quality_service": dataset.quality,
        "dataset_registry_service": dataset.registry,
        "dataset_transaction_service": dataset.transaction,
        "dataset_version_service": dataset.version,
        "demo_service": demo,
        "materialization_service": materialization,
        "object_indexing_service": object_store.indexing,
        "object_links_service": object_store.links,
        "object_query_service": object_store.query,
        "object_records_service": object_store.records,
        "object_sets_service": object_store.sets,
        "ontology_service": ontology,
        "runtime_service": runtime,
        "transform_service": transform,
    }
