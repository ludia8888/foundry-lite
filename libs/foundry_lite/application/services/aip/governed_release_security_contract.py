"""Pure identity, expiry, and persistence records for release widget approval."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from foundry_lite.application.ports import AiExecutionRunRecord
from foundry_lite.application.primitives import _json_hash
from foundry_lite.application.services.aip.fde_tool_result import hash_json
from foundry_lite.application.services.aip.governed_release_security_binding import (
    GovernedReleaseBinding,
    release_binding,
    require_human_app_principal,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected

JsonObject = Mapping[str, object]
_RECEIPT_TTL_SECONDS = 300
_PREPARATION_KIND = "governed_release_widget_preparation"
_RECEIPT_KIND = "governed_release_widget_receipt"
_ACTION_KIND = "governed_release_mcp_action"
_RECOVERY_LEASE_SECONDS = 30


@dataclass(frozen=True)
class GovernedReleaseReplay:
    tool_call_id: str
    output: JsonObject
    is_error: bool = False


def preparation_run_id(binding: GovernedReleaseBinding) -> str:
    identity = _stable_run_identity(binding)
    return f"governed_release_prepare_{_json_hash(identity).removeprefix('sha256:')[:32]}"


def action_run_id(binding: GovernedReleaseBinding) -> str:
    identity = _stable_run_identity(binding)
    return f"governed_release_run_{_json_hash(identity).removeprefix('sha256:')[:32]}"


def _stable_run_identity(binding: GovernedReleaseBinding) -> dict[str, object]:
    """Address exact replays by stable OAuth identity, not HTTP transport session."""

    return {
        "tenantId": binding.tenant_id,
        "actorUserId": binding.actor_user_id,
        "applicationId": binding.application_id,
        "clientId": binding.client_id,
        "oauthSessionHash": binding.oauth_session_hash,
        "toolName": binding.tool_name,
        "idempotencyKey": binding.idempotency_key,
    }


def preparation_record(
    ctx: RequestContext,
    binding: GovernedReleaseBinding,
    run_id: str,
    receipt_id: str,
    now: str,
    expires_at: str,
) -> AiExecutionRunRecord:
    budget = _security_budget(_PREPARATION_KIND, binding, expires_at)
    budget["receiptId"] = receipt_id
    return _run_record(ctx, binding, run_id, "succeeded", now, budget, completed_at=now)


def receipt_record(
    ctx: RequestContext,
    binding: GovernedReleaseBinding,
    receipt_id: str,
    preparation_id: str,
    now: str,
    expires_at: str,
) -> AiExecutionRunRecord:
    budget = _security_budget(_RECEIPT_KIND, binding, expires_at)
    budget["preparationRunId"] = preparation_id
    return _run_record(ctx, binding, receipt_id, "running", now, budget)


def action_record(
    ctx: RequestContext,
    binding: GovernedReleaseBinding,
    run_id: str,
    now: str,
) -> AiExecutionRunRecord:
    budget = {
        "kind": _ACTION_KIND,
        "requestBinding": binding.payload,
        "requestBindingHash": binding.fingerprint,
        "maxToolCalls": 1,
        "maxModelCalls": 0,
    }
    return _run_record(ctx, binding, run_id, "running", now, budget)


def _run_record(
    ctx: RequestContext,
    binding: GovernedReleaseBinding,
    run_id: str,
    status: str,
    now: str,
    budget: JsonObject,
    *,
    completed_at: str | None = None,
) -> AiExecutionRunRecord:
    return AiExecutionRunRecord(
        id=run_id,
        tenant_id=ctx.tenant_id,
        session_id=binding.session_id,
        agent_version_id=f"governed-release-mcp:{binding.application_id}:v1",
        actor_user_id=ctx.actor_user_id,
        request_id=ctx.request_id,
        trace_id=ctx.request_id,
        status=status,
        ontology_version_id="active-ontology",
        model_alias_version="none",
        resolved_model_id="none",
        resolved_model_revision="none",
        prompt_version_id="governed-release-mcp-v1",
        compiled_prompt_hash=binding.fingerprint,
        tool_manifest_hash=hash_json([binding.tool_name]),
        context_manifest_hash=hash_json([binding.application_id, binding.session_id]),
        state_snapshot_hash=binding.fingerprint,
        policy_snapshot_hash=hash_json({"source": "governed_release_mcp"}),
        budget_json=budget,
        usage_json={"source": "governed_release_mcp"} if completed_at else None,
        error_json=None,
        started_at=now,
        completed_at=completed_at,
    )


def _security_budget(
    kind: str,
    binding: GovernedReleaseBinding,
    expires_at: str,
) -> dict[str, object]:
    return {
        "kind": kind,
        "requestBinding": binding.payload,
        "requestBindingHash": binding.fingerprint,
        "expiresAt": expires_at,
    }


def validate_preparation(
    run: JsonObject,
    binding: GovernedReleaseBinding,
    now: str,
) -> str:
    _require_binding(run, binding)
    budget = _required_budget(run, _PREPARATION_KIND)
    if is_expired(budget.get("expiresAt"), now):
        raise release_conflict("widget_confirmation_expired")
    if not isinstance(budget.get("receiptId"), str):
        raise release_conflict("widget_confirmation_missing")
    return str(budget["expiresAt"])


def preparation_receipt_id(
    run: JsonObject,
    binding: GovernedReleaseBinding,
) -> str:
    """Return the hash-addressed active receipt for an exact preparation replay."""
    _require_binding(run, binding)
    budget = _required_budget(run, _PREPARATION_KIND)
    receipt_id = budget.get("receiptId")
    if not isinstance(receipt_id, str):
        raise release_conflict("widget_confirmation_missing")
    return receipt_id


def rotated_preparation_budget(
    run: JsonObject,
    binding: GovernedReleaseBinding,
    receipt_id: str,
    expires_at: str,
) -> dict[str, object]:
    """Bind a new one-time receipt to the same immutable request identity."""
    preparation_receipt_id(run, binding)
    budget = dict(_required_budget(run, _PREPARATION_KIND))
    attempt = budget.get("rotationAttempt", 0)
    budget.update(
        {
            "receiptId": receipt_id,
            "expiresAt": expires_at,
            "rotationAttempt": int(attempt) + 1 if isinstance(attempt, int) else 1,
        }
    )
    return budget


def widget_receipt_id(secret: str) -> str:
    digest = hash_json({"widgetConfirmationSecret": secret}).removeprefix("sha256:")
    return f"governed_release_widget_receipt_{digest}"


def receipt_conflict_reason(
    ledger: Mapping[str, object] | None,
    binding: GovernedReleaseBinding,
    now: str,
) -> str | None:
    if ledger is None:
        return "widget_confirmation_not_found"
    run = ledger.get("run")
    if not isinstance(run, Mapping):
        return "widget_confirmation_invalid"
    try:
        _require_binding(run, binding)
        budget = _required_budget(run, _RECEIPT_KIND)
    except ConflictDetected:
        return "widget_confirmation_binding_mismatch"
    if run.get("status") != "running":
        return "widget_confirmation_already_consumed"
    return "widget_confirmation_expired" if is_expired(budget.get("expiresAt"), now) else None


def replay_from_ledger(
    ledger: Mapping[str, object],
    binding: GovernedReleaseBinding,
) -> GovernedReleaseReplay | None:
    run = ledger.get("run")
    if not isinstance(run, Mapping):
        raise release_conflict("release_run_invalid")
    _require_binding(run, binding)
    if run.get("status") == "succeeded":
        return _successful_replay(ledger)
    if run.get("status") == "failed":
        return _failed_replay(run)
    if run.get("status") == "running":
        return None
    raise release_conflict("release_run_in_progress")


def recovery_budget(
    run: JsonObject,
    binding: GovernedReleaseBinding,
    now: str,
) -> dict[str, object]:
    """Build the next lease only after the current owner is observably stale."""
    _require_binding(run, binding)
    budget = dict(_required_budget(run, _ACTION_KIND))
    if run.get("status") != "running":
        raise release_conflict("release_run_not_recoverable")
    recovery = budget.get("recoveryLease")
    lease = recovery if isinstance(recovery, Mapping) else {}
    anchor = lease.get("startedAt") or run.get("started_at")
    if not isinstance(anchor, str):
        raise release_conflict("release_run_invalid")
    recoverable_at = _parse_time(anchor) + timedelta(seconds=_RECOVERY_LEASE_SECONDS)
    if recoverable_at > _parse_time(now):
        remaining = max(1, int((recoverable_at - _parse_time(now)).total_seconds()) + 1)
        raise ConflictDetected(
            "Governed Release MCP action is still running; retry the exact action after the lease expires",
            details={
                "reason": "release_run_in_progress",
                "isRecoverable": True,
                "retryAfterSeconds": remaining,
                "recoverableAt": recoverable_at.isoformat(),
            },
        )
    attempt = lease.get("attempt", 0)
    next_attempt = int(attempt) + 1 if isinstance(attempt, int) else 1
    budget["recoveryLease"] = {"attempt": next_attempt, "startedAt": now}
    return budget


def recovery_attempt(run: JsonObject) -> int:
    budget = run.get("budget_json")
    recovery = budget.get("recoveryLease") if isinstance(budget, Mapping) else None
    attempt = recovery.get("attempt") if isinstance(recovery, Mapping) else None
    return attempt if isinstance(attempt, int) and attempt > 0 else 0


def failed_retry_budget(
    run: JsonObject,
    binding: GovernedReleaseBinding,
    now: str,
) -> dict[str, object]:
    """Build a fenced attempt only from an explicitly safe terminal failure."""
    require_safe_failed_retry(run, binding)
    budget = dict(_required_budget(run, _ACTION_KIND))
    attempt = recovery_attempt(run) + 1
    retry_count = budget.get("failedRetryCount", 0)
    budget["recoveryLease"] = {
        "attempt": attempt,
        "startedAt": now,
        "source": "fresh_widget_confirmation_after_known_not_committed_failure",
    }
    budget["failedRetryCount"] = (retry_count if isinstance(retry_count, int) else 0) + 1
    return budget


def require_safe_failed_retry(run: JsonObject, binding: GovernedReleaseBinding) -> None:
    """Require immutable evidence that the prior attempt committed no mutation."""
    _require_binding(run, binding)
    _required_budget(run, _ACTION_KIND)
    error = run.get("error_json")
    if run.get("status") != "failed" or not isinstance(error, Mapping):
        raise release_conflict("release_run_not_safely_retryable")
    if error.get("knownNotCommitted") is not True or error.get("safeToRetry") is not True:
        raise release_conflict("release_run_not_safely_retryable")


def _successful_replay(ledger: Mapping[str, object]) -> GovernedReleaseReplay:
    calls = ledger.get("toolCalls")
    rows = calls if isinstance(calls, list) else []
    output = rows[0].get("result_json") if rows and isinstance(rows[0], Mapping) else None
    tool_call_id = rows[0].get("id") if rows and isinstance(rows[0], Mapping) else None
    if not isinstance(output, Mapping) or not isinstance(tool_call_id, str):
        raise release_conflict("release_result_missing")
    return GovernedReleaseReplay(tool_call_id, output)


def _failed_replay(run: JsonObject) -> GovernedReleaseReplay:
    error = run.get("error_json")
    output = error.get("mcpToolResult") if isinstance(error, Mapping) else None
    if not isinstance(output, Mapping):
        raise release_conflict("release_error_missing")
    return GovernedReleaseReplay(f"{run.get('id')}-tool-1", output, is_error=True)


def _require_binding(run: JsonObject, binding: GovernedReleaseBinding) -> None:
    budget = run.get("budget_json")
    if (
        run.get("actor_user_id") != binding.actor_user_id
        or run.get("compiled_prompt_hash") != binding.fingerprint
        or not isinstance(budget, Mapping)
        or budget.get("requestBindingHash") != binding.fingerprint
    ):
        raise release_conflict("widget_confirmation_binding_mismatch")


def _required_budget(run: JsonObject, kind: str) -> Mapping[str, object]:
    budget = run.get("budget_json")
    if not isinstance(budget, Mapping) or budget.get("kind") != kind:
        raise release_conflict("widget_confirmation_invalid")
    return budget


def receipt_expires_at(now: str) -> str:
    return (_parse_time(now) + timedelta(seconds=_RECEIPT_TTL_SECONDS)).isoformat()


def is_expired(value: object, now: str) -> bool:
    return not isinstance(value, str) or _parse_time(value) <= _parse_time(now)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def release_conflict(reason: str) -> ConflictDetected:
    return ConflictDetected(
        "Governed Release MCP confirmation or replay cannot be used",
        details={"reason": reason},
    )


__all__ = [
    "GovernedReleaseBinding",
    "GovernedReleaseReplay",
    "action_record",
    "action_run_id",
    "failed_retry_budget",
    "preparation_record",
    "preparation_receipt_id",
    "preparation_run_id",
    "receipt_conflict_reason",
    "receipt_expires_at",
    "receipt_record",
    "recovery_attempt",
    "recovery_budget",
    "release_binding",
    "release_conflict",
    "require_safe_failed_retry",
    "replay_from_ledger",
    "require_human_app_principal",
    "rotated_preparation_budget",
    "validate_preparation",
    "widget_receipt_id",
]
