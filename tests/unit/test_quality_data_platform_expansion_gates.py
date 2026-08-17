from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from scripts.quality import check_data_pattern_matrix as pattern_gate
from scripts.quality import check_semantic_doc_consistency as semantic_gate


def test_s46_expansion_gates_pass_current_repo() -> None:
    assert semantic_gate.collect_findings() == []
    assert pattern_gate.collect_findings() == []


def test_semantic_docs_reject_active_infra_described_as_future(tmp_path: Path) -> None:
    _write_semantic_tree(
        tmp_path,
        implementation_status="S3 storage ratchet proof is active-covered.\n",
        readme="S3 storage is future.\n",
    )

    findings = _collect_semantic(tmp_path)

    assert any(finding.code == "active_family_described_as_unqualified_future" for finding in findings)


def test_implementation_status_lists_every_active_matrix_family(tmp_path: Path) -> None:
    _write_semantic_tree(
        tmp_path,
        implementation_status="No storage proof is mentioned here.\n",
        readme="S3 managed cloud operations remain future scope.\n",
    )

    findings = _collect_semantic(tmp_path)

    assert any(finding.code == "active_family_missing_current_status" for finding in findings)


def test_semantic_docs_allow_qualified_future_scope(tmp_path: Path) -> None:
    _write_semantic_tree(
        tmp_path,
        implementation_status="S3 storage ratchet proof is active-covered.\n",
        readme="S3 managed cloud operations remain future scope.\nRecord DLQ remains future work.\n",
    )

    assert _collect_semantic(tmp_path) == []


def test_readme_does_not_claim_deferred_feature_as_active(tmp_path: Path) -> None:
    _write_semantic_tree(
        tmp_path,
        implementation_status="S3 storage ratchet proof is active-covered.\n",
        readme="Record DLQ is implemented.\n",
    )

    findings = _collect_semantic(tmp_path)

    assert any(finding.code == "readme_deferred_pattern_claimed_current" for finding in findings)


def test_readme_must_mention_deferred_pattern_alias(tmp_path: Path) -> None:
    _write_semantic_tree(
        tmp_path,
        implementation_status="S3 storage ratchet proof is active-covered.\n",
        readme="S3 managed cloud operations remain future scope.\n",
    )

    findings = _collect_semantic(tmp_path)

    assert any(finding.code == "readme_pattern_missing" for finding in findings)


def test_non_readme_doc_cannot_claim_deferred_feature_as_active(tmp_path: Path) -> None:
    _write_semantic_tree(
        tmp_path,
        implementation_status="S3 storage ratchet proof is active-covered.\n",
        readme="Record DLQ remains future work.\n",
    )
    doc_path = tmp_path / "docs" / "quality-gate-roadmap.md"
    doc_path.write_text("Record DLQ is implemented.\n", encoding="utf-8")

    findings = semantic_gate.collect_findings(
        tmp_path,
        infra_matrix_path=tmp_path / "docs" / "infra-tricky-matrix.json",
        pattern_matrix_path=tmp_path / "docs" / "data-engineering-pattern-matrix.json",
        docs=(doc_path,),
        implementation_status_path=tmp_path / "docs" / "implementation-status.md",
        readme_path=tmp_path / "README.md",
    )

    assert any(finding.code == "doc_deferred_pattern_claimed_current" for finding in findings)


def test_release_truth_rejects_protected_author_self_review_claim(tmp_path: Path) -> None:
    _write_semantic_tree(
        tmp_path,
        implementation_status="S3 storage ratchet proof is active-covered.\n",
        readme="Record DLQ remains future work.\n",
    )
    ledger = tmp_path / "docs" / "sprint-evidence-ledger.md"
    ledger.write_text("Protected reviewer claim (author self-claim allowed).\n", encoding="utf-8")

    findings = _collect_semantic(tmp_path)

    assert any(finding.code == "protected_author_self_review_claim" for finding in findings)


def test_release_truth_rejects_implemented_jsonb_in_target_list(tmp_path: Path) -> None:
    _write_semantic_tree(
        tmp_path,
        implementation_status=(
            "S3 storage ratchet proof is active-covered.\n"
            "- PostgreSQL JSONB object store with production indexes and row-level security.\n"
        ),
        readme="Record DLQ remains future work.\n",
    )

    findings = _collect_semantic(tmp_path)

    assert any(finding.code == "implemented_jsonb_described_as_target" for finding in findings)


def test_data_pattern_matrix_requires_future_test_for_deferred(tmp_path: Path) -> None:
    _write_pattern_tree(tmp_path)
    matrix_path = tmp_path / "docs" / "data-engineering-pattern-matrix.json"
    matrix = _load_json(matrix_path)
    matrix["patterns"][1].pop("futureTests")
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    findings = _collect_patterns(tmp_path)

    assert any(finding.code == "deferred_pattern_missing_future_tests" for finding in findings)


def test_data_pattern_matrix_requires_reason_for_not_applicable(tmp_path: Path) -> None:
    _write_pattern_tree(tmp_path)
    matrix_path = tmp_path / "docs" / "data-engineering-pattern-matrix.json"
    matrix = _load_json(matrix_path)
    matrix["patterns"][2].pop("notApplicableReason")
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    findings = _collect_patterns(tmp_path)

    assert any(finding.code == "not_applicable_missing_reason" for finding in findings)


def test_data_pattern_matrix_requires_active_covered_meaning(tmp_path: Path) -> None:
    _write_pattern_tree(tmp_path)
    matrix_path = tmp_path / "docs" / "data-engineering-pattern-matrix.json"
    matrix = _load_json(matrix_path)
    matrix.pop("activeCoveredMeaning")
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    findings = _collect_patterns(tmp_path)

    assert any(finding.code == "missing_active_covered_meaning" for finding in findings)


def test_data_pattern_matrix_requires_readme_alias_for_deferred(tmp_path: Path) -> None:
    _write_pattern_tree(tmp_path)
    matrix_path = tmp_path / "docs" / "data-engineering-pattern-matrix.json"
    matrix = _load_json(matrix_path)
    matrix["patterns"][1].pop("readmeAliases")
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    findings = _collect_patterns(tmp_path)

    assert any(finding.code == "deferred_pattern_missing_readme_aliases" for finding in findings)


def test_data_pattern_matrix_rejects_unknown_active_infra_ref(tmp_path: Path) -> None:
    _write_pattern_tree(tmp_path)
    matrix_path = tmp_path / "docs" / "data-engineering-pattern-matrix.json"
    matrix = _load_json(matrix_path)
    matrix["patterns"][0]["activeInfraRefs"] = ["missing-family"]
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    findings = _collect_patterns(tmp_path)

    assert any(finding.code == "unknown_active_infra_ref" for finding in findings)


def test_proof_level_is_valid_and_monotonic(tmp_path: Path) -> None:
    _write_pattern_tree(tmp_path)
    matrix_path = tmp_path / "docs" / "data-engineering-pattern-matrix.json"
    matrix = _load_json(matrix_path)
    matrix["patterns"][0]["proofLevel"] = "L99"
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    findings = _collect_patterns(tmp_path)

    assert any(finding.code == "unknown_proof_level" for finding in findings)


def _collect_semantic(root: Path) -> list[semantic_gate.SemanticDocFinding]:
    return semantic_gate.collect_findings(
        root,
        infra_matrix_path=root / "docs" / "infra-tricky-matrix.json",
        pattern_matrix_path=root / "docs" / "data-engineering-pattern-matrix.json",
        docs=(root / "README.md",),
        implementation_status_path=root / "docs" / "implementation-status.md",
        readme_path=root / "README.md",
    )


def _collect_patterns(root: Path) -> list[pattern_gate.DataPatternMatrixFinding]:
    return pattern_gate.collect_findings(
        root,
        matrix_path=root / "docs" / "data-engineering-pattern-matrix.json",
        infra_matrix_path=root / "docs" / "infra-tricky-matrix.json",
        known_tests={"test_pattern_proof"},
    )


def _write_semantic_tree(tmp_path: Path, *, implementation_status: str, readme: str) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "implementation-status.md").write_text(implementation_status, encoding="utf-8")
    (tmp_path / "README.md").write_text(readme, encoding="utf-8")
    (docs / "infra-tricky-matrix.json").write_text(json.dumps(_infra_matrix()), encoding="utf-8")
    (docs / "data-engineering-pattern-matrix.json").write_text(json.dumps(_pattern_matrix()), encoding="utf-8")


def _write_pattern_tree(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "pattern-owner.md").write_text("owner\n", encoding="utf-8")
    (docs / "infra-tricky-matrix.json").write_text(json.dumps(_infra_matrix()), encoding="utf-8")
    (docs / "data-engineering-pattern-matrix.json").write_text(json.dumps(_pattern_matrix()), encoding="utf-8")


def _infra_matrix() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "activeStack": ["s3-storage"],
        "crossCuttingFlows": [],
        "families": [
            {
                "id": "s3-storage",
                "name": "MinIO/S3 DatasetStorageAdapter",
                "status": "active-covered",
            }
        ],
        "crossCuttingFlowProofs": [],
    }


def _pattern_matrix() -> dict[str, Any]:
    return copy.deepcopy(
        {
            "schemaVersion": 1,
            "statusValues": ["enforced", "partial", "deferred", "not-applicable"],
            "proofLevels": [{"id": "L0"}, {"id": "L1"}],
            "activeCoveredMeaning": "active-covered means focused current proof, not managed operations.",
            "activeCoveredMinimumProofLevel": "L1",
            "activeStackCompositionProofLevel": "L1",
            "patterns": [
                {
                    "id": "enforced-pattern",
                    "name": "Enforced pattern",
                    "status": "enforced",
                    "riskTier": "P0",
                    "owner": "runtime",
                    "owningDoc": "docs/pattern-owner.md",
                    "reason": "It is covered.",
                    "proofLevel": "L1",
                    "activeInfraRefs": ["s3-storage"],
                    "evidenceRefs": ["test_pattern_proof"],
                },
                {
                    "id": "deferred-pattern",
                    "name": "Deferred pattern",
                    "status": "deferred",
                    "riskTier": "P1",
                    "owner": "runtime",
                    "owningDoc": "docs/pattern-owner.md",
                    "reason": "It is future work.",
                    "proofLevel": "L0",
                    "activeInfraRefs": [],
                    "futureTests": ["test_future_pattern_proof"],
                    "readmeAliases": ["Record DLQ"],
                },
                {
                    "id": "not-applicable-pattern",
                    "name": "Not applicable pattern",
                    "status": "not-applicable",
                    "riskTier": "P2",
                    "owner": "runtime",
                    "owningDoc": "docs/pattern-owner.md",
                    "reason": "It does not apply.",
                    "proofLevel": "L0",
                    "activeInfraRefs": [],
                    "notApplicableReason": "This profile is outside the selected stack.",
                },
            ],
        }
    )


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
