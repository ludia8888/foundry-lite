from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from foundry_lite.application.ports import DatasetRepository, DatasetStorageAdapter, MetadataRepository
from foundry_lite.security.policy import PolicyService


@dataclass(frozen=True)
class CoreDependencies:
    """Dependencies that compose the core facade without hard-coding local infrastructure."""

    root: Path
    storage_root: Path
    engine: Any
    policy: PolicyService
    metadata_repository: MetadataRepository
    dataset_repository: DatasetRepository
    dataset_storage: DatasetStorageAdapter
