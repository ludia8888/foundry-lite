"""Pure assigned-human review and execution-evidence rules for Pipeline proposals."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports.pipeline_repository import PipelineProposalRow, PipelineVersionRow
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed


def has_execution_approval(
    proposal: PipelineProposalRow,
    event: Mapping[str, object] | None,
) -> bool:
    return (
        _has_approved_decision(proposal)
        and _has_assigned_reviewer(proposal)
        and _has_matching_approval_event(proposal, event)
    )


def decision_status(decision: str) -> str:
    normalized = decision.strip().lower()
    if normalized in {"approve", "approved"}:
        return "approved"
    if normalized in {"reject", "rejected"}:
        return "rejected"
    raise ValidationFailed("unsupported pipeline proposal decision", details={"decision": decision})


def require_assigned_reviewer(proposal: PipelineProposalRow, ctx: RequestContext) -> None:
    if proposal["assigned_to"] is None:
        raise ValidationFailed(
            "pipeline proposal must be assigned before review",
            details={"proposalId": proposal["id"]},
        )
    if proposal["assigned_to"] != ctx.actor_user_id:
        raise ValidationFailed(
            "only the assigned human reviewer can decide this pipeline proposal",
            details={"proposalId": proposal["id"]},
        )


def is_decision_replay(
    proposal: PipelineProposalRow,
    ctx: RequestContext,
    status: str,
    decision: str,
    comment: str | None,
) -> bool:
    if proposal["status"] != status:
        return False
    require_assigned_reviewer(proposal, ctx)
    normalized = decision.strip().lower()
    stored = str(proposal["decision"] or "").strip().lower()
    return normalized in {stored, status} and proposal["decision_comment"] == comment


def next_version_number(latest: PipelineVersionRow | None) -> int:
    return 1 if latest is None else int(latest["version_number"]) + 1


def proposal_audit_ref(row: PipelineProposalRow | None) -> dict[str, object] | None:
    if row is None:
        return None
    return {
        "pipeline_id": row["pipeline_id"],
        "branch_id": row["branch_id"],
        "status": row["status"],
        "graph_fingerprint": row["graph_fingerprint"],
    }


def _has_approved_decision(proposal: PipelineProposalRow) -> bool:
    decision = str(proposal["decision"] or "").strip().lower()
    return proposal["status"] in {"approved", "executed"} and decision in {"approve", "approved"}


def _has_assigned_reviewer(proposal: PipelineProposalRow) -> bool:
    assignee = proposal["assigned_to"]
    return isinstance(assignee, str) and bool(assignee)


def _has_matching_approval_event(
    proposal: PipelineProposalRow,
    event: Mapping[str, object] | None,
) -> bool:
    if not isinstance(event, Mapping):
        return False
    after = event.get("after_ref")
    return (
        event.get("actor_user_id") == proposal["assigned_to"]
        and isinstance(after, Mapping)
        and after.get("status") == "approved"
    )


__all__ = [
    "decision_status",
    "has_execution_approval",
    "is_decision_replay",
    "next_version_number",
    "proposal_audit_ref",
    "require_assigned_reviewer",
]
