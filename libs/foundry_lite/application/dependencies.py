from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from foundry_lite.application.ports import (
    ComputeAdapter,
    DatasetRepository,
    DatasetStorageAdapter,
    DatasetTransactionRepository,
    DatasetVersionRepository,
    MetadataRepository,
    ObjectIndexRepository,
    ObjectReadRepository,
    ObjectSetRepository,
    RuntimeRepository,
)
from foundry_lite.application.ports.action_repository import ActionRepository
from foundry_lite.application.ports.ontology_repository import OntologyRepository
from foundry_lite.application.ports.transform_repository import TransformRepository
from foundry_lite.security.policy import PolicyService


@dataclass(frozen=True)
class CoreDependencies:
    """Dependencies that compose the core facade without hard-coding local infrastructure."""

    root: Path
    storage_root: Path
    engine: Any
    policy: PolicyService
    action_repository: ActionRepository
    ontology_repository: OntologyRepository
    transform_repository: TransformRepository
    compute_adapter: ComputeAdapter
    metadata_repository: MetadataRepository
    dataset_repository: DatasetRepository
    dataset_transaction_repository: DatasetTransactionRepository
    dataset_version_repository: DatasetVersionRepository
    object_index_repository: ObjectIndexRepository
    object_read_repository: ObjectReadRepository
    object_set_repository: ObjectSetRepository
    runtime_repository: RuntimeRepository
    dataset_storage: DatasetStorageAdapter
