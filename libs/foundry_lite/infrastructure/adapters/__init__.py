"""Concrete infrastructure adapters."""

from foundry_lite.infrastructure.adapters.compute import DuckDBComputeAdapter, FakeComputeAdapter
from foundry_lite.infrastructure.adapters.dataset_storage import (
    FakeDatasetStorageAdapter,
    LocalDatasetStorageAdapter,
)
from foundry_lite.infrastructure.adapters.debezium_cdc import (
    DebeziumPostgresSourceConfig,
    DebeziumPostgresStreamAdapter,
)
from foundry_lite.infrastructure.adapters.kafka_stream import (
    KafkaStreamAdapter,
    KafkaStreamAdapterConfig,
    KafkaStreamSubscription,
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
    "DebeziumPostgresSourceConfig",
    "DebeziumPostgresStreamAdapter",
    "FakeConnectorAdapter",
    "FakeDatasetStorageAdapter",
    "FakeComputeAdapter",
    "FakeSearchAdapter",
    "FakeStreamAdapter",
    "FakeWorkflowAdapter",
    "KafkaStreamAdapter",
    "KafkaStreamAdapterConfig",
    "KafkaStreamSubscription",
    "LocalConnectorAdapter",
    "LocalDatasetStorageAdapter",
    "LocalSearchAdapter",
    "LocalStreamAdapter",
    "LocalWorkflowAdapter",
    "RestPullConnectorAdapter",
]
