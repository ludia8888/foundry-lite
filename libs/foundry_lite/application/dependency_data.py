"""Data bounded-context dependency bundle.

Held beside the composition root rather than inside it, for the same reason as
[SourceDependencies][foundry_lite.application.dependency_source]: ``dependencies.py`` is capped
at 500 lines by the module-size gate, and this bundle grows by one field per data capability.
"""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.dependency_compat import required_dependency
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
from foundry_lite.application.ports.ontology_definition_reader import OntologyDefinitionReader
from foundry_lite.application.ports.pipeline_execution_repository import PipelineExecutionRepository
from foundry_lite.application.ports.transform_source_store import TransformSourceStore


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
    transform_source_store: TransformSourceStore | None = None
    ontology_definition_reader: OntologyDefinitionReader | None = None


class DataDependencyAccessors:
    """Typed compatibility accessors owned by the data dependency bundle."""

    data: DataDependencies

    @property
    def ontology_repository(self) -> OntologyRepository:
        return self.data.ontology_repository

    @property
    def ontology_branch_repository(self) -> OntologyBranchRepository:
        return self.data.ontology_branch_repository

    @property
    def pipeline_repository(self) -> PipelineRepository:
        return self.data.pipeline_repository

    @property
    def pipeline_execution_repository(self) -> PipelineExecutionRepository:
        return self.data.pipeline_execution_repository

    @property
    def transform_repository(self) -> TransformRepository:
        return self.data.transform_repository

    @property
    def resource_catalog_repository(self) -> ResourceCatalogRepository:
        return self.data.resource_catalog_repository

    @property
    def materialization_repository(self) -> MaterializationRepository:
        return self.data.materialization_repository

    @property
    def dataset_quality_repository(self) -> DatasetQualityRepository:
        return self.data.dataset_quality_repository

    @property
    def compute_adapter(self) -> ComputeAdapter:
        return self.data.compute_adapter

    @property
    def code_execution_adapter(self) -> CodeExecutionAdapter:
        return self.data.code_execution_adapter

    @property
    def dataset_repository(self) -> DatasetRepository:
        return self.data.dataset_repository

    @property
    def dataset_transaction_repository(self) -> DatasetTransactionRepository:
        return self.data.dataset_transaction_repository

    @property
    def dataset_version_repository(self) -> DatasetVersionRepository:
        return self.data.dataset_version_repository

    @property
    def dataset_storage(self) -> DatasetStorageAdapter:
        return self.data.dataset_storage

    @property
    def transform_source_store(self) -> TransformSourceStore:
        return required_dependency(self.data.transform_source_store, "transform source store unavailable")

    @property
    def ontology_definition_reader(self) -> OntologyDefinitionReader:
        return required_dependency(
            self.data.ontology_definition_reader,
            "ontology definition reader unavailable",
        )
