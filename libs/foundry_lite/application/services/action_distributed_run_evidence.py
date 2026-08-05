"""Small deterministic helpers for distributed Action worker evidence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.primitives import _new_id
from foundry_lite.application.services.action_distributed_contracts import (
    ActionAsyncRunRow,
    ActionExecutionRepository,
    ActionFunctionExecutionResult,
    ActionRunEventRecord,
    ActionRunRetryableFailure,
    ActionStepAttemptRow,
    ConflictDetected,
    FoundryLiteError,
    RequestContext,
    TransactionContext,
)
from foundry_lite.application.services.action_edit_plan_results import ActionEditPlanResult, plan_summary
from foundry_lite.domain.errors import (
    ExternalCompensationRequired,
    ExternalOutcomeUnknown,
    ExternalRetryableWriteback,
    InvariantViolation,
    PermissionDenied,
    RateLimited,
    ValidationFailed,
)

ACTION_RUN_TERMINAL_STATUSES = frozenset(
    {"succeeded", "failed", "cancelled", "conflict", "outcome_unknown", "compensation_required", "reconciled"}
)


def distributed_plan_summary(result: ActionEditPlanResult) -> dict[str, object]:
    """Return the stable committed-plan evidence used by the worker."""
    return dict(plan_summary(result))


def utc_now() -> str:
    """Return the current UTC timestamp in the Action evidence wire format."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def append_action_attempt_event(
    repository: ActionExecutionRepository,
    transaction: TransactionContext,
    ctx: RequestContext,
    row: ActionAsyncRunRow,
    attempt: ActionStepAttemptRow,
    event_type: str,
    payload: dict[str, object],
) -> None:
    repository.append_event(
        transaction=transaction,
        record=ActionRunEventRecord(
            event_id=_new_id("aevent"),
            tenant_id=ctx.tenant_id,
            run_id=row["id"],
            event_type=event_type,
            payload=payload,
            created_at=utc_now(),
            step_key="function" if action_has_function(row) else "commit",
            attempt_number=attempt["attempt_number"],
            worker_id=attempt["worker_id"],
            fencing_token=attempt["fencing_token"],
        ),
    )


def append_action_run_event(
    repository: ActionExecutionRepository,
    transaction: TransactionContext,
    ctx: RequestContext,
    run_id: str,
    event_type: str,
    payload: dict[str, object],
) -> None:
    repository.append_event(
        transaction=transaction,
        record=ActionRunEventRecord(
            event_id=_new_id("aevent"),
            tenant_id=ctx.tenant_id,
            run_id=run_id,
            event_type=event_type,
            payload=payload,
            created_at=utc_now(),
        ),
    )


def action_has_function(row: ActionAsyncRunRow) -> bool:
    plan = row["execution_plan"] or {}
    return isinstance(plan.get("functionVersion"), str) and bool(plan["functionVersion"])


def action_function_output(result: ActionFunctionExecutionResult | None) -> dict[str, object] | None:
    if result is None:
        return None
    return {
        "externalExecutionId": result.external_execution_id,
        "resultHash": result.result_hash,
        "provenance": dict(result.provenance),
        "editBatch": result.edit_batch.to_payload(),
    }


def is_action_error_retryable(exc: Exception) -> bool:
    if isinstance(exc, AdapterError):
        return exc.failure.is_retryable and exc.failure.kind in _SAFE_RETRYABLE_ADAPTER_KINDS
    return isinstance(exc, ConnectionError | TimeoutError | ActionRunRetryableFailure)


def action_error_kind(exc: Exception) -> str:
    if isinstance(exc, AdapterError):
        return f"adapter_{exc.failure.kind}"
    for error_types, kind in _ACTION_ERROR_KIND_RULES:
        if isinstance(exc, error_types):
            return kind
    if isinstance(exc, FoundryLiteError):
        return exc.code.lower()
    return "permanent"


def action_retry_at(changed_at: str, attempt_number: int) -> str:
    current = datetime.fromisoformat(changed_at.replace("Z", "+00:00"))
    seconds = min(30, 2 ** max(0, attempt_number - 1))
    return (current + timedelta(seconds=seconds)).astimezone(UTC).isoformat().replace("+00:00", "Z")


_SAFE_RETRYABLE_ADAPTER_KINDS = frozenset({"timeout", "unavailable", "rate_limited"})
_ACTION_ERROR_KIND_RULES: tuple[tuple[type[BaseException] | tuple[type[BaseException], ...], str], ...] = (
    (ConflictDetected, "conflict"),
    (ExternalOutcomeUnknown, "outcome_unknown"),
    (ExternalCompensationRequired, "reconciliation_required"),
    (ExternalRetryableWriteback, "external_retryable"),
    (PermissionDenied, "authorization"),
    (ValidationFailed, "validation"),
    (InvariantViolation, "invariant"),
    (RateLimited, "rate_limited"),
    (MemoryError, "resource_oom"),
    ((ConnectionError, TimeoutError, ActionRunRetryableFailure), "transient_adapter"),
)
