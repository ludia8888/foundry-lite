from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.services.action_service import ActionService
from foundry_lite.application.services.base import CoreService
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
    object with the same ``CoreDependencies`` instance and an explicit
    collaborator method registry, replacing the previous facade-level MRO.
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
        action = ActionService(dependencies)
        dataset = DatasetServices.create(dependencies)
        demo = DemoService(dependencies)
        materialization = MaterializationService(dependencies)
        object_store = ObjectServices.create(dependencies)
        ontology = OntologyService(dependencies)
        runtime = RuntimeService(dependencies)
        transform = TransformService(dependencies)
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
        for service in services:
            service.bind_collaborators(methods)
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
