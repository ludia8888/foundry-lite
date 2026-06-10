from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.infrastructure.adapters import FakeDatasetStorageAdapter, LocalDatasetStorageAdapter
from foundry_lite.infrastructure.repositories import SqlAlchemyDatasetRepository, SqlAlchemyMetadataRepository
from foundry_lite.security.policy import PolicyService


def create_local_core_dependencies(
    *,
    db_url: str | None = None,
    storage_root: str | Path | None = None,
    adapter_profile: str = "local",
) -> CoreDependencies:
    """Build the local composition root used by CLI, API, tests, and demos."""

    root = Path(storage_root or ".foundry-lite").resolve()
    root.mkdir(parents=True, exist_ok=True)
    object_storage_root = root / "object-storage"
    object_storage_root.mkdir(parents=True, exist_ok=True)

    storage_adapter = _dataset_storage_adapter(adapter_profile, object_storage_root)
    database_url = db_url or f"sqlite:///{root / 'foundry-lite.db'}"
    engine = create_engine(database_url, future=True)
    return CoreDependencies(
        root=root,
        storage_root=object_storage_root,
        engine=engine,
        policy=PolicyService(),
        metadata_repository=SqlAlchemyMetadataRepository(engine),
        dataset_repository=SqlAlchemyDatasetRepository(engine),
        dataset_storage=storage_adapter,
    )


def _dataset_storage_adapter(adapter_profile: str, object_storage_root: Path) -> LocalDatasetStorageAdapter:
    if adapter_profile == "local":
        return LocalDatasetStorageAdapter(object_storage_root)
    if adapter_profile == "fake-storage":
        return FakeDatasetStorageAdapter(object_storage_root)
    raise ValueError(f"unknown adapter profile: {adapter_profile}")
