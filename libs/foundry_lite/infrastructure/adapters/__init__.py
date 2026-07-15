"""Concrete infrastructure adapters."""

from foundry_lite.infrastructure.adapters.asr_processor import AsrProcessorAdapter
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
from foundry_lite.infrastructure.adapters.fake_language_model import FakeLanguageModel
from foundry_lite.infrastructure.adapters.iceberg_dataset_storage import (
    IcebergDatasetStorageAdapter,
    IcebergDatasetStorageAdapterConfig,
)
from foundry_lite.infrastructure.adapters.image_processor import ImageProcessorAdapter
from foundry_lite.infrastructure.adapters.kafka_stream import (
    KafkaStreamAdapter,
    KafkaStreamAdapterConfig,
    KafkaStreamSubscription,
)
from foundry_lite.infrastructure.adapters.kraken_websocket import (
    KrakenSignal,
    KrakenTrade,
    KrakenWebSocketV2Adapter,
    KrakenWebSocketV2Config,
)
from foundry_lite.infrastructure.adapters.local_backup_artifact_store import LocalBackupArtifactStore
from foundry_lite.infrastructure.adapters.local_completion import LocalCompletionAdapter
from foundry_lite.infrastructure.adapters.local_content_index import LocalContentIndexAdapter
from foundry_lite.infrastructure.adapters.local_embedding import LocalEmbeddingAdapter
from foundry_lite.infrastructure.adapters.local_external_media_reader import (
    LocalExternalMediaReader,
    S3ExternalMediaReader,
    S3ExternalMediaReaderConfig,
)
from foundry_lite.infrastructure.adapters.local_media_storage import LocalMediaStorageAdapter
from foundry_lite.infrastructure.adapters.local_preview_renderer import LocalPreviewRendererAdapter
from foundry_lite.infrastructure.adapters.ocr_processor import OcrProcessorAdapter
from foundry_lite.infrastructure.adapters.pdf_text_processor import PdfTextProcessorAdapter
from foundry_lite.infrastructure.adapters.provider_compatible_language_model import ProviderCompatibleLanguageModel
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
from foundry_lite.infrastructure.adapters.source_database import SqlAlchemySourceDatabaseAdapter
from foundry_lite.infrastructure.adapters.source_stream import KafkaSourceStreamAdapter
from foundry_lite.infrastructure.adapters.spark_compute import SparkComputeAdapter
from foundry_lite.infrastructure.adapters.temporal_workflow import (
    TemporalWorkflowAdapter,
    TemporalWorkflowAdapterConfig,
)
from foundry_lite.infrastructure.adapters.video_probe_processor import (
    VideoProbeProcessorAdapter,
    VideoSceneFrameProcessorAdapter,
    VideoSceneVisionProcessorAdapter,
)

__all__ = [
    "DuckDBComputeAdapter",
    "DebeziumPostgresSourceConfig",
    "DebeziumPostgresStreamAdapter",
    "FakeConnectorAdapter",
    "FakeDatasetStorageAdapter",
    "FakeComputeAdapter",
    "SparkComputeAdapter",
    "SqlAlchemySourceDatabaseAdapter",
    "KafkaSourceStreamAdapter",
    "FakeSearchAdapter",
    "FakeStreamAdapter",
    "FakeWorkflowAdapter",
    "KafkaStreamAdapter",
    "KafkaStreamAdapterConfig",
    "KafkaStreamSubscription",
    "KrakenSignal",
    "KrakenTrade",
    "KrakenWebSocketV2Adapter",
    "KrakenWebSocketV2Config",
    "LocalCompletionAdapter",
    "LocalBackupArtifactStore",
    "LocalConnectorAdapter",
    "LocalDatasetStorageAdapter",
    "LocalExternalMediaReader",
    "LocalMediaStorageAdapter",
    "LocalPreviewRendererAdapter",
    "LocalSearchAdapter",
    "LocalStreamAdapter",
    "LocalWorkflowAdapter",
    "AsrProcessorAdapter",
    "ImageProcessorAdapter",
    "OcrProcessorAdapter",
    "PdfTextProcessorAdapter",
    "VideoProbeProcessorAdapter",
    "VideoSceneFrameProcessorAdapter",
    "VideoSceneVisionProcessorAdapter",
    "ElasticsearchAdapter",
    "ElasticsearchAdapterConfig",
    "ElasticsearchContentIndexAdapter",
    "FakeLanguageModel",
    "ProviderCompatibleLanguageModel",
    "LocalContentIndexAdapter",
    "LocalEmbeddingAdapter",
    "RestPullConnectorAdapter",
    "S3MediaStorageAdapter",
    "S3MediaStorageConfig",
    "S3ExternalMediaReader",
    "S3ExternalMediaReaderConfig",
    "IcebergDatasetStorageAdapter",
    "IcebergDatasetStorageAdapterConfig",
    "S3DatasetStorageAdapter",
    "S3DatasetStorageAdapterConfig",
    "TemporalWorkflowAdapter",
    "TemporalWorkflowAdapterConfig",
]
