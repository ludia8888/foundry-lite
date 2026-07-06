"""Application service group for the Pipeline Builder bounded context."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.services.base import CoreService, build_service
from foundry_lite.application.services.pipeline_compiler_service import PipelineCompilerService
from foundry_lite.application.services.pipeline_definition_service import PipelineDefinitionService
from foundry_lite.application.services.pipeline_governance_service import PipelineGovernanceService
from foundry_lite.application.services.pipeline_graph_validation_service import PipelineGraphValidationService
from foundry_lite.application.services.pipeline_run_service import PipelineRunService
from foundry_lite.application.services.pipeline_service import PipelineService


@dataclass(frozen=True)
class PipelineServices:
    """Pipeline Builder use-case services plus the stable entrypoint."""

    compiler: PipelineCompilerService
    definition: PipelineDefinitionService
    entrypoint: PipelineService
    governance: PipelineGovernanceService
    graph_validation: PipelineGraphValidationService
    run: PipelineRunService

    @classmethod
    def create(cls, dependencies: CoreDependencies) -> PipelineServices:
        return cls(
            compiler=build_service(PipelineCompilerService, dependencies),
            definition=build_service(PipelineDefinitionService, dependencies),
            entrypoint=build_service(PipelineService, dependencies),
            governance=build_service(PipelineGovernanceService, dependencies),
            graph_validation=build_service(PipelineGraphValidationService, dependencies),
            run=build_service(PipelineRunService, dependencies),
        )

    def items(self) -> tuple[CoreService, ...]:
        return (self.compiler, self.definition, self.entrypoint, self.governance, self.graph_validation, self.run)
