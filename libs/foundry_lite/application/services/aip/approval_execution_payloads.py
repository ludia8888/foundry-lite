"""Validation and payload helpers for approved AIP action execution."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import cast

from foundry_lite.application.ports.insight_review_repository import InsightReviewRow
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.aip.action_proposal import compute_action_proposal_fingerprint
from foundry_lite.application.services.aip.approval_execution_types import (
    ApprovalExecutionError,
    ApprovalExecutionRequest,
    ExecutionClaim,
    JsonObject,
    PreparedExecution,
)
from foundry_lite.domain.errors import ConflictDetected

PROPOSAL_TYPE = "ontology_action"
EXECUTION_METADATA_KEY = "approvalExecution"


def validate_request(request: ApprovalExecutionRequest) -> None:
    if not request.review_id:
        raise ApprovalExecutionError("missing_field", "review_id is required")
    if not request.expected_proposal_fingerprint.startswith("sha256:"):
        raise ApprovalExecutionError("missing_field", "expected proposal fingerprint must be sha256-prefixed")
    if not request.idempotency_key:
        raise ApprovalExecutionError("missing_field", "idempotency_key is required")


def approved_executable_proposal(row: InsightReviewRow, request: ApprovalExecutionRequest) -> Mapping[str, object]:
    if row["status"] != "approved":
        raise ApprovalExecutionError("review_not_approved", "insight review must be approved before execution")
    if row["execution_status"] == "executing":
        require_matching_execution_claim(row, request)
    elif row["execution_status"] not in {None, "pending_review"}:
        raise ConflictDetected(
            "approval execution is already in progress or terminal",
            details={"review_id": row["id"]},
        )
    if row["proposal_type"] != PROPOSAL_TYPE or row["action_proposal"] is None:
        raise ApprovalExecutionError("not_action_proposal", "insight review does not contain an action proposal")
    return row["action_proposal"]


def require_matching_execution_claim(row: InsightReviewRow, request: ApprovalExecutionRequest) -> None:
    claim = execution_claim(row)
    expected = execution_request_fingerprint(request)
    if claim is None:
        raise ConflictDetected(
            "approval execution is missing its idempotency claim",
            details={"review_id": row["id"]},
        )
    if claim.idempotency_key == request.idempotency_key and claim.request_fingerprint == expected:
        return
    raise ConflictDetected(
        "approval execution idempotency key already belongs to a different request",
        details={"review_id": row["id"]},
    )


def execution_claim(row: InsightReviewRow) -> ExecutionClaim | None:
    metadata = row["review_metadata"]
    value = metadata.get(EXECUTION_METADATA_KEY)
    if not isinstance(value, Mapping):
        return None
    idempotency_key = value.get("idempotencyKey")
    request_fingerprint = value.get("requestFingerprint")
    proposal_fingerprint = value.get("proposalFingerprint")
    if not isinstance(idempotency_key, str) or not idempotency_key:
        return None
    if not isinstance(request_fingerprint, str) or not request_fingerprint:
        return None
    if not isinstance(proposal_fingerprint, str) or not proposal_fingerprint:
        return None
    return ExecutionClaim(idempotency_key, request_fingerprint, proposal_fingerprint)


def execution_request_fingerprint(request: ApprovalExecutionRequest) -> str:
    canonical = json.dumps(
        {
            "proposalFingerprint": request.expected_proposal_fingerprint,
            "reviewId": request.review_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def require_matching_fingerprint(row: InsightReviewRow, expected: str) -> None:
    stored = row["proposal_fingerprint"]
    proposal = row["action_proposal"] or {}
    proposal_fingerprint = proposal.get("proposalFingerprint")
    if stored != expected or proposal_fingerprint != expected:
        raise ApprovalExecutionError("fingerprint_mismatch", "approved proposal fingerprint does not match")


def prepared_execution(row: InsightReviewRow, proposal: Mapping[str, object]) -> PreparedExecution:
    policy_version = required_text(proposal, "policyVersion")
    if row["approval_policy_version"] != policy_version:
        raise ApprovalExecutionError("fingerprint_mismatch", "approval policy version does not match proposal")
    return PreparedExecution(
        review_id=row["id"],
        proposal_fingerprint=required_row_text(row, "proposal_fingerprint"),
        originating_ai_run_id=required_row_text(row, "originating_ai_run_id"),
        action_type=required_text(proposal, "actionType"),
        target_object_type=required_text(proposal, "targetObjectType"),
        target_object_id=required_text(proposal, "targetObjectId"),
        expected_object_version=required_int(proposal, "expectedObjectVersion"),
        parameters=required_mapping(proposal, "parameters"),
        policy_version=policy_version,
    )


def require_recomputed_fingerprint(
    prepared: PreparedExecution,
    evidence_refs: Sequence[JsonObject],
    run: Mapping[str, object],
) -> None:
    fingerprint = compute_action_proposal_fingerprint(
        action_type=prepared.action_type,
        target_object_type=prepared.target_object_type,
        target_object_id=prepared.target_object_id,
        expected_object_version=prepared.expected_object_version,
        parameters=prepared.parameters,
        evidence_refs=evidence_refs,
        agent_version_id=required_text(run, "agent_version_id"),
        policy_version=prepared.policy_version,
    )
    if fingerprint != prepared.proposal_fingerprint:
        raise ApprovalExecutionError("fingerprint_mismatch", "approved proposal was changed after review")


def require_not_expired(proposal: Mapping[str, object]) -> None:
    expires_at = required_text(proposal, "expiresAt")
    if parse_instant(expires_at) <= parse_instant(_now()):
        raise ApprovalExecutionError("review_expired", "approved proposal has expired")


def evidence_refs(proposal: Mapping[str, object]) -> list[JsonObject]:
    refs = proposal.get("evidenceRefs")
    if not isinstance(refs, list) or not refs:
        raise ApprovalExecutionError("missing_evidence", "approved proposal requires evidence references")
    if not all(isinstance(ref, dict) for ref in refs):
        raise ApprovalExecutionError("invalid_proposal", "proposal evidence references must be objects")
    return [cast(JsonObject, ref) for ref in refs]


def required_row_text(row: InsightReviewRow, key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise ApprovalExecutionError("invalid_review", f"review is missing {key}")
    return value


def required_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ApprovalExecutionError("invalid_proposal", f"proposal is missing {key}")
    return value


def required_int(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int):
        raise ApprovalExecutionError("invalid_proposal", f"proposal is missing integer {key}")
    return value


def required_mapping(payload: Mapping[str, object], key: str) -> JsonObject:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ApprovalExecutionError("invalid_proposal", f"proposal is missing {key}")
    return value


def parse_instant(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApprovalExecutionError("invalid_expiration", "proposal expiration is not a valid ISO timestamp") from exc
