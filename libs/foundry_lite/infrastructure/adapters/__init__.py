"""Concrete infrastructure adapters."""

from foundry_lite.infrastructure.adapters.compute import DuckDBComputeAdapter, FakeComputeAdapter
from foundry_lite.infrastructure.adapters.dataset_storage import (
    FakeDatasetStorageAdapter,
    LocalDatasetStorageAdapter,
)

__all__ = [
    "DuckDBComputeAdapter",
    "FakeDatasetStorageAdapter",
    "FakeComputeAdapter",
    "LocalDatasetStorageAdapter",
]
