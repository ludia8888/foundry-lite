"""Result and replay helpers for external-agent Action proposals."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.action_types import ActionExecutionPlanResponse
from foundry_lite.domain.errors import ConflictDetected, NotFound


def external_mcp_replay(
    review: Mapping[str, object],
    plan: ActionExecutionPlanResponse,
    fingerprint: str,
) -> dict[str, object]:
    if review.get("proposalFingerprint") != fingerprint:
        raise ConflictDetected("Ontology MCP JSON-RPC id was reused with different Action arguments")
    proposal = review.get("actionProposal")
    if not isinstance(proposal, Mapping):
        raise ConflictDetected("Ontology MCP approval replay is missing its immutable proposal")
    return external_mcp_result(proposal, review, plan, fingerprint, review.get("expiresAt"))


def external_mcp_result(
    proposal: Mapping[str, object],
    review: Mapping[str, object],
    plan: ActionExecutionPlanResponse,
    fingerprint: str,
    expires_at: object,
) -> dict[str, object]:
    return {
        "status": "approval_required",
        "proposalId": proposal["proposalId"],
        "reviewId": review["id"],
        "proposalFingerprint": fingerprint,
        "planHash": plan["planHash"],
        "risk": plan["risk"],
        "approval": plan["approval"],
        "expiresAt": expires_at,
    }


def external_mcp_proposal(
    proposal: Mapping[str, object],
    application_id: str,
    client_id: str | None,
    token_scopes: Sequence[str],
) -> dict[str, object]:
    """Seal the originating OAuth application envelope into the immutable proposal."""
    return {
        **proposal,
        "source": "ontology_mcp",
        "applicationId": application_id,
        "clientId": client_id,
        "tokenScopes": list(token_scopes),
    }


def external_mcp_status(review: Mapping[str, object], application_id: str, actor_user_id: str) -> dict[str, object]:
    proposal = _owned_external_proposal(review, application_id, actor_user_id)
    return {
        "status": _status(review),
        "approvalStatus": review.get("status"),
        "executionStatus": review.get("executionStatus"),
        "reviewId": review.get("id"),
        "proposalId": proposal.get("proposalId"),
        "proposalFingerprint": review.get("proposalFingerprint"),
        "actionApiName": proposal.get("actionApiName", proposal.get("actionType")),
        "expiresAt": review.get("expiresAt"),
        "actionRunId": review.get("approvedActionRunId"),
        "updatedAt": review.get("updatedAt"),
    }


def external_mcp_status_action_name(review: Mapping[str, object], application_id: str, actor_user_id: str) -> str:
    proposal = _owned_external_proposal(review, application_id, actor_user_id)
    value = proposal.get("actionApiName", proposal.get("actionType"))
    if not isinstance(value, str) or not value:
        raise NotFound("Ontology MCP approval was not found")
    return value


def _owned_external_proposal(
    review: Mapping[str, object], application_id: str, actor_user_id: str
) -> Mapping[str, object]:
    proposal = review.get("actionProposal")
    if (
        not isinstance(proposal, Mapping)
        or proposal.get("source") != "ontology_mcp"
        or proposal.get("applicationId") != application_id
        or review.get("createdByUserId") != actor_user_id
    ):
        raise NotFound("Ontology MCP approval was not found")
    return proposal


def _status(review: Mapping[str, object]) -> str:
    execution = review.get("executionStatus")
    if execution == "executed":
        return "succeeded"
    if execution in {"executing", "failed"}:
        return str(execution)
    return {"pending": "approval_pending", "approved": "approved", "rejected": "rejected"}.get(
        str(review.get("status")), "unknown"
    )
