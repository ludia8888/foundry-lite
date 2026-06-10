"""Concrete infrastructure adapters."""

from foundry_lite.infrastructure.adapters.dataset_storage import (
    FakeDatasetStorageAdapter,
    LocalDatasetStorageAdapter,
)

__all__ = [
    "FakeDatasetStorageAdapter",
    "LocalDatasetStorageAdapter",
]
