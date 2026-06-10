from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.infrastructure.adapters import (
    DuckDBComputeAdapter,
    FakeComputeAdapter,
    FakeDatasetStorageAdapter,
    LocalDatasetStorageAdapter,
)
from foundry_lite.infrastructure.repositories import (
    SqlAlchemyDatasetRepository,
    SqlAlchemyDatasetTransactionRepository,
    SqlAlchemyDatasetVersionRepository,
    SqlAlchemyMetadataRepository,
    SqlAlchemyObjectIndexRepository,
    SqlAlchemyObjectReadRepository,
    SqlAlchemyRuntimeRepository,
)
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
    compute_adapter = _compute_adapter(adapter_profile)
    database_url = db_url or f"sqlite:///{root / 'foundry-lite.db'}"
    engine = create_engine(database_url, future=True)
    return CoreDependencies(
        root=root,
        storage_root=object_storage_root,
        engine=engine,
        policy=PolicyService(),
        compute_adapter=compute_adapter,
        metadata_repository=SqlAlchemyMetadataRepository(engine),
        dataset_repository=SqlAlchemyDatasetRepository(engine),
        dataset_transaction_repository=SqlAlchemyDatasetTransactionRepository(engine),
        dataset_version_repository=SqlAlchemyDatasetVersionRepository(engine),
        object_index_repository=SqlAlchemyObjectIndexRepository(engine),
        object_read_repository=SqlAlchemyObjectReadRepository(engine),
        runtime_repository=SqlAlchemyRuntimeRepository(engine),
        dataset_storage=storage_adapter,
    )


def _dataset_storage_adapter(adapter_profile: str, object_storage_root: Path) -> LocalDatasetStorageAdapter:
    if adapter_profile == "local":
        return LocalDatasetStorageAdapter(object_storage_root)
    if adapter_profile == "fake-storage":
        return FakeDatasetStorageAdapter(object_storage_root)
    raise ValueError(f"unknown adapter profile: {adapter_profile}")


def _compute_adapter(adapter_profile: str) -> DuckDBComputeAdapter:
    if adapter_profile == "local":
        return DuckDBComputeAdapter()
    if adapter_profile == "fake-storage":
        return FakeComputeAdapter()
    raise ValueError(f"unknown adapter profile: {adapter_profile}")
