"""Compatibility entrypoint for the Action bounded context."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import BinaryIO

from foundry_lite.application.action_types import (
    ActionApplyResponse,
    ActionBatchApplyResponse,
    ActionCatalogItem,
    ActionCatalogPage,
    ActionExecutionPlanResponse,
    ActionMediaUploadResult,
    ActionValidationResponse,
    ActionWritebackQueueResult,
    ActionWritebackReconciliationResult,
    ActionWritebackRecoveryItem,
    ActionWritebackRecoveryResult,
)
from foundry_lite.application.services.action_execution_service_registry import (
    ActionApplyService,
    ActionAsyncRunService,
    ActionBatchApplyService,
    ActionLogRevertService,
    ActionWritebackService,
)
from foundry_lite.application.services.action_external_mcp_run import (
    get_external_mcp_action_run,
    resume_external_mcp_action_run,
    start_external_mcp_action_run,
)
from foundry_lite.application.services.action_service_registry import (
    ActionBranchService,
    ActionDefinitionService,
    ActionMediaService,
    ActionPlanningService,
    ActionValidationService,
)
from foundry_lite.application.services.action_workflow import ExternalWritebackAdapter
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
        "action_branch_service",
        "action_definition_service",
        "action_planning_service",
        "action_log_revert_service",
        "action_media_service",
        "action_validation_service",
        "action_writeback_service",
    )
    action_apply_service: ActionApplyService
    action_async_run_service: ActionAsyncRunService
    action_batch_apply_service: ActionBatchApplyService
    action_branch_service: ActionBranchService
    action_definition_service: ActionDefinitionService
    action_planning_service: ActionPlanningService
    action_log_revert_service: ActionLogRevertService
    action_media_service: ActionMediaService
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

    def get_external_mcp_action(self, action_api_name: str, *, ctx: RequestContext) -> ActionCatalogItem:
        return self.action_definition_service.get_external_mcp_action(action_api_name, ctx=ctx)

    def action_schema(self, action_api_name: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self.action_definition_service.action_schema(action_api_name, ctx=ctx)

    def upload_action_media_parameter(
        self,
        action_api_name: str,
        parameter_name: str,
        *,
        object_type: str,
        object_id: str,
        file_name: str,
        source: BinaryIO,
        supplied_mime_type: str,
        idempotency_key: str,
        format: str | None = None,
        ctx: RequestContext | None = None,
    ) -> ActionMediaUploadResult:
        return self.action_media_service.upload_parameter(
            action_api_name,
            parameter_name,
            object_type=object_type,
            object_id=object_id,
            file_name=file_name,
            source=source,
            supplied_mime_type=supplied_mime_type,
            idempotency_key=idempotency_key,
            format=format,
            ctx=ctx or RequestContext(),
        )

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
        branch_id: str | None = None,
    ) -> ActionExecutionPlanResponse:
        if branch_id is not None:
            return self.action_branch_service.plan(
                action_api_name,
                branch_id=branch_id,
                object_type=object_type,
                object_id=object_id,
                expected_object_version=expected_object_version,
                params=params,
                ctx=ctx or RequestContext(),
                is_dry_run=is_dry_run,
            )
        return self.action_planning_service.plan_action(
            action_api_name,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            params=params,
            ctx=ctx,
            is_dry_run=is_dry_run,
        )

    def plan_external_mcp_action(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        ctx: RequestContext,
    ) -> ActionExecutionPlanResponse:
        return self.action_planning_service.plan_external_mcp_action(
            action_api_name,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            params=params,
            ctx=ctx,
        )

    def execute_branch_action(
        self,
        action_api_name: str,
        *,
        branch_id: str,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self.action_branch_service.execute(
            action_api_name,
            branch_id=branch_id,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            params=params,
            idempotency_key=idempotency_key,
            ctx=ctx or RequestContext(),
        )

    def branch_object(
        self, branch_id: str, object_type: str, object_id: str, *, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        return self.action_branch_service.get_object(branch_id, object_type, object_id, ctx=ctx or RequestContext())

    def branch_link(
        self,
        branch_id: str,
        link_type: str,
        from_object_id: str,
        to_object_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self.action_branch_service.get_link(
            branch_id, link_type, from_object_id, to_object_id, ctx=ctx or RequestContext()
        )

    def branch_diff(self, branch_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self.action_branch_service.diff(branch_id, ctx=ctx or RequestContext())

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

    def start_external_mcp_action_run(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        idempotency_key: str,
        wait_seconds: int,
        ctx: RequestContext,
    ) -> dict[str, object]:
        return start_external_mcp_action_run(
            self.action_async_run_service,
            action_api_name,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            params=params,
            idempotency_key=idempotency_key,
            wait_seconds=wait_seconds,
            ctx=ctx,
        )

    def resume_idempotent_action_run(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object] | None:
        return self.action_async_run_service.resume_idempotent(
            action_api_name,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            params=params,
            idempotency_key=idempotency_key,
            ctx=ctx or RequestContext(),
        )

    def resume_external_mcp_action_run(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext,
    ) -> dict[str, object] | None:
        return resume_external_mcp_action_run(
            self.action_async_run_service,
            action_api_name,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            params=params,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def start_action_batch_run(
        self,
        action_api_name: str,
        *,
        object_type: str,
        items: Sequence[Mapping[str, object]],
        idempotency_key: str,
        wait_seconds: int,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self.action_async_run_service.start_batch(
            action_api_name,
            object_type=object_type,
            raw_items=tuple(items),
            idempotency_key=idempotency_key,
            wait_seconds=wait_seconds,
            ctx=ctx or RequestContext(),
        )

    def get_action_run(self, run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self.action_async_run_service.get(run_id, ctx=ctx or RequestContext())

    def get_external_mcp_action_run(self, run_id: str, *, ctx: RequestContext) -> dict[str, object]:
        return get_external_mcp_action_run(self.action_async_run_service, run_id, ctx=ctx)

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

    def list_action_logs(
        self, *, cursor: str | None = None, limit: int = 50, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        return self.action_log_revert_service.list_logs(cursor=cursor, limit=limit, ctx=ctx or RequestContext())

    def action_revert_eligibility(self, run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return dict(self.action_log_revert_service.revert_eligibility(run_id, ctx=ctx or RequestContext()))

    def revert_action_run(
        self, run_id: str, *, idempotency_key: str, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        return self.action_log_revert_service.revert(
            run_id, idempotency_key=idempotency_key, ctx=ctx or RequestContext()
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
