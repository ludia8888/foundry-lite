"""Atomic lost-response recovery for Governed Release widget preparations."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import AiRunRepository, TransactionContext
from foundry_lite.application.ports.transaction_context import AI_RUN_SUCCEEDED
from foundry_lite.application.primitives import _new_id
from foundry_lite.application.services.aip.agent_runtime_ledger import event_record
from foundry_lite.application.services.aip.governed_release_security_contract import (
    GovernedReleaseBinding,
    action_run_id,
    preparation_receipt_id,
    receipt_conflict_reason,
    receipt_expires_at,
    receipt_record,
    release_conflict,
    require_safe_failed_retry,
    rotated_preparation_budget,
    widget_receipt_id,
)
from foundry_lite.domain.context import RequestContext

JsonObject = Mapping[str, object]


def append_initial_preparation_events(
    repository: AiRunRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    binding: GovernedReleaseBinding,
    run_id: str,
    receipt_id: str,
    now: str,
    expires_at: str,
) -> None:
    repository.append_execution_event(
        transaction=conn,
        record=event_record(
            ctx,
            run_id,
            1,
            "governed_release_widget_action_prepared",
            {"toolName": binding.tool_name, "expiresAt": expires_at},
            now,
        ),
    )
    repository.append_execution_event(
        transaction=conn,
        record=event_record(
            ctx,
            receipt_id,
            1,
            "governed_release_widget_confirmation_issued",
            {"preparationRunId": run_id, "expiresAt": expires_at},
            now,
        ),
    )


def rotate_preparation_receipt(
    repository: AiRunRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    binding: GovernedReleaseBinding,
    existing: JsonObject,
    run_id: str,
    now: str,
) -> dict[str, object]:
    """Rotate the hash-only receipt so a lost raw token can be recovered once."""
    old_receipt_id = preparation_receipt_id(existing, binding)
    is_consumed_safe_retry = _require_rotatable_receipt(repository, conn, ctx, binding, old_receipt_id, now)
    secret = _new_id("governed_release_widget_secret")
    receipt_id = widget_receipt_id(secret)
    expires_at = receipt_expires_at(now)
    replacement = rotated_preparation_budget(existing, binding, receipt_id, expires_at)
    updated = repository.compare_and_swap_execution_run_budget(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        ai_run_id=run_id,
        expected_status="succeeded",
        expected_budget_json=_budget(existing),
        replacement_budget_json=replacement,
    )
    if updated is None:
        raise release_conflict("widget_preparation_rotated_concurrently")
    _replace_receipt(
        repository,
        conn,
        ctx,
        binding,
        old_receipt_id,
        receipt_id,
        run_id,
        now,
        expires_at,
        updated,
        is_consumed_safe_retry=is_consumed_safe_retry,
    )
    return _prepared_payload(run_id, secret, expires_at)


def _require_rotatable_receipt(
    repository: AiRunRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    binding: GovernedReleaseBinding,
    receipt_id: str,
    now: str,
) -> bool:
    receipt = repository.ledger_for_run(transaction=conn, tenant_id=ctx.tenant_id, ai_run_id=receipt_id)
    reason = receipt_conflict_reason(receipt, binding, now)
    if reason in {None, "widget_confirmation_expired"}:
        return False
    if reason != "widget_confirmation_already_consumed":
        raise release_conflict(str(reason))
    action = repository.execution_run_by_id(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        ai_run_id=action_run_id(binding),
    )
    if not isinstance(action, Mapping):
        raise release_conflict("release_run_not_safely_retryable")
    require_safe_failed_retry(action, binding)
    return True


def _replace_receipt(
    repository: AiRunRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    binding: GovernedReleaseBinding,
    old_receipt_id: str,
    receipt_id: str,
    run_id: str,
    now: str,
    expires_at: str,
    updated: JsonObject,
    *,
    is_consumed_safe_retry: bool,
) -> None:
    if not is_consumed_safe_retry:
        _revoke_receipt(repository, conn, ctx, old_receipt_id, now)
    repository.create_execution_run(
        transaction=conn,
        record=receipt_record(ctx, binding, receipt_id, run_id, now, expires_at),
    )
    _append_rotation_events(
        repository, conn, ctx, binding, run_id, old_receipt_id, receipt_id, now, expires_at, updated
    )


def _revoke_receipt(
    repository: AiRunRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    receipt_id: str,
    now: str,
) -> None:
    revoked = repository.update_execution_run_status(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        ai_run_id=receipt_id,
        transition=AI_RUN_SUCCEEDED,
        usage_json={"source": "governed_release_widget_confirmation_rotated"},
        error_json=None,
        completed_at=now,
    )
    if revoked is None:
        raise release_conflict("widget_preparation_rotated_concurrently")
    repository.append_execution_event(
        transaction=conn,
        record=event_record(
            ctx,
            receipt_id,
            2,
            "governed_release_widget_confirmation_rotated",
            {"reason": "replaced_by_exact_prepare_replay"},
            now,
        ),
    )


def _append_rotation_events(
    repository: AiRunRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    binding: GovernedReleaseBinding,
    run_id: str,
    old_receipt_id: str,
    receipt_id: str,
    now: str,
    expires_at: str,
    updated: JsonObject,
) -> None:
    attempt = _rotation_attempt(updated)
    repository.append_execution_event(
        transaction=conn,
        record=event_record(
            ctx,
            run_id,
            attempt + 1,
            "governed_release_widget_preparation_replayed",
            {"toolName": binding.tool_name, "rotationAttempt": attempt},
            now,
        ),
    )
    repository.append_execution_event(
        transaction=conn,
        record=event_record(
            ctx,
            receipt_id,
            1,
            "governed_release_widget_confirmation_issued",
            {"preparationRunId": run_id, "replacesReceiptId": old_receipt_id, "expiresAt": expires_at},
            now,
        ),
    )


def _budget(run: JsonObject) -> Mapping[str, object]:
    value = run.get("budget_json")
    if not isinstance(value, Mapping):
        raise release_conflict("widget_preparation_invalid")
    return value


def _rotation_attempt(run: JsonObject) -> int:
    budget = run.get("budget_json")
    attempt = budget.get("rotationAttempt") if isinstance(budget, Mapping) else None
    if not isinstance(attempt, int) or attempt < 1:
        raise release_conflict("widget_preparation_invalid")
    return attempt


def _prepared_payload(run_id: str, secret: str, expires_at: str) -> dict[str, object]:
    return {
        "preparationRunId": run_id,
        "widgetConfirmationToken": secret,
        "expiresAt": expires_at,
        "isReplayed": True,
    }


__all__ = ["append_initial_preparation_events", "rotate_preparation_receipt"]
