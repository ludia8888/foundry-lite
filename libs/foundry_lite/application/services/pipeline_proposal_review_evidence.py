"""Immutable Pipeline proposal diff and durable test-receipt admission."""

from __future__ import annotations

import json
from collections.abc import Mapping

from foundry_lite.application.services.pipeline_graph_release_diff import pipeline_graph_release_diff
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed

JsonObject = Mapping[str, object]
_MARKER = "[pipeline-branch-evidence] "
_PROOF_VERSION = "pipeline-static-review-v1"


def description_with_review_evidence(
    description: str | None,
    branch: JsonObject,
) -> str:
    diff = pipeline_graph_release_diff(_mapping(branch, "base_graph"), _mapping(branch, "graph"))
    payload = {
        "proofVersion": _PROOF_VERSION,
        "completeness": "complete_normalized_graph",
        "baseVersionId": branch.get("base_version_id"),
        "candidateFingerprint": branch.get("graph_fingerprint"),
        "changeDiff": diff,
    }
    marker = _MARKER + json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"{(description or '').rstrip()}\n\n{marker}".lstrip()


def public_proposal_description(description: object) -> str | None:
    if not isinstance(description, str):
        return None
    position = _marker_position(description)
    public = description[:position] if position >= 0 else description
    return public.rstrip() or None


def proposal_change_diff(description: object, expected_fingerprint: str) -> dict[str, object]:
    payload = _marker_payload(description)
    if payload is None:
        return {
            "completeness": "unavailable",
            "candidateFingerprint": expected_fingerprint,
            "changeDiff": None,
        }
    if payload.get("candidateFingerprint") != expected_fingerprint:
        raise ConflictDetected("pipeline proposal review evidence fingerprint mismatch")
    if payload.get("proofVersion") != _PROOF_VERSION or not isinstance(payload.get("changeDiff"), Mapping):
        raise ConflictDetected("pipeline proposal review evidence is unsupported or malformed")
    return dict(payload)


def latest_test_receipt(proposal: JsonObject, stored: JsonObject | None) -> dict[str, object]:
    if stored is None:
        return {"status": "missing", "isCurrentGraph": False, "proofKind": "static_graph_output_contract"}
    stored_result = stored.get("result")
    result: Mapping[str, object] = stored_result if isinstance(stored_result, Mapping) else {}
    expected = proposal.get("graph_fingerprint")
    observed = result.get("graphFingerprint")
    is_current = isinstance(expected, str) and observed == expected
    proof_version = result.get("proofVersion")
    status = (
        str(stored.get("status")) if is_current and proof_version == _PROOF_VERSION else _invalid_status(is_current)
    )
    return {
        "id": stored.get("id"),
        "status": status,
        "isCurrentGraph": is_current,
        "graphFingerprint": observed,
        "createdAt": stored.get("created_at"),
        "createdBy": stored.get("created_by"),
        "proofKind": result.get("proofKind", "static_graph_output_contract"),
        "proofVersion": proof_version,
        "isDataExecution": result.get("isDataExecution", False),
        "testCount": result.get("testCount"),
        "declaredTestCount": result.get("declaredTestCount"),
        "evaluatedChecks": _string_items(result.get("evaluatedChecks")),
        "failureCount": len(_mapping_items(result.get("failures"))),
    }


def require_approvable_test_receipt(receipt: JsonObject) -> None:
    if (
        receipt.get("status") == "passed"
        and receipt.get("isCurrentGraph") is True
        and receipt.get("proofVersion") == _PROOF_VERSION
    ):
        return
    raise ValidationFailed(
        "pipeline approval requires a current passing durable test receipt",
        details={"testReceipt": dict(receipt)},
    )


def require_approvable_change_diff(evidence: JsonObject) -> None:
    change_diff = evidence.get("changeDiff")
    candidate_fingerprint = evidence.get("candidateFingerprint")
    if (
        evidence.get("completeness") == "complete_normalized_graph"
        and isinstance(candidate_fingerprint, str)
        and isinstance(change_diff, Mapping)
        and change_diff.get("graphFingerprint") == candidate_fingerprint
    ):
        return
    raise ValidationFailed(
        "pipeline approval requires a complete immutable branch diff",
        details={"reviewEvidence": dict(evidence)},
    )


def _marker_payload(description: object) -> Mapping[str, object] | None:
    if not isinstance(description, str):
        return None
    position = _marker_position(description)
    if position < 0:
        return None
    prefix_length = len(_MARKER) if position == 0 else len(f"\n\n{_MARKER}")
    raw = description[position + prefix_length :]
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConflictDetected("pipeline proposal review evidence is malformed") from exc
    if not isinstance(payload, Mapping):
        raise ConflictDetected("pipeline proposal review evidence is malformed")
    return payload


def _marker_position(description: str) -> int:
    position = description.rfind(f"\n\n{_MARKER}")
    if position >= 0:
        return position
    return 0 if description.startswith(_MARKER) else -1


def _mapping(value: JsonObject, key: str) -> Mapping[str, object]:
    item = value.get(key)
    if not isinstance(item, Mapping):
        raise ValidationFailed(f"pipeline branch {key} is missing")
    return item


def _mapping_items(value: object) -> list[Mapping[str, object]]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _string_items(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _invalid_status(is_current: bool) -> str:
    return "unsupported" if is_current else "stale"


__all__ = [
    "description_with_review_evidence",
    "latest_test_receipt",
    "proposal_change_diff",
    "public_proposal_description",
    "require_approvable_change_diff",
    "require_approvable_test_receipt",
]
