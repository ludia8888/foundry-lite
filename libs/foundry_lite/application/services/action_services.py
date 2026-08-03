"""Action bounded-context service group."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.services.action_service_registry import (
    ActionApplyService,
    ActionAsyncRunService,
    ActionBatchApplyService,
    ActionDefinitionService,
    ActionDistributedRunService,
    ActionLogRevertService,
    ActionPlanningService,
    ActionService,
    ActionValidationService,
    ActionWritebackService,
)
from foundry_lite.application.services.base import CoreService, build_service


@dataclass(frozen=True)
class ActionServices:
    apply: ActionApplyService
    async_run: ActionAsyncRunService
    batch_apply: ActionBatchApplyService
    definition: ActionDefinitionService
    distributed: ActionDistributedRunService
    log_revert: ActionLogRevertService
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
            log_revert=build_service(ActionLogRevertService, dependencies),
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
            self.log_revert,
            self.entrypoint,
            self.planning,
            self.validation,
            self.writeback,
        )
