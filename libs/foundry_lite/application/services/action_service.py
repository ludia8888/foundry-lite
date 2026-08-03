"""Compatibility entrypoint for the Action bounded context."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.action_types import (
    ActionApplyResponse,
    ActionBatchApplyResponse,
    ActionCatalogItem,
    ActionCatalogPage,
    ActionExecutionPlanResponse,
    ActionValidationResponse,
    ActionWritebackQueueResult,
    ActionWritebackReconciliationResult,
    ActionWritebackRecoveryItem,
    ActionWritebackRecoveryResult,
)
from foundry_lite.application.services.action_apply_service import ActionApplyService
from foundry_lite.application.services.action_async_run_service import ActionAsyncRunService
from foundry_lite.application.services.action_batch_service import ActionBatchApplyService
from foundry_lite.application.services.action_definition_service import ActionDefinitionService
from foundry_lite.application.services.action_planning_service import ActionPlanningService
from foundry_lite.application.services.action_validation_service import ActionValidationService
from foundry_lite.application.services.action_workflow import ExternalWritebackAdapter
from foundry_lite.application.services.action_writeback_service import ActionWritebackService
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed


class ActionService(CoreService):
    """Stable public Action entrypoint that delegates to focused use cases."""

    required_dependencies = ()
    required_collaborators = (
        "action_apply_service",
        "action_async_run_service",
        "action_batch_apply_service",
        "action_definition_service",
        "action_planning_service",
        "action_validation_service",
        "action_writeback_service",
    )
    action_apply_service: ActionApplyService
    action_async_run_service: ActionAsyncRunService
    action_batch_apply_service: ActionBatchApplyService
    action_definition_service: ActionDefinitionService
    action_planning_service: ActionPlanningService
    action_validation_service: ActionValidationService
    action_writeback_service: ActionWritebackService

    def list_actions(
        self,
        *,
        cursor: str | None = None,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> ActionCatalogPage:
        return self.action_definition_service.list_actions(cursor=cursor, limit=limit, ctx=ctx)

    def get_action(self, action_api_name: str, *, ctx: RequestContext | None = None) -> ActionCatalogItem:
        return self.action_definition_service.get_action(action_api_name, ctx=ctx)

    def action_schema(self, action_api_name: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self.action_definition_service.action_schema(action_api_name, ctx=ctx)

    def plan_action(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        ctx: RequestContext | None = None,
        is_dry_run: bool = False,
    ) -> ActionExecutionPlanResponse:
        return self.action_planning_service.plan_action(
            action_api_name,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            params=params,
            ctx=ctx,
            is_dry_run=is_dry_run,
        )

    def apply_action(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext | None = None,
        simulate_writeback_failure: bool = False,
        simulate_writeback_retryable: bool = False,
        simulate_writeback_outcome_unknown: bool = False,
        simulate_writeback_compensation_required: bool = False,
        external_writeback_uri: str | None = None,
    ) -> ActionApplyResponse:
        if not idempotency_key:
            raise ValidationFailed("idempotency key is required")
        return self.action_apply_service.apply_action(
            action_api_name,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            params=params,
            idempotency_key=idempotency_key,
            ctx=ctx,
            simulate_writeback_failure=simulate_writeback_failure,
            simulate_writeback_retryable=simulate_writeback_retryable,
            simulate_writeback_outcome_unknown=simulate_writeback_outcome_unknown,
            simulate_writeback_compensation_required=simulate_writeback_compensation_required,
            external_writeback_uri=external_writeback_uri,
        )

    def start_action_run(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        idempotency_key: str,
        wait_seconds: int,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self.action_async_run_service.start(
            action_api_name,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            params=params,
            idempotency_key=idempotency_key,
            wait_seconds=wait_seconds,
            ctx=ctx or RequestContext(),
        )

    def get_action_run(self, run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self.action_async_run_service.get(run_id, ctx=ctx or RequestContext())

    def list_action_runs(
        self, *, cursor: str | None = None, limit: int = 50, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        return self.action_async_run_service.list_runs(cursor=cursor, limit=limit, ctx=ctx or RequestContext())

    def action_run_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self.action_async_run_service.events(
            run_id, after_sequence=after_sequence, limit=limit, ctx=ctx or RequestContext()
        )

    def cancel_action_run(
        self,
        run_id: str,
        *,
        idempotency_key: str,
        reason: str | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self.action_async_run_service.cancel(
            run_id, idempotency_key=idempotency_key, reason=reason, ctx=ctx or RequestContext()
        )

    def apply_action_batch(
        self,
        action_api_name: str,
        *,
        object_type: str,
        targets: Sequence[Mapping[str, object]],
        params: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> ActionBatchApplyResponse:
        if not idempotency_key:
            raise ValidationFailed("idempotency key is required")
        return self.action_batch_apply_service.apply_action_batch(
            action_api_name,
            object_type=object_type,
            targets=targets,
            params=params,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def validate_action(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        ctx: RequestContext | None = None,
    ) -> ActionValidationResponse:
        return self.action_validation_service.validate_action(
            action_api_name,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            params=params,
            ctx=ctx,
        )

    def set_external_writeback_adapter(self, adapter: ExternalWritebackAdapter) -> None:
        self.action_writeback_service.set_external_writeback_adapter(adapter)

    def reconcile_action_writeback(
        self,
        writeback_id: str,
        *,
        remote_status: str | None = None,
        remote_resource_id: str | None = None,
        external_writeback_uri: str | None = None,
        ctx: RequestContext | None = None,
    ) -> ActionWritebackReconciliationResult:
        return self.action_writeback_service.reconcile_action_writeback(
            writeback_id,
            remote_status=remote_status,
            remote_resource_id=remote_resource_id,
            external_writeback_uri=external_writeback_uri,
            ctx=ctx,
        )

    def list_unresolved_action_writebacks(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> ActionWritebackQueueResult:
        return self.action_writeback_service.list_unresolved_action_writebacks(status=status, limit=limit, ctx=ctx)

    def recover_action_writebacks(
        self,
        *,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> ActionWritebackRecoveryResult:
        return self.action_writeback_service.recover_action_writebacks(limit=limit, ctx=ctx)

    def approve_action_writeback_recovery(
        self,
        writeback_id: str,
        *,
        approval_id: str,
        reason: str,
        external_writeback_uri: str | None = None,
        ctx: RequestContext | None = None,
    ) -> ActionWritebackRecoveryItem:
        return self.action_writeback_service.approve_action_writeback_recovery(
            writeback_id,
            approval_id=approval_id,
            reason=reason,
            external_writeback_uri=external_writeback_uri,
            ctx=ctx,
        )
