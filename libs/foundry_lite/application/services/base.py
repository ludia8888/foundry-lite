from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import fields
from pathlib import Path
from typing import Any

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
)
from foundry_lite.application.ports.action_repository import ActionRepository
from foundry_lite.application.ports.dataset_quality_repository import DatasetQualityRepository
from foundry_lite.application.ports.materialization_repository import MaterializationRepository
from foundry_lite.application.ports.ontology_repository import OntologyRepository
from foundry_lite.application.ports.transform_repository import TransformRepository
from foundry_lite.security.policy import PolicyService

CollaboratorMap = Mapping[str, Callable[..., Any]]


class CoreService:
    """Base class for constructor-injected application services.

    The attributes declared here mirror :class:`CoreDependencies`. Each service
    receives the dependency bag at construction time and gets cross-service
    helper methods through an explicit collaborator map owned by
    ``CoreServices``.

    This keeps ``FoundryLiteCore`` as a thin facade rather than a multiple
    inheritance host, while preserving the current public API surface.
    """

    root: Path
    storage_root: Path
    compute_adapter: ComputeAdapter
    dataset_repository: DatasetRepository
    dataset_transaction_repository: DatasetTransactionRepository
    dataset_version_repository: DatasetVersionRepository
    object_index_repository: ObjectIndexRepository
    object_read_repository: ObjectReadRepository
    object_set_repository: ObjectSetRepository
    runtime_repository: RuntimeRepository
    dataset_storage: DatasetStorageAdapter
    engine: Any
    policy: PolicyService
    action_repository: ActionRepository
    ontology_repository: OntologyRepository
    transform_repository: TransformRepository
    materialization_repository: MaterializationRepository
    dataset_quality_repository: DatasetQualityRepository

    def __init__(self, dependencies: CoreDependencies) -> None:
        self._dependencies = dependencies
        self._collaborators: CollaboratorMap = {}
        for field in fields(CoreDependencies):
            setattr(self, field.name, getattr(dependencies, field.name))

    def bind_collaborators(self, collaborators: CollaboratorMap) -> None:
        self._collaborators = collaborators

    def __getattr__(self, name: str) -> Any:
        collaborators = self.__dict__.get("_collaborators", {})
        if name in collaborators:
            return collaborators[name]
        raise AttributeError(name)
