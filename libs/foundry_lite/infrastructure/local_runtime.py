from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.ports.content_index import ContentIndexAdapter
from foundry_lite.application.ports.dataset_storage import DatasetStorageAdapter
from foundry_lite.application.ports.external_media_reader import ExternalMediaReader
from foundry_lite.application.ports.media_processor import MediaProcessorAdapter
from foundry_lite.application.ports.media_storage import MediaStorageAdapter
from foundry_lite.application.ports.ontology_repository import PropertyClassificationRow
from foundry_lite.application.ports.search_adapter import SearchAdapter
from foundry_lite.application.ports.workflow_adapter import WorkflowAdapter
from foundry_lite.application.services.object_store.query_cursor import (
    require_object_query_cursor_signing_key_for_runtime,
)
from foundry_lite.infrastructure.adapters import (
    AsrProcessorAdapter,
    DuckDBComputeAdapter,
    ElasticsearchAdapter,
    ElasticsearchAdapterConfig,
    ElasticsearchContentIndexAdapter,
    FakeComputeAdapter,
    FakeConnectorAdapter,
    FakeDatasetStorageAdapter,
    FakeSearchAdapter,
    FakeStreamAdapter,
    FakeWorkflowAdapter,
    IcebergDatasetStorageAdapter,
    IcebergDatasetStorageAdapterConfig,
    ImageProcessorAdapter,
    LocalConnectorAdapter,
    LocalContentIndexAdapter,
    LocalDatasetStorageAdapter,
    LocalEmbeddingAdapter,
    LocalExternalMediaReader,
    LocalMediaStorageAdapter,
    LocalPreviewRendererAdapter,
    LocalSearchAdapter,
    LocalStreamAdapter,
    LocalWorkflowAdapter,
    OcrProcessorAdapter,
    PdfTextProcessorAdapter,
    S3DatasetStorageAdapter,
    S3DatasetStorageAdapterConfig,
    S3ExternalMediaReader,
    S3ExternalMediaReaderConfig,
    S3MediaStorageAdapter,
    S3MediaStorageConfig,
    SparkComputeAdapter,
    TemporalWorkflowAdapter,
    TemporalWorkflowAdapterConfig,
    VideoProbeProcessorAdapter,
    VideoSceneFrameProcessorAdapter,
)
from foundry_lite.infrastructure.adapters.asr_processor import _faster_whisper_asr_engine
from foundry_lite.infrastructure.adapters.local_embedding import (
    FASTEMBED_MODEL_VERSION,
    _fastembed_embedding_engine,
)
from foundry_lite.infrastructure.adapters.ocr_processor import _tesseract_ocr_engine
from foundry_lite.infrastructure.adapters.video_probe_processor import (
    _ffmpeg_scene_frame_extractor,
    _ffprobe_video_probe_runner,
)
from foundry_lite.infrastructure.repositories import (
    SqlAlchemyActionRepository,
    SqlAlchemyDatasetQualityRepository,
    SqlAlchemyDatasetRepository,
    SqlAlchemyDatasetTransactionRepository,
    SqlAlchemyDatasetVersionRepository,
    SqlAlchemyDestructiveDevelopmentAdmin,
    SqlAlchemyErasureRepository,
    SqlAlchemyInsightReviewRepository,
    SqlAlchemyMaterializationRepository,
    SqlAlchemyMediaAccessCacheRepository,
    SqlAlchemyMediaDerivativeRepository,
    SqlAlchemyMediaReferenceBindingRepository,
    SqlAlchemyMediaRepository,
    SqlAlchemyMetadataRepository,
    SqlAlchemyObjectIndexRepository,
    SqlAlchemyObjectReadRepository,
    SqlAlchemyObjectSetRepository,
    SqlAlchemyOntologyRepository,
    SqlAlchemyRuntimeRepository,
    SqlAlchemyTransformRepository,
)
from foundry_lite.infrastructure.secrets import secret_provider_from_env
from foundry_lite.security.policy import ClassificationProvider, PolicyService

_RUNTIME_PROFILE_ENV = "FOUNDRY_LITE_RUNTIME_PROFILE"
_SCHEMA_MUTATION_PROTECTED_PROFILES = frozenset({"production", "prod", "staging", "stage"})


def _classification_provider(
    engine: Engine, ontology_repository: SqlAlchemyOntologyRepository
) -> ClassificationProvider:
    """Read the active ontology's classified properties for a tenant.

    Keeps the security policy ontology-driven (no hardcoded sensitive names) while
    leaving the policy itself free of any database/vendor SDK dependency.
    """

    def provider(tenant_id: str) -> list[PropertyClassificationRow]:
        with engine.begin() as conn:
            return ontology_repository.active_property_classifications(transaction=conn, tenant_id=tenant_id)

    return provider


def create_local_core_dependencies(
    *,
    db_url: str | None = None,
    storage_root: str | Path | None = None,
    adapter_profile: str = "local",
) -> CoreDependencies:
    """Build the local composition root used by CLI, API, tests, and demos."""

    require_object_query_cursor_signing_key_for_runtime()
    root = Path(storage_root or ".foundry-lite").resolve()
    root.mkdir(parents=True, exist_ok=True)
    object_storage_root = root / "object-storage"
    object_storage_root.mkdir(parents=True, exist_ok=True)
    media_storage_root = root / "media-storage"
    media_storage_root.mkdir(parents=True, exist_ok=True)

    storage_adapter = _dataset_storage_adapter(adapter_profile, object_storage_root)
    media_storage = _media_storage_adapter(adapter_profile, media_storage_root)
    media_processor = _media_processor_adapter(adapter_profile)
    content_index_adapter = _content_index_adapter(adapter_profile)
    embedding_model_adapter = LocalEmbeddingAdapter(
        embedding_engine=_fastembed_embedding_engine, model_version=FASTEMBED_MODEL_VERSION
    )
    compute_adapter = _compute_adapter(adapter_profile)
    connector_adapter = _connector_adapter(adapter_profile)
    search_adapter = _search_adapter(adapter_profile)
    stream_adapter = _stream_adapter(adapter_profile)
    workflow_adapter = _workflow_adapter(adapter_profile)
    database_url = db_url or f"sqlite:///{root / 'foundry-lite.db'}"
    engine = create_engine(database_url, future=True)
    ontology_repository = SqlAlchemyOntologyRepository(engine)
    allow_schema_mutation = _schema_mutation_allowed_from_env()
    return CoreDependencies(
        root=root,
        storage_root=object_storage_root,
        engine=engine,
        policy=PolicyService(classification_provider=_classification_provider(engine, ontology_repository)),
        action_repository=SqlAlchemyActionRepository(engine),
        ontology_repository=ontology_repository,
        transform_repository=SqlAlchemyTransformRepository(engine),
        materialization_repository=SqlAlchemyMaterializationRepository(engine),
        dataset_quality_repository=SqlAlchemyDatasetQualityRepository(engine),
        compute_adapter=compute_adapter,
        connector_adapter=connector_adapter,
        metadata_repository=SqlAlchemyMetadataRepository(
            engine,
            allow_schema_mutation=allow_schema_mutation,
        ),
        destructive_development_admin=SqlAlchemyDestructiveDevelopmentAdmin(
            engine,
            allow_schema_mutation=allow_schema_mutation,
        ),
        dataset_repository=SqlAlchemyDatasetRepository(engine),
        dataset_transaction_repository=SqlAlchemyDatasetTransactionRepository(engine),
        dataset_version_repository=SqlAlchemyDatasetVersionRepository(engine),
        insight_review_repository=SqlAlchemyInsightReviewRepository(engine),
        object_index_repository=SqlAlchemyObjectIndexRepository(engine),
        object_read_repository=SqlAlchemyObjectReadRepository(engine),
        object_set_repository=SqlAlchemyObjectSetRepository(engine),
        runtime_repository=SqlAlchemyRuntimeRepository(engine),
        erasure_repository=SqlAlchemyErasureRepository(engine),
        media_repository=SqlAlchemyMediaRepository(engine),
        media_derivative_repository=SqlAlchemyMediaDerivativeRepository(engine),
        media_reference_binding_repository=SqlAlchemyMediaReferenceBindingRepository(engine),
        media_access_cache_repository=SqlAlchemyMediaAccessCacheRepository(engine),
        media_processor=media_processor,
        media_preview_renderer=LocalPreviewRendererAdapter(),
        external_media_reader=_external_media_reader(adapter_profile),
        dataset_storage=storage_adapter,
        media_storage=media_storage,
        content_index_adapter=content_index_adapter,
        embedding_model_adapter=embedding_model_adapter,
        search_adapter=search_adapter,
        secret_provider=secret_provider_from_env(),
        stream_adapter=stream_adapter,
        workflow_adapter=workflow_adapter,
    )


def _dataset_storage_adapter(adapter_profile: str, object_storage_root: Path) -> DatasetStorageAdapter:
    if adapter_profile == "local":
        return LocalDatasetStorageAdapter(object_storage_root)
    if adapter_profile == "fake-storage":
        return FakeDatasetStorageAdapter(object_storage_root)
    if adapter_profile == "s3-storage":
        return S3DatasetStorageAdapter(_s3_storage_config(object_storage_root))
    if adapter_profile == "iceberg":
        return IcebergDatasetStorageAdapter(_iceberg_storage_config(object_storage_root))
    raise ValueError(f"unknown adapter profile: {adapter_profile}")


def _media_storage_adapter(adapter_profile: str, media_storage_root: Path) -> MediaStorageAdapter:
    # Media storage is selectable independently of dataset storage; v1 ships local only
    # (the S3 media profile lands with the media S3 ratchet, doc §6.1 direct upload).
    media_profile = os.getenv("FOUNDRY_LITE_MEDIA_STORAGE_PROFILE", adapter_profile)
    if media_profile == "s3-media":
        return S3MediaStorageAdapter(_s3_media_storage_config())
    if media_profile in {"local", "fake-storage", "s3-storage", "iceberg"}:
        return LocalMediaStorageAdapter(media_storage_root)
    raise ValueError(f"unknown media storage profile: {media_profile}")


def _external_media_reader(adapter_profile: str) -> ExternalMediaReader:
    # Virtual media sets are pointers to an external source (no byte copy). The default reader
    # has no connector bundled (reports external_reader_unavailable); the s3-external profile
    # reaches out to a real S3/MinIO source via HEAD, reusing the dataset S3 connection env.
    # Selected via the existing adapter profile (no dedicated env var).
    if adapter_profile == "s3-external":
        return S3ExternalMediaReader(_s3_external_media_reader_config())
    return LocalExternalMediaReader()


def _s3_external_media_reader_config() -> S3ExternalMediaReaderConfig:
    return S3ExternalMediaReaderConfig(
        endpoint_url=os.getenv("FOUNDRY_LITE_S3_ENDPOINT_URL"),
        access_key_id=os.getenv("FOUNDRY_LITE_S3_ACCESS_KEY_ID"),
        secret_access_key=os.getenv("FOUNDRY_LITE_S3_SECRET_ACCESS_KEY"),
        region_name=os.getenv("FOUNDRY_LITE_S3_REGION", "us-east-1"),
    )


def _media_processor_adapter(adapter_profile: str) -> MediaProcessorAdapter:
    # Media processing is selectable independently of storage; v1 ships the local PDF
    # raw-text processor (pypdf). External processors (OCR/ASR/FFmpeg) land in later ratchets.
    processor_profile = os.getenv("FOUNDRY_LITE_MEDIA_PROCESSOR_PROFILE", adapter_profile)
    if processor_profile == "image-pillow":
        return ImageProcessorAdapter()
    if processor_profile == "ffprobe":
        return VideoProbeProcessorAdapter(probe_runner=_ffprobe_video_probe_runner)
    if processor_profile == "video-scene-frames":
        return VideoSceneFrameProcessorAdapter(scene_frame_extractor=_ffmpeg_scene_frame_extractor)
    if processor_profile == "ocr-tesseract":
        return OcrProcessorAdapter(ocr_engine=_tesseract_ocr_engine)
    if processor_profile == "asr-whisper":
        return AsrProcessorAdapter(asr_engine=_faster_whisper_asr_engine)
    if processor_profile in {"local", "fake-storage", "s3-storage", "iceberg", "s3-media", "pdf-pypdf"}:
        return PdfTextProcessorAdapter()
    raise ValueError(f"unknown media processor profile: {processor_profile}")


def _content_index_adapter(adapter_profile: str) -> ContentIndexAdapter:
    # Content search index is selectable independently; v1 default is the in-memory local
    # projection. Elasticsearch is the production backend (lexical-first; dense/hybrid = M8).
    index_profile = os.getenv("FOUNDRY_LITE_CONTENT_INDEX_PROFILE", adapter_profile)
    if index_profile == "elasticsearch":
        endpoint = os.getenv("FOUNDRY_LITE_ELASTICSEARCH_URL", "http://localhost:9200")
        return ElasticsearchContentIndexAdapter(ElasticsearchAdapterConfig(endpoint=endpoint))
    if index_profile in {"local", "fake-storage", "s3-storage", "iceberg", "s3-media"}:
        return LocalContentIndexAdapter()
    raise ValueError(f"unknown content index profile: {index_profile}")


def _s3_media_storage_config() -> S3MediaStorageConfig:
    # Reuse the dataset S3 connection env with a dedicated media key sub-namespace.
    return S3MediaStorageConfig(
        bucket=os.environ["FOUNDRY_LITE_S3_BUCKET"],
        endpoint_url=os.getenv("FOUNDRY_LITE_S3_ENDPOINT_URL"),
        access_key_id=os.getenv("FOUNDRY_LITE_S3_ACCESS_KEY_ID"),
        secret_access_key=os.getenv("FOUNDRY_LITE_S3_SECRET_ACCESS_KEY"),
        region_name=os.getenv("FOUNDRY_LITE_S3_REGION", "us-east-1"),
        prefix=f"{os.getenv('FOUNDRY_LITE_S3_PREFIX', 'foundry-lite')}/media",
        should_create_bucket_if_missing=os.getenv("FOUNDRY_LITE_S3_CREATE_BUCKET", "1") != "0",
    )


def _compute_adapter(adapter_profile: str) -> DuckDBComputeAdapter:
    # Compute is selectable independently of storage (like search) so a Spark
    # runner can transform datasets backed by any storage profile.
    compute_profile = os.getenv("FOUNDRY_LITE_COMPUTE_PROFILE", adapter_profile)
    if compute_profile == "spark":
        return SparkComputeAdapter()
    if compute_profile in {"local", "s3-storage", "iceberg"}:
        return DuckDBComputeAdapter()
    if compute_profile == "fake-storage":
        return FakeComputeAdapter()
    raise ValueError(f"unknown compute profile: {compute_profile}")


def _connector_adapter(adapter_profile: str) -> LocalConnectorAdapter:
    if adapter_profile in {"local", "s3-storage", "iceberg"}:
        return LocalConnectorAdapter()
    if adapter_profile == "fake-storage":
        return FakeConnectorAdapter()
    raise ValueError(f"unknown adapter profile: {adapter_profile}")


def _search_adapter(adapter_profile: str) -> SearchAdapter:
    search_profile = os.getenv("FOUNDRY_LITE_SEARCH_PROFILE", adapter_profile)
    if search_profile in {"local", "s3-storage", "iceberg"}:
        return LocalSearchAdapter()
    if search_profile == "fake-storage":
        return FakeSearchAdapter()
    if search_profile == "elasticsearch":
        endpoint = os.getenv("FOUNDRY_LITE_ELASTICSEARCH_URL", "http://localhost:9200")
        return ElasticsearchAdapter(ElasticsearchAdapterConfig(endpoint=endpoint))
    raise ValueError(f"unknown search adapter profile: {search_profile}")


def _stream_adapter(adapter_profile: str) -> LocalStreamAdapter:
    if adapter_profile in {"local", "s3-storage", "iceberg"}:
        return LocalStreamAdapter()
    if adapter_profile == "fake-storage":
        return FakeStreamAdapter()
    raise ValueError(f"unknown adapter profile: {adapter_profile}")


def _workflow_adapter(adapter_profile: str) -> WorkflowAdapter:
    # Workflow orchestration is selectable independently of storage (like compute
    # and search) so a Temporal cluster can drive durable workflows regardless of
    # which storage profile backs datasets.
    workflow_profile = os.getenv("FOUNDRY_LITE_WORKFLOW_PROFILE", adapter_profile)
    if workflow_profile == "temporal":
        return TemporalWorkflowAdapter(_temporal_workflow_config())
    if workflow_profile in {"local", "s3-storage", "iceberg"}:
        return LocalWorkflowAdapter()
    if workflow_profile == "fake-storage":
        return FakeWorkflowAdapter()
    raise ValueError(f"unknown workflow profile: {workflow_profile}")


def _schema_mutation_allowed_from_env() -> bool:
    runtime_profile = os.getenv(_RUNTIME_PROFILE_ENV, "local").strip().casefold()
    return runtime_profile not in _SCHEMA_MUTATION_PROTECTED_PROFILES


def _temporal_workflow_config() -> TemporalWorkflowAdapterConfig:
    return TemporalWorkflowAdapterConfig(
        address=os.getenv("FOUNDRY_LITE_TEMPORAL_ADDRESS", "localhost:7233"),
        namespace=os.getenv("FOUNDRY_LITE_TEMPORAL_NAMESPACE", "default"),
        task_queue=os.getenv("FOUNDRY_LITE_TEMPORAL_TASK_QUEUE", "foundry-lite"),
        execution_timeout_seconds=int(os.getenv("FOUNDRY_LITE_TEMPORAL_EXECUTION_TIMEOUT", "300")),
    )


def _s3_storage_config(object_storage_root: Path) -> S3DatasetStorageAdapterConfig:
    return S3DatasetStorageAdapterConfig(
        bucket=os.environ["FOUNDRY_LITE_S3_BUCKET"],
        endpoint_url=os.getenv("FOUNDRY_LITE_S3_ENDPOINT_URL"),
        access_key_id=os.getenv("FOUNDRY_LITE_S3_ACCESS_KEY_ID"),
        secret_access_key=os.getenv("FOUNDRY_LITE_S3_SECRET_ACCESS_KEY"),
        region_name=os.getenv("FOUNDRY_LITE_S3_REGION", "us-east-1"),
        prefix=os.getenv("FOUNDRY_LITE_S3_PREFIX", "foundry-lite"),
        cache_root=object_storage_root / "_s3-cache",
        should_create_bucket_if_missing=os.getenv("FOUNDRY_LITE_S3_CREATE_BUCKET", "1") != "0",
    )


def _iceberg_storage_config(object_storage_root: Path) -> IcebergDatasetStorageAdapterConfig:
    return IcebergDatasetStorageAdapterConfig(
        catalog_uri=os.environ["FOUNDRY_LITE_ICEBERG_CATALOG_URI"],
        warehouse=os.environ["FOUNDRY_LITE_ICEBERG_WAREHOUSE"],
        namespace=os.getenv("FOUNDRY_LITE_ICEBERG_NAMESPACE", "foundry_lite"),
        cache_root=object_storage_root / "_iceberg-cache",
        s3_endpoint_url=os.getenv("FOUNDRY_LITE_S3_ENDPOINT_URL"),
        s3_access_key_id=os.getenv("FOUNDRY_LITE_S3_ACCESS_KEY_ID"),
        s3_secret_access_key=os.getenv("FOUNDRY_LITE_S3_SECRET_ACCESS_KEY"),
        s3_region=os.getenv("FOUNDRY_LITE_S3_REGION", "us-east-1"),
    )
