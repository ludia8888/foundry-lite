"""Thin facade entrypoints for action gateway workflows."""

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
)
from foundry_lite.application.services.action_effect_operator_service import ActionEffectOperatorService
from foundry_lite.application.services.action_notification_policy_service import ActionNotificationPolicyService
from foundry_lite.application.services.action_service import ActionService
from foundry_lite.domain.context import RequestContext
from foundry_lite.observability.tracing import trace_public_methods


@trace_public_methods
class ActionGateway:
    """Action bounded context: apply ontology actions with idempotency and audit."""

    def __init__(
        self,
        action: ActionService,
        notification_policies: ActionNotificationPolicyService,
        effect_operations: ActionEffectOperatorService,
    ) -> None:
        self._action = action
        self._notification_policies = notification_policies
        self._effect_operations = effect_operations

    def list_effect_receipts(
        self,
        *,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._effect_operations.list_receipts(status=status, cursor=cursor, limit=limit, ctx=ctx)

    def get_effect_receipt(self, receipt_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._effect_operations.get_receipt(receipt_id, ctx=ctx)

    def cancel_effect(
        self,
        receipt_id: str,
        *,
        reason: str | None,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._effect_operations.cancel(
            receipt_id,
            reason=reason,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def retry_effect(
        self,
        receipt_id: str,
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._effect_operations.retry(receipt_id, idempotency_key=idempotency_key, ctx=ctx)

    def reconcile_effect(
        self,
        receipt_id: str,
        *,
        resolution: str,
        evidence: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._effect_operations.reconcile(
            receipt_id,
            resolution=resolution,
            evidence=evidence,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def list_notification_policies(
        self, *, cursor: str | None = None, limit: int = 50, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        return self._notification_policies.list_policies(cursor=cursor, limit=limit, ctx=ctx)

    def get_notification_policy(self, policy_name: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._notification_policies.get_policy(policy_name, ctx=ctx)

    def create_notification_policy(
        self,
        policy_name: str,
        *,
        display_name: str,
        delivery_mode: str,
        recipients: Sequence[Mapping[str, object]],
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._notification_policies.create_policy(
            policy_name,
            display_name=display_name,
            delivery_mode=delivery_mode,
            recipients=recipients,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def update_notification_policy(
        self,
        policy_name: str,
        *,
        display_name: str,
        delivery_mode: str,
        recipients: Sequence[Mapping[str, object]],
        status: str,
        expected_fingerprint: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._notification_policies.update_policy(
            policy_name,
            display_name=display_name,
            delivery_mode=delivery_mode,
            recipients=recipients,
            status=status,
            expected_fingerprint=expected_fingerprint,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def disable_notification_policy(
        self,
        policy_name: str,
        *,
        expected_fingerprint: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._notification_policies.disable_policy(
            policy_name,
            expected_fingerprint=expected_fingerprint,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def list(
        self,
        *,
        cursor: str | None = None,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> ActionCatalogPage:
        return self._action.list_actions(cursor=cursor, limit=limit, ctx=ctx)

    def get(self, action_api_name: str, *, ctx: RequestContext | None = None) -> ActionCatalogItem:
        return self._action.get_action(action_api_name, ctx=ctx)

    def schema(self, action_api_name: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._action.action_schema(action_api_name, ctx=ctx)

    def upload_parameter(
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
        return self._action.upload_action_media_parameter(
            action_api_name,
            parameter_name,
            object_type=object_type,
            object_id=object_id,
            file_name=file_name,
            source=source,
            supplied_mime_type=supplied_mime_type,
            idempotency_key=idempotency_key,
            format=format,
            ctx=ctx,
        )

    def plan(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        branch_id: str | None = None,
        ctx: RequestContext | None = None,
    ) -> ActionExecutionPlanResponse:
        return self._action.plan_action(
            action_api_name,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            params=params,
            branch_id=branch_id,
            ctx=ctx,
        )

    def dry_run(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        branch_id: str | None = None,
        ctx: RequestContext | None = None,
    ) -> ActionExecutionPlanResponse:
        return self._action.plan_action(
            action_api_name,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            params=params,
            branch_id=branch_id,
            ctx=ctx,
            is_dry_run=True,
        )

    def execute_branch(
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
        return self._action.execute_branch_action(
            action_api_name,
            branch_id=branch_id,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            params=params,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def branch_object(
        self, branch_id: str, object_type: str, object_id: str, *, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        return self._action.branch_object(branch_id, object_type, object_id, ctx=ctx)

    def branch_link(
        self,
        branch_id: str,
        link_type: str,
        from_object_id: str,
        to_object_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._action.branch_link(branch_id, link_type, from_object_id, to_object_id, ctx=ctx)

    def branch_diff(self, branch_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._action.branch_diff(branch_id, ctx=ctx)

    def apply(
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
        return self._action.apply_action(
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

    def start_run(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        idempotency_key: str,
        wait_seconds: int = 0,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._action.start_action_run(
            action_api_name,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            params=params,
            idempotency_key=idempotency_key,
            wait_seconds=wait_seconds,
            ctx=ctx,
        )

    def resume_idempotent_run(
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
        return self._action.resume_idempotent_action_run(
            action_api_name,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            params=params,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def start_batch_run(
        self,
        action_api_name: str,
        *,
        object_type: str,
        items: Sequence[Mapping[str, object]],
        idempotency_key: str,
        wait_seconds: int = 0,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        """Queue one all-or-nothing Function-backed Action batch."""
        return self._action.start_action_batch_run(
            action_api_name,
            object_type=object_type,
            items=items,
            idempotency_key=idempotency_key,
            wait_seconds=wait_seconds,
            ctx=ctx,
        )

    def get_run(self, run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._action.get_action_run(run_id, ctx=ctx)

    def list_runs(
        self, *, cursor: str | None = None, limit: int = 50, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        return self._action.list_action_runs(cursor=cursor, limit=limit, ctx=ctx)

    def events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._action.action_run_events(run_id, after_sequence=after_sequence, limit=limit, ctx=ctx)

    def cancel(
        self,
        run_id: str,
        *,
        idempotency_key: str,
        reason: str | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._action.cancel_action_run(run_id, idempotency_key=idempotency_key, reason=reason, ctx=ctx)

    def logs(
        self, *, cursor: str | None = None, limit: int = 50, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        return self._action.list_action_logs(cursor=cursor, limit=limit, ctx=ctx)

    def revert_eligibility(self, run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._action.action_revert_eligibility(run_id, ctx=ctx)

    def revert(self, run_id: str, *, idempotency_key: str, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._action.revert_action_run(run_id, idempotency_key=idempotency_key, ctx=ctx)

    def apply_batch(
        self,
        action_api_name: str,
        *,
        object_type: str,
        targets: Sequence[Mapping[str, object]],
        params: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> ActionBatchApplyResponse:
        """Apply one action to many targets of the target type atomically (all-or-nothing)."""
        return self._action.apply_action_batch(
            action_api_name,
            object_type=object_type,
            targets=targets,
            params=params,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def validate(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        ctx: RequestContext | None = None,
    ) -> ActionValidationResponse:
        return self._action.validate_action(
            action_api_name,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            params=params,
            ctx=ctx,
        )
