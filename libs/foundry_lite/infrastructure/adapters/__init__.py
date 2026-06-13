"""Concrete infrastructure adapters."""

from foundry_lite.infrastructure.adapters.compute import DuckDBComputeAdapter, FakeComputeAdapter
from foundry_lite.infrastructure.adapters.dataset_storage import (
    FakeDatasetStorageAdapter,
    LocalDatasetStorageAdapter,
)
from foundry_lite.infrastructure.adapters.rest_connector import RestPullConnectorAdapter
from foundry_lite.infrastructure.adapters.scale_foundation import (
    FakeConnectorAdapter,
    FakeSearchAdapter,
    FakeStreamAdapter,
    FakeWorkflowAdapter,
    LocalConnectorAdapter,
    LocalSearchAdapter,
    LocalStreamAdapter,
    LocalWorkflowAdapter,
)

__all__ = [
    "DuckDBComputeAdapter",
    "FakeConnectorAdapter",
    "FakeDatasetStorageAdapter",
    "FakeComputeAdapter",
    "FakeSearchAdapter",
    "FakeStreamAdapter",
    "FakeWorkflowAdapter",
    "LocalConnectorAdapter",
    "LocalDatasetStorageAdapter",
    "LocalSearchAdapter",
    "LocalStreamAdapter",
    "LocalWorkflowAdapter",
    "RestPullConnectorAdapter",
]
