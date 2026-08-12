"""Audit boundary for durable Governed Release security transitions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.services.aip.governed_release_security_contract import GovernedReleaseBinding
from foundry_lite.domain.context import RequestContext


class GovernedReleaseAuditBoundary(Protocol):
    """Write release-security evidence in the caller transaction."""

    def _audit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        event_type: str,
        resource_type: str,
        resource_id: str | None,
        action: str,
        decision: str = "allow",
        policy_decision: Mapping[str, object] | None = None,
        before_ref: Mapping[str, object] | None = None,
        after_ref: Mapping[str, object] | None = None,
        correlation_id: str | None = None,
    ) -> None: ...


def _audit(
    conn: TransactionContext,
    ctx: RequestContext,
    audit: GovernedReleaseAuditBoundary,
    run_id: str,
    attempt: int,
) -> None:
    """Record the exact known-not-committed retry admission."""

    audit._audit(
        conn,
        ctx,
        event_type="governed_release.failed_run_reopened",
        resource_type="governed_release_run",
        resource_id=run_id,
        action="retry_failed_release_action",
        policy_decision={"knownNotCommitted": True, "attempt": attempt},
        correlation_id=ctx.request_id,
    )


def audit_release_action(
    conn: TransactionContext,
    ctx: RequestContext,
    audit: GovernedReleaseAuditBoundary,
    binding: GovernedReleaseBinding,
    run_id: str,
    event: str,
    *,
    attempt: int = 0,
    error: Mapping[str, object] | None = None,
) -> None:
    """Record an exact proposal-scoped lifecycle event without raw tool payloads."""

    if binding.proposal_id is None:
        return
    after_ref = _release_action_ref(binding, run_id, event, attempt, error)
    audit._audit(
        conn,
        ctx,
        event_type=f"governed_release.action.{event}",
        resource_type="governed_release_proposal",
        resource_id=binding.proposal_id,
        action=binding.tool_name,
        after_ref=after_ref,
        correlation_id=run_id,
    )


def _release_action_ref(
    binding: GovernedReleaseBinding,
    run_id: str,
    event: str,
    attempt: int,
    error: Mapping[str, object] | None,
) -> dict[str, object]:
    ref: dict[str, object] = {
        "runId": run_id,
        "toolName": binding.tool_name,
        "releaseKind": binding.release_kind,
        "status": event,
        "attempt": attempt,
    }
    if error is not None:
        ref.update(_safe_error_ref(error))
    return ref


def _safe_error_ref(error: Mapping[str, object]) -> dict[str, object]:
    return {key: error[key] for key in ("type", "knownNotCommitted", "safeToRetry", "retryEvidence") if key in error}


__all__ = ["_audit", "audit_release_action", "GovernedReleaseAuditBoundary"]
