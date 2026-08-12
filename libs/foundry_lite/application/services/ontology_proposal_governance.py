"""Assigned-human review invariants for ontology proposal execution."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports.insight_review_repository import InsightReviewRow
from foundry_lite.application.services.ontology_proposal_payloads import proposal_status
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, PermissionDenied, ValidationFailed


def require_decidable(row: InsightReviewRow) -> None:
    if proposal_status(row) not in ("submitted", "in_review"):
        raise ConflictDetected(
            "ontology proposal is no longer decidable",
            details={"proposal_id": row["id"], "status": proposal_status(row)},
        )


def require_assigned_decider(row: InsightReviewRow, ctx: RequestContext) -> None:
    assignee = row["assignee_user_id"]
    if not isinstance(assignee, str) or not assignee:
        raise ValidationFailed(
            "ontology proposal must be assigned before review",
            details={"proposalId": row["id"]},
        )
    if assignee != ctx.actor_user_id:
        raise PermissionDenied(
            "only the assigned human reviewer can decide this ontology proposal",
            details={"proposalId": row["id"], "assigneeUserId": assignee},
        )


def require_execution_approval(row: InsightReviewRow) -> None:
    if not _has_execution_approval(row):
        raise PermissionDenied(
            "ontology proposal lacks assigned human-reviewer approval evidence",
            details={"proposalId": row["id"]},
        )


def _has_execution_approval(row: InsightReviewRow) -> bool:
    decision = row["decision"]
    if row["status"] != "approved" or not isinstance(decision, Mapping):
        return False
    return (
        _has_assigned_approval(row, decision)
        and decision.get("hasBlockedChanges") is False
        and _has_safe_migration_plan(decision)
    )


def _has_assigned_approval(row: InsightReviewRow, decision: Mapping[str, object]) -> bool:
    assignee = row["assignee_user_id"]
    decided_by = decision.get("decidedByUserId")
    return isinstance(assignee, str) and decision.get("decision") == "approved" and decided_by == assignee


def _has_safe_migration_plan(decision: Mapping[str, object]) -> bool:
    migration_plan = decision.get("migrationPlan")
    return isinstance(migration_plan, Mapping) and not bool(migration_plan.get("blockedChanges"))


__all__ = ["require_assigned_decider", "require_decidable", "require_execution_approval"]
