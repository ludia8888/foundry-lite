from __future__ import annotations

from copy import deepcopy

import pytest
from foundry_lite.application.services.aip.governed_release_candidate_evidence import (
    release_candidate_evidence,
)
from foundry_lite.application.services.pipeline_graph_model import pipeline_graph_fingerprint
from foundry_lite.application.services.pipeline_graph_normalizer import normalize_pipeline_graph
from foundry_lite.application.services.pipeline_graph_release_diff import (
    pipeline_graph_release_diff,
)
from foundry_lite.application.services.pipeline_proposal_review_evidence import (
    description_with_review_evidence,
    latest_test_receipt,
    proposal_change_diff,
    public_proposal_description,
    require_approvable_change_diff,
    require_approvable_test_receipt,
)
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed

_PROOF_VERSION = "pipeline-static-review-v1"


def test_pipeline_release_diff_covers_every_graph_section_deterministically() -> None:
    base = _base_graph()
    candidate = _candidate_graph()
    original_base = deepcopy(base)
    original_candidate = deepcopy(candidate)

    first = pipeline_graph_release_diff(base, candidate)
    second = pipeline_graph_release_diff(base, candidate)

    assert first == second
    assert first["changed"] is True
    assert base == original_base
    assert candidate == original_candidate
    items = first["items"]
    assert isinstance(items, list)
    assert _change(items, "pipeline_node", "legacy_sql")["changeType"] == "removed"
    assert _change(items, "pipeline_node", "new_sql")["changeType"] == "added"
    assert _change(items, "pipeline_node", "source")["changeType"] == "modified"
    assert _change(items, "pipeline_node", "out")["changeType"] == "modified"
    for resource_type in (
        "pipeline_edge",
        "pipeline_output_contract",
        "pipeline_tests",
        "pipeline_schedule",
        "pipeline_layout",
    ):
        assert any(item.get("resourceType") == resource_type for item in items)
    summary = first["summary"]
    assert isinstance(summary, dict)
    assert summary["totalChangeCount"] == len(items)
    assert summary["addedCount"] > 0
    assert summary["modifiedCount"] > 0
    assert summary["removedCount"] > 0
    assert all("beforeFingerprint" in item and "afterFingerprint" in item for item in items)
    assert all("before" not in item and "after" not in item for item in items)


def test_pipeline_release_diff_reports_an_unchanged_normalized_graph() -> None:
    graph = _base_graph()

    result = pipeline_graph_release_diff(graph, deepcopy(graph))

    assert result["changed"] is False
    assert result["items"] == []
    assert result["baseFingerprint"] == result["graphFingerprint"]
    assert result["summary"]["totalChangeCount"] == 0


def test_proposal_marker_keeps_public_description_and_binds_the_candidate() -> None:
    branch = _branch()

    description = description_with_review_evidence("Operator-authored summary", branch)
    evidence = proposal_change_diff(description, "candidate-fingerprint")

    assert public_proposal_description(description) == "Operator-authored summary"
    assert evidence["proofVersion"] == _PROOF_VERSION
    assert evidence["completeness"] == "complete_normalized_graph"
    assert evidence["candidateFingerprint"] == "candidate-fingerprint"
    assert evidence["changeDiff"]["changed"] is True


def test_last_server_marker_wins_over_a_user_supplied_marker() -> None:
    spoofed = (
        "User text\n\n[pipeline-branch-evidence] "
        '{"candidateFingerprint":"attacker-selected","proofVersion":"pipeline-static-review-v1",'
        '"changeDiff":{}}'
    )

    description = description_with_review_evidence(spoofed, _branch())
    evidence = proposal_change_diff(description, "candidate-fingerprint")

    assert evidence["candidateFingerprint"] == "candidate-fingerprint"
    assert "attacker-selected" in str(public_proposal_description(description))


def test_proposal_marker_rejects_fingerprint_rebinding_and_malformed_json() -> None:
    description = description_with_review_evidence("Summary", _branch())
    tampered = description.replace(
        '"candidateFingerprint":"candidate-fingerprint"',
        '"candidateFingerprint":"attacker-selected"',
    )

    with pytest.raises(ConflictDetected, match="fingerprint mismatch"):
        proposal_change_diff(tampered, "candidate-fingerprint")
    with pytest.raises(ConflictDetected, match="fingerprint mismatch"):
        proposal_change_diff(description, "different-expected-fingerprint")
    with pytest.raises(ConflictDetected, match="malformed"):
        proposal_change_diff("Summary\n\n[pipeline-branch-evidence] {", "candidate-fingerprint")


def test_proposal_marker_rejects_an_unknown_proof_contract() -> None:
    description = description_with_review_evidence("Summary", _branch())
    unsupported = description.replace(
        '"proofVersion":"pipeline-static-review-v1"',
        '"proofVersion":"future-unreviewed-version"',
    )

    with pytest.raises(ConflictDetected, match="unsupported or malformed"):
        proposal_change_diff(unsupported, "candidate-fingerprint")


def test_empty_public_description_still_preserves_an_approvable_immutable_diff() -> None:
    branch = _branch()
    canonical = dict(normalize_pipeline_graph(_candidate_graph()))
    branch["graph"] = canonical
    branch["graph_fingerprint"] = pipeline_graph_fingerprint(canonical)

    description = description_with_review_evidence(None, branch)
    evidence = proposal_change_diff(description, str(branch["graph_fingerprint"]))

    assert public_proposal_description(description) is None
    require_approvable_change_diff(evidence)


def test_pipeline_approval_fails_closed_for_missing_or_rebound_change_diff() -> None:
    with pytest.raises(ValidationFailed, match="complete immutable branch diff"):
        require_approvable_change_diff(proposal_change_diff(None, "candidate-fingerprint"))
    rebound = {
        "completeness": "complete_normalized_graph",
        "candidateFingerprint": "candidate-fingerprint",
        "changeDiff": {"graphFingerprint": "other-fingerprint"},
    }
    with pytest.raises(ValidationFailed, match="complete immutable branch diff"):
        require_approvable_change_diff(rebound)


@pytest.mark.parametrize(
    ("case", "expected_status"),
    [
        ("missing", "missing"),
        ("failed", "failed"),
        ("stale", "stale"),
        ("unsupported", "unsupported"),
    ],
)
def test_pipeline_approval_fails_closed_without_a_current_passing_receipt(
    case: str,
    expected_status: str,
) -> None:
    stored = _stored_receipt_case(case)
    receipt = latest_test_receipt(_proposal_row(), stored)

    assert receipt["status"] == expected_status
    with pytest.raises(ValidationFailed, match="current passing durable test receipt"):
        require_approvable_test_receipt(receipt)


def test_pipeline_approval_accepts_only_the_bound_versioned_static_receipt() -> None:
    receipt = latest_test_receipt(_proposal_row(), _stored_test())

    require_approvable_test_receipt(receipt)

    assert receipt == {
        "id": "test-result-1",
        "status": "passed",
        "isCurrentGraph": True,
        "graphFingerprint": "candidate-fingerprint",
        "createdAt": "2026-08-09T10:00:00Z",
        "createdBy": "pipeline-author",
        "proofKind": "static_graph_output_contract",
        "proofVersion": _PROOF_VERSION,
        "isDataExecution": False,
        "testCount": 3,
        "declaredTestCount": 2,
        "evaluatedChecks": ["graph_validation", "output_descriptor_contracts"],
        "failureCount": 0,
    }


def test_candidate_evidence_never_upgrades_static_tests_into_external_ci() -> None:
    diff = pipeline_graph_release_diff(_base_graph(), _candidate_graph())
    receipt = latest_test_receipt(_proposal_row(), _stored_test())

    evidence = release_candidate_evidence(
        "pipeline",
        {"graph": _candidate_graph(), "graphFingerprint": "candidate-fingerprint"},
        {"changeDiff": diff, "testReceipt": receipt},
    )

    validation = evidence["validationEvidence"]
    assert isinstance(validation, list)
    durable_test = validation[0]
    external_ci = validation[1]
    assert durable_test["status"] == "passed"
    assert durable_test["proofKind"] == "static_graph_output_contract"
    assert durable_test["details"]["isDataExecution"] is False
    assert external_ci == {
        "id": "external-ci-receipt",
        "label": "Candidate-scoped external CI receipt",
        "status": "not_available",
        "proofKind": "external_ci",
        "details": {"reason": "no candidate-scoped external CI receipt is persisted"},
    }
    risk = evidence["riskClassification"]
    assert risk["level"] == "high"
    assert risk["policyVersion"] == "foundry-lite-release-risk-v1"
    assert risk["isComplete"] is False
    assert risk["missingEvidence"] == ["external_ci"]
    assert any("External CI receipt is unavailable" in reason for reason in risk["reasons"])


@pytest.mark.parametrize(
    ("case", "expected_level"),
    [
        ("unclassified", "unclassified"),
        ("low", "low"),
        ("medium", "medium"),
        ("high-change", "high"),
        ("failed-tests", "high"),
    ],
)
def test_pipeline_risk_is_deterministic_and_explicitly_incomplete_without_ci(
    case: str,
    expected_level: str,
) -> None:
    source_evidence = _risk_case(case)

    evidence = release_candidate_evidence(
        "pipeline",
        {"graph": _candidate_graph(), "graphFingerprint": "candidate-fingerprint"},
        source_evidence,
    )

    risk = evidence["riskClassification"]
    assert risk["level"] == expected_level
    assert risk["policyVersion"] == "foundry-lite-release-risk-v1"
    assert risk["isComplete"] is False
    assert risk["missingEvidence"] == ["external_ci"]


def _branch() -> dict[str, object]:
    return {
        "base_graph": _base_graph(),
        "graph": _candidate_graph(),
        "base_version_id": "pipeline-version-1",
        "graph_fingerprint": "candidate-fingerprint",
    }


def _proposal_row() -> dict[str, object]:
    return {"graph_fingerprint": "candidate-fingerprint"}


def _stored_test(
    *,
    status: str = "passed",
    graph_fingerprint: str = "candidate-fingerprint",
    proof_version: str = _PROOF_VERSION,
) -> dict[str, object]:
    return {
        "id": "test-result-1",
        "status": status,
        "created_at": "2026-08-09T10:00:00Z",
        "created_by": "pipeline-author",
        "result": {
            "status": status,
            "graphFingerprint": graph_fingerprint,
            "proofKind": "static_graph_output_contract",
            "proofVersion": proof_version,
            "isDataExecution": False,
            "testCount": 3,
            "declaredTestCount": 2,
            "evaluatedChecks": ["graph_validation", "output_descriptor_contracts"],
            "failures": [] if status == "passed" else [{"test": "graph_validation"}],
        },
    }


def _passing_receipt() -> dict[str, object]:
    return {
        "status": "passed",
        "isCurrentGraph": True,
        "proofKind": "static_graph_output_contract",
        "proofVersion": _PROOF_VERSION,
        "isDataExecution": False,
    }


def _stored_receipt_case(case: str) -> dict[str, object] | None:
    if case == "missing":
        return None
    if case == "failed":
        return _stored_test(status="failed")
    if case == "stale":
        return _stored_test(graph_fingerprint="older-fingerprint")
    return _stored_test(proof_version="unsupported-proof")


def _risk_case(case: str) -> dict[str, object]:
    if case == "unclassified":
        return {"testReceipt": None}
    receipt = _passing_receipt()
    if case == "failed-tests":
        receipt["status"] = "failed"
        return {"changeDiff": {"items": []}, "testReceipt": receipt}
    if case == "low":
        return {"changeDiff": {"items": []}, "testReceipt": receipt}
    resource_type = "pipeline_node" if case == "medium" else "pipeline_schedule"
    return {
        "changeDiff": {"items": [_diff_item(resource_type, "modified")]},
        "testReceipt": receipt,
    }


def _diff_item(resource_type: str, change_type: str) -> dict[str, object]:
    return {
        "resourceType": resource_type,
        "resourceId": "resource-1",
        "changeType": change_type,
        "summary": f"{resource_type} resource-1 {change_type}",
    }


def _change(
    items: list[object],
    resource_type: str,
    resource_id: str,
) -> dict[str, object]:
    for item in items:
        if (
            isinstance(item, dict)
            and item.get("resourceType") == resource_type
            and item.get("resourceId") == resource_id
        ):
            return item
    raise AssertionError(f"missing change: {resource_type}/{resource_id}")


def _base_graph() -> dict[str, object]:
    return {
        "nodes": [
            _node("source", "dataset", {"datasetRef": "raw.orders"}),
            _node("legacy_sql", "sql", {"sql": "select * from source"}),
            _node("out", "output_dataset", {"outputDatasetRef": "clean.orders"}),
        ],
        "edges": [
            {"source": "source", "target": "legacy_sql"},
            {"source": "legacy_sql", "target": "out"},
        ],
        "layout": {"source": {"x": 0, "y": 0}},
        "outputContract": {"columns": [_column("order_id", "string")]},
        "tests": [{"name": "schema-contract-v1"}],
        "schedule": {"kind": "manual"},
    }


def _candidate_graph() -> dict[str, object]:
    return {
        "nodes": [
            _node("source", "dataset", {"datasetRef": "raw.orders_v2"}),
            _node("new_sql", "sql", {"sql": "select order_id, amount from source"}),
            _node("out", "output_dataset", {"outputDatasetRef": "clean.orders_v2"}),
        ],
        "edges": [
            {"source": "source", "target": "new_sql"},
            {"source": "new_sql", "target": "out"},
        ],
        "layout": {"source": {"x": 10, "y": 5}},
        "outputContract": {
            "columns": [_column("order_id", "string"), _column("amount", "int")],
        },
        "tests": [{"name": "schema-contract-v2"}],
        "schedule": {"kind": "cron", "expression": "0 * * * *"},
    }


def _node(node_id: str, node_type: str, config: dict[str, object]) -> dict[str, object]:
    return {"id": node_id, "type": node_type, "config": config}


def _column(name: str, column_type: str) -> dict[str, object]:
    return {"name": name, "type": column_type, "nullable": False}
