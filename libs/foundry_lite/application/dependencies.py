from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from foundry_lite.application.ports import (
    ActionRepository,
    ComputeAdapter,
    DatasetQualityRepository,
    DatasetRepository,
    DatasetStorageAdapter,
    DatasetTransactionRepository,
    DatasetVersionRepository,
    MaterializationRepository,
    MetadataRepository,
    ObjectIndexRepository,
    ObjectReadRepository,
    ObjectSetRepository,
    OntologyRepository,
    RuntimeRepository,
    TransactionManager,
    TransformRepository,
)
from foundry_lite.application.ports.connector_adapter import ConnectorAdapter
from foundry_lite.application.ports.insight_review_repository import InsightReviewRepository
from foundry_lite.application.ports.search_adapter import SearchAdapter
from foundry_lite.application.ports.secret_provider import SecretProvider
from foundry_lite.application.ports.stream_adapter import StreamAdapter
from foundry_lite.application.ports.workflow_adapter import WorkflowAdapter
from foundry_lite.security.policy import PolicyService


@dataclass(frozen=True)
class CoreDependencies:
    """Dependencies that compose the core facade without hard-coding local infrastructure."""

    root: Path
    storage_root: Path
    engine: TransactionManager
    policy: PolicyService
    action_repository: ActionRepository
    ontology_repository: OntologyRepository
    transform_repository: TransformRepository
    materialization_repository: MaterializationRepository
    dataset_quality_repository: DatasetQualityRepository
    compute_adapter: ComputeAdapter
    connector_adapter: ConnectorAdapter
    metadata_repository: MetadataRepository
    dataset_repository: DatasetRepository
    dataset_transaction_repository: DatasetTransactionRepository
    dataset_version_repository: DatasetVersionRepository
    insight_review_repository: InsightReviewRepository
    object_index_repository: ObjectIndexRepository
    object_read_repository: ObjectReadRepository
    object_set_repository: ObjectSetRepository
    runtime_repository: RuntimeRepository
    dataset_storage: DatasetStorageAdapter
    search_adapter: SearchAdapter
    secret_provider: SecretProvider
    stream_adapter: StreamAdapter
    workflow_adapter: WorkflowAdapter
