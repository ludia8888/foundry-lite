"""Application service helpers for insight review payloads workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TypedDict

from foundry_lite.application.ports.insight_review_repository import (
    InsightReviewPriority,
    InsightReviewRecord,
    InsightReviewRow,
)
from foundry_lite.application.primitives import _json_hash, _new_id
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed


@dataclass(frozen=True)
class InsightReviewProposalFields:
    proposal_type: str | None = None
    proposal_fingerprint: str | None = None
    originating_ai_run_id: str | None = None
    originating_tool_call_id: str | None = None
    expires_at: str | None = None
    execution_status: str | None = None
    approved_action_run_id: str | None = None
    approval_policy_version: str | None = None


class _ProposalRecordKwargs(TypedDict):
    proposal_type: str | None
    proposal_fingerprint: str | None
    originating_ai_run_id: str | None
    originating_tool_call_id: str | None
    expires_at: str | None
    execution_status: str | None
    approved_action_run_id: str | None
    approval_policy_version: str | None


def review_record(
    ctx: RequestContext,
    *,
    claim_id: str,
    claim_text: str,
    evidence_object_ids: Sequence[str],
    evidence_refs: Sequence[Mapping[str, object]],
    idempotency_key: str,
    priority: InsightReviewPriority,
    assignee_user_id: str | None,
    action_proposal: Mapping[str, object] | None,
    proposal_fields: InsightReviewProposalFields | None,
    now: str,
) -> InsightReviewRecord:
    _validate_claim_inputs(claim_id, claim_text, evidence_object_ids, evidence_refs)
    return InsightReviewRecord(
        review_id=_new_id("insight_review"),
        tenant_id=ctx.tenant_id,
        claim_id=claim_id,
        claim_text=claim_text,
        status="pending",
        priority=priority,
        assignee_user_id=assignee_user_id,
        evidence_object_ids=list(evidence_object_ids),
        evidence_refs=[dict(ref) for ref in evidence_refs],
        action_proposal=dict(action_proposal) if action_proposal is not None else None,
        **_proposal_record_kwargs(proposal_fields or InsightReviewProposalFields()),
        created_by_user_id=ctx.actor_user_id,
        created_idempotency_key=idempotency_key,
        assignment_idempotency_key=None,
        decision=None,
        decision_idempotency_key=None,
        review_metadata={"requestId": ctx.request_id},
        created_at=now,
        updated_at=now,
    )


def _proposal_record_kwargs(fields: InsightReviewProposalFields) -> _ProposalRecordKwargs:
    return {
        "proposal_type": fields.proposal_type,
        "proposal_fingerprint": fields.proposal_fingerprint,
        "originating_ai_run_id": fields.originating_ai_run_id,
        "originating_tool_call_id": fields.originating_tool_call_id,
        "expires_at": fields.expires_at,
        "execution_status": fields.execution_status,
        "approved_action_run_id": fields.approved_action_run_id,
        "approval_policy_version": fields.approval_policy_version,
    }


def review_payload(row: InsightReviewRow) -> dict[str, object]:
    return {
        "id": row["id"],
        "claimId": row["claim_id"],
        "claimText": row["claim_text"],
        "status": row["status"],
        "priority": row["priority"],
        "assigneeUserId": row["assignee_user_id"],
        "evidenceObjectIds": list(row["evidence_object_ids"]),
        "evidenceRefs": [dict(ref) for ref in row["evidence_refs"]],
        "actionProposal": dict(row["action_proposal"]) if row["action_proposal"] is not None else None,
        "proposalType": row["proposal_type"],
        "proposalFingerprint": row["proposal_fingerprint"],
        "originatingAiRunId": row["originating_ai_run_id"],
        "originatingToolCallId": row["originating_tool_call_id"],
        "expiresAt": row["expires_at"],
        "executionStatus": row["execution_status"],
        "approvedActionRunId": row["approved_action_run_id"],
        "approvalPolicyVersion": row["approval_policy_version"],
        "createdByUserId": row["created_by_user_id"],
        "decision": dict(row["decision"]) if row["decision"] is not None else None,
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def ensure_create_replay_matches(existing: InsightReviewRow, record: InsightReviewRecord) -> None:
    if _review_create_fingerprint(_review_fingerprint_payload(existing)) == _review_create_fingerprint(
        _record_fingerprint_payload(record)
    ):
        return
    raise ConflictDetected(
        "idempotency key reused with different insight review payload",
        details={
            "idempotency_key": record.created_idempotency_key,
            "existing_review_id": existing["id"],
        },
    )


def _validate_claim_inputs(
    claim_id: str,
    claim_text: str,
    evidence_object_ids: Sequence[str],
    evidence_refs: Sequence[Mapping[str, object]],
) -> None:
    _required_text(claim_id, "claimId")
    _required_text(claim_text, "claimText")
    if not evidence_object_ids or not evidence_refs:
        raise ValidationFailed("insight review requires evidence objects")


def _review_fingerprint_payload(row: InsightReviewRow) -> dict[str, object]:
    return {
        "claimId": row["claim_id"],
        "claimText": row["claim_text"],
        "evidenceObjectIds": list(row["evidence_object_ids"]),
        "evidenceRefs": [dict(ref) for ref in row["evidence_refs"]],
        "priority": str(row["priority"]),
        "assigneeUserId": row["assignee_user_id"],
        "actionProposal": dict(row["action_proposal"]) if row["action_proposal"] is not None else None,
        "proposalFields": _proposal_fields_from_row(row),
    }


def _record_fingerprint_payload(record: InsightReviewRecord) -> dict[str, object]:
    return {
        "claimId": record.claim_id,
        "claimText": record.claim_text,
        "evidenceObjectIds": list(record.evidence_object_ids),
        "evidenceRefs": [dict(ref) for ref in record.evidence_refs],
        "priority": record.priority,
        "assigneeUserId": record.assignee_user_id,
        "actionProposal": dict(record.action_proposal) if record.action_proposal is not None else None,
        "proposalFields": _proposal_fields_from_record(record),
    }


def _proposal_fields_from_row(row: InsightReviewRow) -> dict[str, object]:
    return {
        "proposalType": row["proposal_type"],
        "proposalFingerprint": row["proposal_fingerprint"],
        "originatingAiRunId": row["originating_ai_run_id"],
        "originatingToolCallId": row["originating_tool_call_id"],
        "expiresAt": row["expires_at"],
        "executionStatus": row["execution_status"],
        "approvedActionRunId": row["approved_action_run_id"],
        "approvalPolicyVersion": row["approval_policy_version"],
    }


def _proposal_fields_from_record(record: InsightReviewRecord) -> dict[str, object]:
    return {
        "proposalType": record.proposal_type,
        "proposalFingerprint": record.proposal_fingerprint,
        "originatingAiRunId": record.originating_ai_run_id,
        "originatingToolCallId": record.originating_tool_call_id,
        "expiresAt": record.expires_at,
        "executionStatus": record.execution_status,
        "approvedActionRunId": record.approved_action_run_id,
        "approvalPolicyVersion": record.approval_policy_version,
    }


def _review_create_fingerprint(payload: Mapping[str, object]) -> str:
    return _json_hash(payload)


def _required_text(value: str, name: str) -> str:
    if not value:
        raise ValidationFailed(f"{name} is required")
    return value
