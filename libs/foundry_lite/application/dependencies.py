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
    ObjectReadRepository,
    RuntimeRepository,
)
from foundry_lite.security.policy import PolicyService


@dataclass(frozen=True)
class CoreDependencies:
    """Dependencies that compose the core facade without hard-coding local infrastructure."""

    root: Path
    storage_root: Path
    engine: Any
    policy: PolicyService
    compute_adapter: ComputeAdapter
    metadata_repository: MetadataRepository
    dataset_repository: DatasetRepository
    dataset_transaction_repository: DatasetTransactionRepository
    dataset_version_repository: DatasetVersionRepository
    object_read_repository: ObjectReadRepository
    runtime_repository: RuntimeRepository
    dataset_storage: DatasetStorageAdapter
