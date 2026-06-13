from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.ports import (
    ComputeAdapter,
    DatasetRepository,
    DatasetStorageAdapter,
    DatasetTransactionRepository,
    DatasetVersionRepository,
    ObjectIndexRepository,
    ObjectReadRepository,
    ObjectSetRepository,
    RuntimeRepository,
    TransactionManager,
)
from foundry_lite.application.ports.action_repository import ActionRepository
from foundry_lite.application.ports.connector_adapter import ConnectorAdapter
from foundry_lite.application.ports.dataset_quality_repository import DatasetQualityRepository
from foundry_lite.application.ports.materialization_repository import MaterializationRepository
from foundry_lite.application.ports.ontology_repository import OntologyRepository
from foundry_lite.application.ports.transform_repository import TransformRepository
from foundry_lite.observability.tracing import trace_direct_public_methods
from foundry_lite.security.policy import PolicyService

CollaboratorMap = Mapping[str, object]

SERVICE_COLLABORATORS: Mapping[str, str] = {
    "action_service": "ActionService",
    "dataset_ingest_service": "DatasetIngestService",
    "dataset_quality_service": "DatasetQualityService",
    "dataset_registry_service": "DatasetRegistryService",
    "dataset_transaction_service": "DatasetTransactionService",
    "dataset_version_service": "DatasetVersionService",
    "demo_service": "DemoService",
    "materialization_service": "MaterializationService",
    "object_indexing_service": "ObjectIndexingService",
    "object_links_service": "ObjectLinksService",
    "object_query_service": "ObjectQueryService",
    "object_records_service": "ObjectRecordsService",
    "object_sets_service": "ObjectSetsService",
    "ontology_service": "OntologyService",
    "runtime_service": "RuntimeService",
    "transform_service": "TransformService",
}


class CoreService:
    """Base class for constructor-injected application services.

    Each concrete service declares only the infrastructure dependencies it
    directly uses through ``required_dependencies`` and only the service
    collaborators it directly calls through ``required_collaborators``.

    This keeps ``FoundryLiteCore`` as a thin facade rather than a multiple
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
    object_index_repository: ObjectIndexRepository
    object_read_repository: ObjectReadRepository
    object_set_repository: ObjectSetRepository
    runtime_repository: RuntimeRepository
    dataset_storage: DatasetStorageAdapter
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
