from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.ports.dataset_storage import DatasetStorageAdapter
from foundry_lite.application.ports.search_adapter import SearchAdapter
from foundry_lite.infrastructure.adapters import (
    DuckDBComputeAdapter,
    ElasticsearchAdapter,
    ElasticsearchAdapterConfig,
    FakeComputeAdapter,
    FakeConnectorAdapter,
    FakeDatasetStorageAdapter,
    FakeSearchAdapter,
    FakeStreamAdapter,
    FakeWorkflowAdapter,
    IcebergDatasetStorageAdapter,
    IcebergDatasetStorageAdapterConfig,
    LocalConnectorAdapter,
    LocalDatasetStorageAdapter,
    LocalSearchAdapter,
    LocalStreamAdapter,
    LocalWorkflowAdapter,
    S3DatasetStorageAdapter,
    S3DatasetStorageAdapterConfig,
)
from foundry_lite.infrastructure.repositories import (
    SqlAlchemyActionRepository,
    SqlAlchemyDatasetQualityRepository,
    SqlAlchemyDatasetRepository,
    SqlAlchemyDatasetTransactionRepository,
    SqlAlchemyDatasetVersionRepository,
    SqlAlchemyMaterializationRepository,
    SqlAlchemyMetadataRepository,
    SqlAlchemyObjectIndexRepository,
    SqlAlchemyObjectReadRepository,
    SqlAlchemyObjectSetRepository,
    SqlAlchemyOntologyRepository,
    SqlAlchemyRuntimeRepository,
    SqlAlchemyTransformRepository,
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
    connector_adapter = _connector_adapter(adapter_profile)
    search_adapter = _search_adapter(adapter_profile)
    stream_adapter = _stream_adapter(adapter_profile)
    workflow_adapter = _workflow_adapter(adapter_profile)
    database_url = db_url or f"sqlite:///{root / 'foundry-lite.db'}"
    engine = create_engine(database_url, future=True)
    return CoreDependencies(
        root=root,
        storage_root=object_storage_root,
        engine=engine,
        policy=PolicyService(),
        action_repository=SqlAlchemyActionRepository(engine),
        ontology_repository=SqlAlchemyOntologyRepository(engine),
        transform_repository=SqlAlchemyTransformRepository(engine),
        materialization_repository=SqlAlchemyMaterializationRepository(engine),
        dataset_quality_repository=SqlAlchemyDatasetQualityRepository(engine),
        compute_adapter=compute_adapter,
        connector_adapter=connector_adapter,
        metadata_repository=SqlAlchemyMetadataRepository(engine),
        dataset_repository=SqlAlchemyDatasetRepository(engine),
        dataset_transaction_repository=SqlAlchemyDatasetTransactionRepository(engine),
        dataset_version_repository=SqlAlchemyDatasetVersionRepository(engine),
        object_index_repository=SqlAlchemyObjectIndexRepository(engine),
        object_read_repository=SqlAlchemyObjectReadRepository(engine),
        object_set_repository=SqlAlchemyObjectSetRepository(engine),
        runtime_repository=SqlAlchemyRuntimeRepository(engine),
        dataset_storage=storage_adapter,
        search_adapter=search_adapter,
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


def _compute_adapter(adapter_profile: str) -> DuckDBComputeAdapter:
    if adapter_profile in {"local", "s3-storage", "iceberg"}:
        return DuckDBComputeAdapter()
    if adapter_profile == "fake-storage":
        return FakeComputeAdapter()
    raise ValueError(f"unknown adapter profile: {adapter_profile}")


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


def _workflow_adapter(adapter_profile: str) -> LocalWorkflowAdapter:
    if adapter_profile in {"local", "s3-storage", "iceberg"}:
        return LocalWorkflowAdapter()
    if adapter_profile == "fake-storage":
        return FakeWorkflowAdapter()
    raise ValueError(f"unknown adapter profile: {adapter_profile}")


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
