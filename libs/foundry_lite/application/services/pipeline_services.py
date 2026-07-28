"""Application service group for the Pipeline Builder bounded context."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.services.base import CoreService, build_service
from foundry_lite.application.services.pipeline_definition_service_types import (
    ExactCommittedDatasetVersionReader,
    PipelineCatalogService,
    PipelineCompilerService,
    PipelineDefinitionService,
    PipelineDeploymentService,
    PipelineGovernanceService,
    PipelineGraphValidationService,
)
from foundry_lite.application.services.pipeline_runtime_service_types import (
    PipelineGraphV2ExecutionService,
    PipelineGraphV2RunCoordinatorService,
    PipelinePreviewService,
    PipelineRunService,
    PipelineSchedulerService,
    PipelineService,
)


@dataclass(frozen=True)
class PipelineServices:
    """Pipeline Builder use-case services plus the stable entrypoint."""

    catalog: PipelineCatalogService
    compiler: PipelineCompilerService
    dataset_reader: ExactCommittedDatasetVersionReader
    definition: PipelineDefinitionService
    deployment: PipelineDeploymentService
    entrypoint: PipelineService
    governance: PipelineGovernanceService
    graph_v2_execution: PipelineGraphV2ExecutionService
    graph_v2_run_coordinator: PipelineGraphV2RunCoordinatorService
    graph_validation: PipelineGraphValidationService
    preview: PipelinePreviewService
    run: PipelineRunService
    scheduler: PipelineSchedulerService

    @classmethod
    def create(cls, dependencies: CoreDependencies) -> PipelineServices:
        return cls(
            catalog=build_service(PipelineCatalogService, dependencies),
            compiler=build_service(PipelineCompilerService, dependencies),
            dataset_reader=build_service(ExactCommittedDatasetVersionReader, dependencies),
            definition=build_service(PipelineDefinitionService, dependencies),
            deployment=build_service(PipelineDeploymentService, dependencies),
            entrypoint=build_service(PipelineService, dependencies),
            governance=build_service(PipelineGovernanceService, dependencies),
            graph_v2_execution=build_service(PipelineGraphV2ExecutionService, dependencies),
            graph_v2_run_coordinator=build_service(
                PipelineGraphV2RunCoordinatorService,
                dependencies,
            ),
            graph_validation=build_service(PipelineGraphValidationService, dependencies),
            preview=build_service(PipelinePreviewService, dependencies),
            run=build_service(PipelineRunService, dependencies),
            scheduler=build_service(PipelineSchedulerService, dependencies),
        )

    def items(self) -> tuple[CoreService, ...]:
        return (
            self.catalog,
            self.compiler,
            self.dataset_reader,
            self.definition,
            self.deployment,
            self.entrypoint,
            self.governance,
            self.graph_v2_execution,
            self.graph_v2_run_coordinator,
            self.graph_validation,
            self.preview,
            self.run,
            self.scheduler,
        )
