from __future__ import annotations

from pathlib import Path
from typing import Any

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
from foundry_lite.application.ports.ontology_repository import OntologyRepository
from foundry_lite.security.policy import PolicyService


class CoreServiceMixin:
    """Shared dependency contract for service mixins behind the FoundryLiteCore facade."""

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

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)
