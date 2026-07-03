"""Thin facade entrypoints for action gateway workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.action_types import (
    ActionApplyResponse,
    ActionBatchApplyResponse,
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
