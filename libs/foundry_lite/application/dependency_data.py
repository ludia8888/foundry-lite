"""Data bounded-context dependency bundle.

Held beside the composition root rather than inside it, for the same reason as
[SourceDependencies][foundry_lite.application.dependency_source]: ``dependencies.py`` is capped
at 500 lines by the module-size gate, and this bundle grows by one field per data capability.
"""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.ports import (
    ComputeAdapter,
    DatasetQualityRepository,
    DatasetRepository,
    DatasetStorageAdapter,
    DatasetTransactionRepository,
    DatasetVersionRepository,
    MaterializationRepository,
    OntologyRepository,
    PipelineRepository,
    ResourceCatalogRepository,
    TransformRepository,
)
from foundry_lite.application.ports.code_execution import CodeExecutionAdapter
from foundry_lite.application.ports.ontology_branch_repository import OntologyBranchRepository
from foundry_lite.application.ports.pipeline_execution_repository import PipelineExecutionRepository


@dataclass(frozen=True)
class DataDependencies:
    ontology_repository: OntologyRepository
    ontology_branch_repository: OntologyBranchRepository
    pipeline_repository: PipelineRepository
    pipeline_execution_repository: PipelineExecutionRepository
    resource_catalog_repository: ResourceCatalogRepository
    transform_repository: TransformRepository
    materialization_repository: MaterializationRepository
    dataset_quality_repository: DatasetQualityRepository
    compute_adapter: ComputeAdapter
    # The sandbox that runs untrusted user code. It reached the application layer with Python
    # ontology functions: a transform gets it through the compute adapter, but a function is
    # executed by an application service, so the service needs the port directly.
    code_execution_adapter: CodeExecutionAdapter
    dataset_repository: DatasetRepository
    dataset_transaction_repository: DatasetTransactionRepository
    dataset_version_repository: DatasetVersionRepository
    dataset_storage: DatasetStorageAdapter
