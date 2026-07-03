"""Action bounded-context service group."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.services.action_apply_service import ActionApplyService
from foundry_lite.application.services.action_batch_service import ActionBatchApplyService
from foundry_lite.application.services.action_service import ActionService
from foundry_lite.application.services.action_validation_service import ActionValidationService
from foundry_lite.application.services.action_writeback_service import ActionWritebackService
from foundry_lite.application.services.base import CoreService, build_service


@dataclass(frozen=True)
class ActionServices:
    apply: ActionApplyService
    batch_apply: ActionBatchApplyService
    entrypoint: ActionService
    validation: ActionValidationService
    writeback: ActionWritebackService

    @classmethod
    def create(cls, dependencies: CoreDependencies) -> ActionServices:
        return cls(
            apply=build_service(ActionApplyService, dependencies),
            batch_apply=build_service(ActionBatchApplyService, dependencies),
            entrypoint=build_service(ActionService, dependencies),
            validation=build_service(ActionValidationService, dependencies),
            writeback=build_service(ActionWritebackService, dependencies),
        )

    def items(self) -> tuple[CoreService, ...]:
        return (self.apply, self.batch_apply, self.entrypoint, self.validation, self.writeback)
