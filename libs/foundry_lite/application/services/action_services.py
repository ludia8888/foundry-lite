"""Action bounded-context service group."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.services.action_execution_service_registry import (
    ActionApplyService,
    ActionAsyncRunService,
    ActionBatchApplyService,
    ActionDistributedRunService,
    ActionLogRevertService,
    ActionMonitoringAlertService,
    ActionWritebackService,
)
from foundry_lite.application.services.action_service import ActionService
from foundry_lite.application.services.action_service_registry import (
    ActionBranchService,
    ActionDefinitionService,
    ActionEffectOperatorService,
    ActionLogOntologyService,
    ActionMediaRuntimeService,
    ActionMediaService,
    ActionNotificationPolicyService,
    ActionPlanningService,
    ActionValidationService,
)
from foundry_lite.application.services.base import CoreService, build_service


@dataclass(frozen=True)
class ActionServices:
    apply: ActionApplyService
    async_run: ActionAsyncRunService
    batch_apply: ActionBatchApplyService
    branch: ActionBranchService
    definition: ActionDefinitionService
    effect_operations: ActionEffectOperatorService
    distributed: ActionDistributedRunService
    log_revert: ActionLogRevertService
    monitoring_alerts: ActionMonitoringAlertService
    log_ontology: ActionLogOntologyService
    media: ActionMediaService
    media_runtime: ActionMediaRuntimeService
    notification_policies: ActionNotificationPolicyService
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
            branch=build_service(ActionBranchService, dependencies),
            definition=build_service(ActionDefinitionService, dependencies),
            effect_operations=build_service(ActionEffectOperatorService, dependencies),
            distributed=build_service(ActionDistributedRunService, dependencies),
            log_revert=build_service(ActionLogRevertService, dependencies),
            monitoring_alerts=build_service(ActionMonitoringAlertService, dependencies),
            log_ontology=build_service(ActionLogOntologyService, dependencies),
            media=build_service(ActionMediaService, dependencies),
            media_runtime=build_service(ActionMediaRuntimeService, dependencies),
            notification_policies=build_service(ActionNotificationPolicyService, dependencies),
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
            self.branch,
            self.definition,
            self.effect_operations,
            self.distributed,
            self.log_revert,
            self.monitoring_alerts,
            self.log_ontology,
            self.media,
            self.media_runtime,
            self.notification_policies,
            self.entrypoint,
            self.planning,
            self.validation,
            self.writeback,
        )
