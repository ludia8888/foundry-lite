"""Application service group for the Transform bounded context."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.services.base import CoreService, build_service
from foundry_lite.application.services.transform_definition_service import TransformDefinitionService
from foundry_lite.application.services.transform_dlq_replay_service import TransformDlqReplayService
from foundry_lite.application.services.transform_graph import TransformGraphService
from foundry_lite.application.services.transform_run_service import TransformRunService
from foundry_lite.application.services.transform_scheduler_service import TransformSchedulerService
from foundry_lite.application.services.transform_service import TransformService


@dataclass(frozen=True)
class TransformServices:
    """Transform use-case services plus the stable compatibility entrypoint."""

    definition: TransformDefinitionService
    dlq_replay: TransformDlqReplayService
    entrypoint: TransformService
    graph: TransformGraphService
    run: TransformRunService
    scheduler: TransformSchedulerService

    @classmethod
    def create(cls, dependencies: CoreDependencies) -> TransformServices:
        return cls(
            definition=build_service(TransformDefinitionService, dependencies),
            dlq_replay=build_service(TransformDlqReplayService, dependencies),
            entrypoint=build_service(TransformService, dependencies),
            graph=build_service(TransformGraphService, dependencies),
            run=build_service(TransformRunService, dependencies),
            scheduler=build_service(TransformSchedulerService, dependencies),
        )

    def items(self) -> tuple[CoreService, ...]:
        return (self.definition, self.dlq_replay, self.entrypoint, self.graph, self.run, self.scheduler)
