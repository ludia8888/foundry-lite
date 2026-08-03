"""Action bounded-context service group."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.services.action_apply_service import ActionApplyService
from foundry_lite.application.services.action_async_run_service import ActionAsyncRunService
from foundry_lite.application.services.action_batch_service import ActionBatchApplyService
from foundry_lite.application.services.action_definition_service import ActionDefinitionService
from foundry_lite.application.services.action_distributed_run_service import ActionDistributedRunService
from foundry_lite.application.services.action_planning_service import ActionPlanningService
from foundry_lite.application.services.action_service import ActionService
from foundry_lite.application.services.action_validation_service import ActionValidationService
from foundry_lite.application.services.action_writeback_service import ActionWritebackService
from foundry_lite.application.services.base import CoreService, build_service


@dataclass(frozen=True)
class ActionServices:
    apply: ActionApplyService
    async_run: ActionAsyncRunService
    batch_apply: ActionBatchApplyService
    definition: ActionDefinitionService
    distributed: ActionDistributedRunService
    entrypoint: ActionService
    planning: ActionPlanningService
    validation: ActionValidationService
    writeback: ActionWritebackService

    @classmethod
    def create(cls, dependencies: CoreDependencies) -> ActionServices:
        return cls(
            apply=build_service(ActionApplyService, dependencies),
            async_run=build_service(ActionAsyncRunService, dependencies),
            batch_apply=build_service(ActionBatchApplyService, dependencies),
            definition=build_service(ActionDefinitionService, dependencies),
            distributed=build_service(ActionDistributedRunService, dependencies),
            entrypoint=build_service(ActionService, dependencies),
            planning=build_service(ActionPlanningService, dependencies),
            validation=build_service(ActionValidationService, dependencies),
            writeback=build_service(ActionWritebackService, dependencies),
        )

    def items(self) -> tuple[CoreService, ...]:
        return (
            self.apply,
            self.async_run,
            self.batch_apply,
            self.definition,
            self.distributed,
            self.entrypoint,
            self.planning,
            self.validation,
            self.writeback,
        )
