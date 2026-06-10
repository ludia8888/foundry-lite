from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.services.action_service import ActionService
from foundry_lite.application.services.base import CoreService, build_service
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
    methods: dict[str, Callable[..., Any]]
    method_owners: dict[str, CoreService]

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
        methods, method_owners = _method_registry(services)
        collaborators = _collaborator_map(
            action, dataset, demo, materialization, object_store, ontology, runtime, transform
        )
        for service in services:
            service.bind_collaborators(collaborators)
        return cls(
            action=action,
            dataset=dataset,
            demo=demo,
            materialization=materialization,
            object_store=object_store,
            ontology=ontology,
            runtime=runtime,
            transform=transform,
            methods=methods,
            method_owners=method_owners,
        )

    def method(self, name: str) -> Callable[..., Any]:
        return self.methods[name]

    def override_method(self, name: str, value: Callable[..., Any]) -> None:
        service = self.method_owners[name]
        setattr(service, name, value)
        self.methods[name] = value


def _method_registry(
    services: Iterable[CoreService],
) -> tuple[dict[str, Callable[..., Any]], dict[str, CoreService]]:
    registry: dict[str, Callable[..., Any]] = {}
    owners: dict[str, str] = {}
    owner_services: dict[str, CoreService] = {}
    for service in services:
        for cls in service.__class__.mro():
            if cls in {CoreService, object}:
                continue
            for name, value in cls.__dict__.items():
                if name.startswith("__") or not callable(value):
                    continue
                if name in registry:
                    raise RuntimeError(f"service method {name!r} is defined by both {owners[name]} and {cls.__name__}")
                registry[name] = getattr(service, name)
                owners[name] = cls.__name__
                owner_services[name] = service
    return registry, owner_services


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
