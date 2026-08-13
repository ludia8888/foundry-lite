"""Application service helpers for ontology change proposal payloads."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

import yaml

from foundry_lite.application.ports import OntologyValidationResult
from foundry_lite.application.ports.insight_review_repository import (
    InsightReviewRecord,
    InsightReviewRow,
)
from foundry_lite.application.primitives import _new_id
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed

ONTOLOGY_PROPOSAL_TYPE = "ontology_change"
PROPOSAL_STATUSES = ("submitted", "in_review", "approved", "rejected", "applied", "withdrawn")
ProposalDecision = Literal["approved", "rejected"]
ProposalEvent = Literal["submitted", "assigned", "revised", "decided", "applied", "withdrawn"]
PROPOSAL_EVENT_ACTIONS: Mapping[str, str] = {
    "submitted": "ontology:validate",
    "assigned": "ontology:activate",
    "revised": "ontology:validate",
    "decided": "ontology:activate",
    "applied": "ontology:activate",
    "withdrawn": "ontology:validate",
}
_LIVE_EXECUTION_STATUS = "pending_review"


@dataclass(frozen=True)
class ProposalListFilters:
    """Column-level filters equivalent to one derived proposal status."""

    status: str | None
    execution_statuses: tuple[str, ...] | None
    is_assigned: bool | None


@dataclass(frozen=True)
class ProposalPageCursor:
    """Keyset position after which the next proposal page starts."""

    created_before: str | None
    before_id: str | None


_LIST_FILTERS: Mapping[str, ProposalListFilters] = {
    "submitted": ProposalListFilters("pending", None, False),
    "in_review": ProposalListFilters("pending", None, True),
    "approved": ProposalListFilters("approved", (_LIVE_EXECUTION_STATUS, "executing", "failed"), None),
    "applied": ProposalListFilters("approved", ("executed",), None),
    "rejected": ProposalListFilters("rejected", (_LIVE_EXECUTION_STATUS,), None),
    "withdrawn": ProposalListFilters("rejected", ("withdrawn",), None),
}


def yaml_fingerprint(yaml_text: str) -> str:
    """Content fingerprint used for replay detection and drift protection."""
    return f"sha256:{hashlib.sha256(yaml_text.encode('utf-8')).hexdigest()}"


def semantic_yaml_fingerprint(yaml_text: str) -> str:
    """Hash parsed content so formatting-only differences do not block recovery."""
    payload = yaml.safe_load(yaml_text)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def proposal_record(
    ctx: RequestContext,
    *,
    title: str,
    description: str | None,
    yaml_text: str,
    validation: OntologyValidationResult,
    idempotency_key: str,
    now: str,
) -> InsightReviewRecord:
    proposal_id = _new_id("ontprop")
    return InsightReviewRecord(
        review_id=proposal_id,
        tenant_id=ctx.tenant_id,
        claim_id=proposal_id,
        claim_text=title,
        status="pending",
        priority="normal",
        assignee_user_id=None,
        evidence_object_ids=[],
        evidence_refs=[],
        action_proposal=_action_proposal(title, description, yaml_text, validation),
        proposal_type=ONTOLOGY_PROPOSAL_TYPE,
        proposal_fingerprint=yaml_fingerprint(yaml_text),
        originating_ai_run_id=None,
        originating_tool_call_id=None,
        expires_at=None,
        execution_status=_LIVE_EXECUTION_STATUS,
        approved_action_run_id=None,
        approval_policy_version=None,
        created_by_user_id=ctx.actor_user_id,
        created_idempotency_key=idempotency_key,
        assignment_idempotency_key=None,
        decision=None,
        decision_idempotency_key=None,
        review_metadata={"requestId": ctx.request_id},
        created_at=now,
        updated_at=now,
    )


def _action_proposal(
    title: str,
    description: str | None,
    yaml_text: str,
    validation: OntologyValidationResult,
) -> dict[str, object]:
    plan = dict(validation["migration_plan"])
    return {
        "proposalType": ONTOLOGY_PROPOSAL_TYPE,
        "title": title,
        "description": description,
        "yamlText": yaml_text,
        "submittedMigrationPlan": plan,
        "objectTypeCount": validation["object_type_count"],
        "linkTypeCount": validation["link_type_count"],
        "actionTypeCount": validation["action_type_count"],
    }


def ensure_submit_replay_matches(existing: InsightReviewRow, record: InsightReviewRecord) -> None:
    if (
        existing.get("proposal_type") == ONTOLOGY_PROPOSAL_TYPE
        and existing.get("proposal_fingerprint") == record.proposal_fingerprint
    ):
        return
    raise ConflictDetected(
        "idempotency key reused with different ontology proposal content",
        details={
            "idempotency_key": record.created_idempotency_key,
            "existing_proposal_id": existing["id"],
        },
    )


def require_fingerprint_match(row: InsightReviewRow, expected_fingerprint: str) -> None:
    required_text(expected_fingerprint, "expectedFingerprint")
    if row["proposal_fingerprint"] != expected_fingerprint:
        raise ConflictDetected(
            "ontology proposal fingerprint mismatch",
            details={
                "proposal_id": row["id"],
                "expectedFingerprint": expected_fingerprint,
                "proposalFingerprint": row["proposal_fingerprint"],
            },
        )


def require_proposal_updatable(row: InsightReviewRow) -> None:
    """A proposal is revisable only while pending, undecided, and not withdrawn.

    This is the same storage guard the conditional repository update (and
    withdraw) enforces, surfaced early with the derived status for callers.
    """
    if row["status"] == "pending" and row["execution_status"] == _LIVE_EXECUTION_STATUS and row["decision"] is None:
        return
    raise ConflictDetected(
        "ontology proposal can no longer be updated",
        details={"proposal_id": row["id"], "status": proposal_status(row)},
    )


def revised_proposal_content(
    row: InsightReviewRow,
    *,
    yaml_text: str,
    title: str | None,
    description: str | None,
    validation: OntologyValidationResult,
) -> tuple[str, dict[str, object]]:
    """Return the (claim_text, action_proposal) pair for a revised proposal.

    ``title``/``description`` are optional overrides; omitted fields keep the
    currently stored values.
    """
    existing = dict(row["action_proposal"] or {})
    new_title = title if title is not None else str(row["claim_text"])
    if description is None:
        stored = existing.get("description")
        description = stored if isinstance(stored, str) else None
    return new_title, _action_proposal(new_title, description, yaml_text, validation)


def revision_record(ctx: RequestContext, row: InsightReviewRow, now: str) -> dict[str, object]:
    """Metadata entry recording the latest in-place revision of a proposal."""
    return {
        "count": revision_count(row) + 1,
        "lastRevisedAt": now,
        "lastRevisedByUserId": ctx.actor_user_id,
    }


def revision_count(row: InsightReviewRow) -> int:
    count = _revision_entry(row).get("count", 0)
    return count if isinstance(count, int) else 0


def proposal_status(row: InsightReviewRow) -> str:
    if row["execution_status"] == "withdrawn":
        return "withdrawn"
    if row["status"] == "pending":
        return "in_review" if row["assignee_user_id"] else "submitted"
    if row["status"] == "approved":
        return "applied" if row["execution_status"] == "executed" else "approved"
    return "rejected"


def proposal_yaml_text(row: InsightReviewRow) -> str:
    proposal = row["action_proposal"] or {}
    yaml_text = proposal.get("yamlText")
    if not isinstance(yaml_text, str) or not yaml_text:
        raise ValidationFailed("ontology proposal is missing its yaml text", details={"proposal_id": row["id"]})
    return yaml_text


def proposal_payload(row: InsightReviewRow) -> dict[str, object]:
    proposal = dict(row["action_proposal"] or {})
    metadata = dict(row["review_metadata"])
    submitted_plan = _mapping_or_none(proposal.get("submittedMigrationPlan"))
    return {
        "id": row["id"],
        "proposalType": ONTOLOGY_PROPOSAL_TYPE,
        "status": proposal_status(row),
        "title": row["claim_text"],
        "description": proposal.get("description"),
        "fingerprint": row["proposal_fingerprint"],
        "submittedByUserId": row["created_by_user_id"],
        "assigneeUserId": row["assignee_user_id"],
        "submittedMigrationPlan": submitted_plan,
        "hasBlockedChangesAtSubmit": _has_blocked_changes(submitted_plan),
        "validation": {
            "objectTypeCount": proposal.get("objectTypeCount"),
            "linkTypeCount": proposal.get("linkTypeCount"),
            "actionTypeCount": proposal.get("actionTypeCount"),
        },
        "decision": dict(row["decision"]) if row["decision"] is not None else None,
        "executionStatus": row["execution_status"],
        "appliedOntologyVersion": _mapping_or_none(metadata.get("appliedOntologyVersion")),
        "withdrawal": _mapping_or_none(metadata.get("withdrawal")),
        "revisionCount": revision_count(row),
        "lastRevisedAt": _revision_entry(row).get("lastRevisedAt"),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def proposal_detail_payload(row: InsightReviewRow) -> dict[str, object]:
    payload = proposal_payload(row)
    payload["yamlText"] = proposal_yaml_text(row)
    payload["timeline"] = proposal_timeline(row)
    return payload


def proposal_timeline(row: InsightReviewRow) -> list[dict[str, object]]:
    events: list[dict[str, object]] = [
        {"event": "submitted", "at": row["created_at"], "byUserId": row["created_by_user_id"]}
    ]
    if row["assignee_user_id"] is not None:
        events.append({"event": "assigned", "assigneeUserId": row["assignee_user_id"]})
    revision = _revision_entry(row)
    if revision:
        events.append(
            {
                "event": "revised",
                "count": revision.get("count"),
                "at": revision.get("lastRevisedAt"),
                "byUserId": revision.get("lastRevisedByUserId"),
            }
        )
    events.extend(_decision_timeline(row))
    return events


def _revision_entry(row: InsightReviewRow) -> dict[str, object]:
    return _mapping_or_none(dict(row["review_metadata"]).get("revision")) or {}


def _decision_timeline(row: InsightReviewRow) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    decision = row["decision"]
    if decision is not None:
        events.append(
            {
                "event": "decided",
                "decision": decision.get("decision"),
                "comment": decision.get("comment"),
                "at": decision.get("decidedAt"),
                "byUserId": decision.get("decidedByUserId"),
            }
        )
    metadata = dict(row["review_metadata"])
    applied = _mapping_or_none(metadata.get("appliedOntologyVersion"))
    if applied is not None:
        events.append({"event": "applied", **applied})
    withdrawal = _mapping_or_none(metadata.get("withdrawal"))
    if withdrawal is not None:
        events.append({"event": "withdrawn", **withdrawal})
    return events


def decision_record(
    ctx: RequestContext,
    *,
    decision: str,
    comment: str | None,
    idempotency_key: str,
    migration_plan: Mapping[str, object],
    now: str,
) -> dict[str, object]:
    return {
        "decision": decision,
        "comment": comment,
        "decidedByUserId": ctx.actor_user_id,
        "decidedAt": now,
        "idempotencyKey": idempotency_key,
        "migrationPlan": dict(migration_plan),
        "hasBlockedChanges": _has_blocked_changes(migration_plan),
    }


def withdrawal_record(ctx: RequestContext, reason: str | None, now: str) -> dict[str, object]:
    return {"reason": reason, "withdrawnByUserId": ctx.actor_user_id, "at": now}


def proposal_list_filters(status: str | None) -> ProposalListFilters:
    if status is None:
        return ProposalListFilters(None, None, None)
    filters = _LIST_FILTERS.get(status)
    if filters is None:
        raise ValidationFailed(
            "invalid ontology proposal status filter",
            details={"status": status, "allowed": list(PROPOSAL_STATUSES)},
        )
    return filters


def encode_proposal_cursor(row: InsightReviewRow) -> str:
    raw = json.dumps({"createdAt": row["created_at"], "id": row["id"]}, sort_keys=True, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_proposal_cursor(cursor: str | None) -> ProposalPageCursor:
    if cursor is None:
        return ProposalPageCursor(None, None)
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
    except (ValueError, binascii.Error) as exc:
        raise ValidationFailed("invalid ontology proposal cursor", details={"cursor": cursor}) from exc
    created_at = payload.get("createdAt") if isinstance(payload, dict) else None
    row_id = payload.get("id") if isinstance(payload, dict) else None
    if not isinstance(created_at, str) or not isinstance(row_id, str):
        raise ValidationFailed("invalid ontology proposal cursor", details={"cursor": cursor})
    return ProposalPageCursor(created_at, row_id)


def proposal_audit_ref(row: InsightReviewRow) -> dict[str, object]:
    return {
        "status": proposal_status(row),
        "proposalFingerprint": row["proposal_fingerprint"],
        "assigneeUserId": row["assignee_user_id"],
        "executionStatus": row["execution_status"],
    }


def decision_replay(
    row: InsightReviewRow,
    decision: ProposalDecision,
    comment: str | None,
) -> dict[str, object] | None:
    stored = row["decision"]
    if stored is None:
        return None
    if stored.get("decision") == decision and stored.get("comment") == comment:
        return proposal_payload(row)
    raise ConflictDetected(
        "ontology proposal was already decided differently",
        details={"proposal_id": row["id"], "decision": stored.get("decision")},
    )


def parse_decision(decision: str) -> ProposalDecision:
    if decision == "approve":
        return "approved"
    if decision == "reject":
        return "rejected"
    raise ValidationFailed(
        "invalid ontology proposal decision",
        details={"decision": decision, "allowed": ["approve", "reject"]},
    )


def bounded_limit(limit: int) -> int:
    if limit < 1 or limit > 200:
        raise ValidationFailed("limit must be between 1 and 200", details={"limit": limit})
    return limit


def required_text(value: str, name: str) -> str:
    if not value:
        raise ValidationFailed(f"{name} is required")
    return value


def _has_blocked_changes(plan: Mapping[str, object] | None) -> bool:
    return bool(plan.get("blockedChanges")) if plan is not None else False


def _mapping_or_none(value: object) -> dict[str, object] | None:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return None
