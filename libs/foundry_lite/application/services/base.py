from __future__ import annotations

from pathlib import Path
from typing import Any

from foundry_lite.application.ports import (
    DatasetRepository,
    DatasetStorageAdapter,
    DatasetTransactionRepository,
    DatasetVersionRepository,
    RuntimeRepository,
)
from foundry_lite.security.policy import PolicyService


class CoreServiceMixin:
    """Shared dependency contract for service mixins behind the FoundryLiteCore facade."""

    root: Path
    storage_root: Path
    dataset_repository: DatasetRepository
    dataset_transaction_repository: DatasetTransactionRepository
    dataset_version_repository: DatasetVersionRepository
    runtime_repository: RuntimeRepository
    dataset_storage: DatasetStorageAdapter
    engine: Any
    policy: PolicyService

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)
