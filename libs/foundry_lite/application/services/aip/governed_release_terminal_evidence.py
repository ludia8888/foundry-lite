"""Durable terminal event and audit pairs for governed release actions."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import AiRunRepository, TransactionContext
from foundry_lite.application.services.aip import governed_release_run_evidence as run_evidence
from foundry_lite.application.services.aip.agent_runtime_ledger import event_record
from foundry_lite.application.services.aip.governed_release_audit import (
    GovernedReleaseAuditBoundary,
    audit_release_action,
)
from foundry_lite.application.services.aip.governed_release_security_contract import GovernedReleaseBinding
from foundry_lite.domain.context import RequestContext


def append_success_evidence(
    repository: AiRunRepository,
    audit: GovernedReleaseAuditBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    binding: GovernedReleaseBinding,
    run_id: str,
    execution_attempt: int,
    now: str,
) -> None:
    repository.append_execution_event(
        transaction=conn,
        record=event_record(
            ctx,
            run_id,
            run_evidence.terminal_sequence(execution_attempt),
            "succeeded",
            {"source": "governed_release_mcp"},
            now,
        ),
    )
    audit_release_action(conn, ctx, audit, binding, run_id, "succeeded", attempt=execution_attempt)


def append_failure_evidence(
    repository: AiRunRepository,
    audit: GovernedReleaseAuditBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    binding: GovernedReleaseBinding,
    run_id: str,
    execution_attempt: int,
    error: Mapping[str, object],
    now: str,
) -> None:
    repository.append_execution_event(
        transaction=conn,
        record=event_record(
            ctx,
            run_id,
            run_evidence.terminal_sequence(execution_attempt),
            "failed",
            {"source": "governed_release_mcp", "toolName": binding.tool_name},
            now,
        ),
    )
    audit_release_action(
        conn,
        ctx,
        audit,
        binding,
        run_id,
        "failed",
        attempt=execution_attempt,
        error=error,
    )


def append_outcome_unknown_evidence(
    repository: AiRunRepository,
    audit: GovernedReleaseAuditBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    binding: GovernedReleaseBinding,
    run_id: str,
    execution_attempt: int,
    error_type: str,
    now: str,
) -> None:
    inserted = repository.append_execution_event(
        transaction=conn,
        record=event_record(
            ctx,
            run_id,
            run_evidence.outcome_unknown_sequence(execution_attempt),
            "governed_release_outcome_unknown",
            {"toolName": binding.tool_name, "errorType": error_type},
            now,
        ),
    )
    if inserted:
        audit_release_action(
            conn,
            ctx,
            audit,
            binding,
            run_id,
            "outcome_unknown",
            attempt=execution_attempt,
            error={"type": error_type},
        )


__all__ = [
    "append_failure_evidence",
    "append_outcome_unknown_evidence",
    "append_success_evidence",
]
