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
from foundry_lite.infrastructure.adapters.elasticsearch_content_index import ElasticsearchContentIndexAdapter
from foundry_lite.infrastructure.adapters.elasticsearch_search import ElasticsearchAdapter, ElasticsearchAdapterConfig
from foundry_lite.infrastructure.adapters.iceberg_dataset_storage import (
    IcebergDatasetStorageAdapter,
    IcebergDatasetStorageAdapterConfig,
)
from foundry_lite.infrastructure.adapters.kafka_stream import (
    KafkaStreamAdapter,
    KafkaStreamAdapterConfig,
    KafkaStreamSubscription,
)
from foundry_lite.infrastructure.adapters.local_content_index import LocalContentIndexAdapter
from foundry_lite.infrastructure.adapters.local_media_storage import LocalMediaStorageAdapter
from foundry_lite.infrastructure.adapters.pdf_text_processor import PdfTextProcessorAdapter
from foundry_lite.infrastructure.adapters.rest_connector import RestPullConnectorAdapter
from foundry_lite.infrastructure.adapters.s3_dataset_storage import (
    S3DatasetStorageAdapter,
    S3DatasetStorageAdapterConfig,
)
from foundry_lite.infrastructure.adapters.s3_media_storage import (
    S3MediaStorageAdapter,
    S3MediaStorageConfig,
)
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
from foundry_lite.infrastructure.adapters.spark_compute import SparkComputeAdapter
from foundry_lite.infrastructure.adapters.temporal_workflow import (
    TemporalWorkflowAdapter,
    TemporalWorkflowAdapterConfig,
)

__all__ = [
    "DuckDBComputeAdapter",
    "DebeziumPostgresSourceConfig",
    "DebeziumPostgresStreamAdapter",
    "FakeConnectorAdapter",
    "FakeDatasetStorageAdapter",
    "FakeComputeAdapter",
    "SparkComputeAdapter",
    "FakeSearchAdapter",
    "FakeStreamAdapter",
    "FakeWorkflowAdapter",
    "KafkaStreamAdapter",
    "KafkaStreamAdapterConfig",
    "KafkaStreamSubscription",
    "LocalConnectorAdapter",
    "LocalDatasetStorageAdapter",
    "LocalMediaStorageAdapter",
    "LocalSearchAdapter",
    "LocalStreamAdapter",
    "LocalWorkflowAdapter",
    "PdfTextProcessorAdapter",
    "ElasticsearchAdapter",
    "ElasticsearchAdapterConfig",
    "ElasticsearchContentIndexAdapter",
    "LocalContentIndexAdapter",
    "RestPullConnectorAdapter",
    "S3MediaStorageAdapter",
    "S3MediaStorageConfig",
    "IcebergDatasetStorageAdapter",
    "IcebergDatasetStorageAdapterConfig",
    "S3DatasetStorageAdapter",
    "S3DatasetStorageAdapterConfig",
    "TemporalWorkflowAdapter",
    "TemporalWorkflowAdapterConfig",
]
