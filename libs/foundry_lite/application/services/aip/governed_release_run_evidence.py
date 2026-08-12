"""Run-record helpers for Governed Release confirmation and recovery."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import AiToolCallRecord
from foundry_lite.application.services.aip.fde_tool_result import hash_json
from foundry_lite.application.services.aip.governed_release_security_contract import (
    GovernedReleaseBinding,
    recovery_attempt,
    release_conflict,
)
from foundry_lite.application.services.mcp_tool_results import tool_error_structured
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ConflictDetected,
    FoundryLiteError,
    InvariantViolation,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)

JsonObject = Mapping[str, object]


def prepared_payload(
    run_id: str,
    receipt_id: str,
    expires_at: str,
    *,
    is_replayed: bool,
) -> dict[str, object]:
    return {
        "preparationRunId": run_id,
        "widgetConfirmationToken": receipt_id,
        "expiresAt": expires_at,
        "isReplayed": is_replayed,
    }


def tool_record(
    ctx: RequestContext,
    run_id: str,
    binding: GovernedReleaseBinding,
    output: JsonObject,
    now: str,
) -> AiToolCallRecord:
    return AiToolCallRecord(
        id=f"{run_id}-tool-1",
        tenant_id=ctx.tenant_id,
        ai_run_id=run_id,
        sequence=1,
        tool_id=binding.tool_name,
        tool_version="v1",
        arguments_hash=binding.arguments_hash,
        effect="WRITE",
        authorization_decision="allowed_by_widget_human_oauth_confirmation",
        confirmation_policy="USER",
        status="succeeded",
        result_hash=hash_json(output),
        linked_action_run_id=None,
        started_at=now,
        completed_at=now,
        error_json=None,
        result_json=dict(output),
    )


def execution_error(
    ctx: RequestContext,
    exc: Exception,
    *,
    is_known_not_committed: bool,
) -> dict[str, object]:
    safe_error = (
        exc if isinstance(exc, FoundryLiteError) else InvariantViolation("Governed release action failed unexpectedly")
    )
    error: dict[str, object] = {
        "type": type(safe_error).__name__,
        "detail": safe_error.message,
        "mcpToolResult": tool_error_structured(safe_error, request_id=ctx.request_id),
    }
    if is_known_not_committed:
        error.update(
            {
                "knownNotCommitted": True,
                "safeToRetry": True,
                "retryEvidence": "pre_mutation_foundry_error",
            }
        )
    return error


def is_known_not_committed_error(exc: FoundryLiteError) -> bool:
    """Only admission/precondition errors are evidence of no committed mutation."""
    return isinstance(exc, (ValidationFailed, PermissionDenied, NotFound, ConflictDetected))


def budget(run: JsonObject) -> Mapping[str, object]:
    value = run.get("budget_json")
    if not isinstance(value, Mapping):
        raise release_conflict("release_run_invalid")
    return value


def require_execution_attempt(run: JsonObject, execution_attempt: int) -> None:
    if recovery_attempt(run) != execution_attempt:
        raise release_conflict("release_execution_lease_lost")


def recovery_sequence(execution_attempt: int) -> int:
    return execution_attempt * 3 + 1


def terminal_sequence(execution_attempt: int) -> int:
    return execution_attempt * 3 + 2


def outcome_unknown_sequence(execution_attempt: int) -> int:
    return execution_attempt * 3 + 3


def require_matching_tool_result(existing: JsonObject, expected: AiToolCallRecord) -> None:
    if (
        existing.get("ai_run_id") != expected.ai_run_id
        or existing.get("tool_id") != expected.tool_id
        or existing.get("arguments_hash") != expected.arguments_hash
        or existing.get("result_hash") != expected.result_hash
    ):
        raise release_conflict("release_result_conflict")


__all__ = [
    "budget",
    "execution_error",
    "is_known_not_committed_error",
    "outcome_unknown_sequence",
    "prepared_payload",
    "recovery_sequence",
    "require_execution_attempt",
    "require_matching_tool_result",
    "terminal_sequence",
    "tool_record",
]
