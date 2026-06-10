from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import Engine

from foundry_lite.application.ports import DatasetStorageAdapter
from foundry_lite.security.policy import PolicyService


@dataclass(frozen=True)
class CoreDependencies:
    """Dependencies that compose the core facade without hard-coding local infrastructure."""

    root: Path
    storage_root: Path
    engine: Engine
    policy: PolicyService
    dataset_storage: DatasetStorageAdapter
    initialize_schema: Callable[[Engine], None] | None
