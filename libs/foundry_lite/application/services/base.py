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
    RuntimeRepository,
)
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
    runtime_repository: RuntimeRepository
    dataset_storage: DatasetStorageAdapter
    engine: Any
    policy: PolicyService

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)
