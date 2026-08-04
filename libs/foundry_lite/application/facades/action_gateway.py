"""Thin facade entrypoints for action gateway workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.action_types import (
    ActionApplyResponse,
    ActionBatchApplyResponse,
    ActionCatalogItem,
    ActionCatalogPage,
    ActionExecutionPlanResponse,
    ActionValidationResponse,
)
from foundry_lite.application.services.action_service import ActionService
from foundry_lite.domain.context import RequestContext
from foundry_lite.observability.tracing import trace_public_methods


@trace_public_methods
class ActionGateway:
    """Action bounded context: apply ontology actions with idempotency and audit."""

    def __init__(self, action: ActionService) -> None:
        self._action = action

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
