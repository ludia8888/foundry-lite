from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine

from foundry_lite.application.ports import DatasetStorageAdapter
from foundry_lite.security.policy import PolicyService


class CoreServiceMixin:
    """Shared dependency contract for service mixins behind the FoundryLiteCore facade."""

    root: Path
    storage_root: Path
    dataset_storage: DatasetStorageAdapter
    engine: Engine
    policy: PolicyService

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)
