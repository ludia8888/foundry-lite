from __future__ import annotations

import copy
from typing import Any

import pytest

from scripts.quality import (
    check_operator_evidence_contract as operator_gate,
)
from scripts.quality import (
    check_proof_matrix as proof_gate,
)
from scripts.quality import (
    check_source_of_truth_contract as sot_gate,
)
from scripts.quality._proof_matrix_lib import ARTIFACTS


def _matrix() -> dict[str, Any]:
    return copy.deepcopy(proof_gate.load_matrix())


def test_proof_matrix_passes_current_repo() -> None:
    assert proof_gate.collect_findings() == []
    assert operator_gate.collect_findings() == []
    assert sot_gate.collect_findings() == []


def test_proof_matrix_fails_when_active_infra_missing_proof_class(monkeypatch: pytest.MonkeyPatch) -> None:
    matrix = _matrix()
    s3 = next(f for f in matrix["families"] if f["id"] == "s3-storage")
    s3["proofs"].pop("operator-evidence", None)
    monkeypatch.setattr(proof_gate, "load_matrix", lambda: matrix)

    findings = proof_gate.collect_findings()
    assert any(f.area == "s3-storage / operator-evidence" and "no test mapping" in f.problem for f in findings)


def test_proof_matrix_fails_when_mapped_test_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    real = proof_gate.all_test_names()
    monkeypatch.setattr(proof_gate, "all_test_names", lambda: real - {"test_s3_dataset_storage_adapter_contract"})

    findings = proof_gate.collect_findings()
    assert any("does not exist in the repo" in f.problem for f in findings)


def test_proof_matrix_fails_when_focused_gate_missing_from_package(monkeypatch: pytest.MonkeyPatch) -> None:
    scripts = {k: v for k, v in proof_gate.package_scripts().items() if k != "quality:s3-storage"}
    monkeypatch.setattr(proof_gate, "package_scripts", lambda: scripts)

    findings = proof_gate.collect_findings()
    assert any("missing from package.json" in f.problem for f in findings)


def test_proof_matrix_fails_when_focused_gate_not_wired_into_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proof_gate, "ci_script_text", lambda: "")

    findings = proof_gate.collect_findings()
    assert any("not wired into scripts/ci_gate.sh" in f.problem for f in findings)


def test_operator_evidence_fails_when_required_payload_paths_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    matrix = _matrix()
    s3 = next(f for f in matrix["families"] if f["id"] == "s3-storage")
    s3["operatorEvidence"]["requiredPayloadPaths"] = []
    monkeypatch.setattr(operator_gate, "load_matrix", lambda: matrix)

    findings = operator_gate.collect_findings()
    assert any("requiredPayloadPaths is empty" in f.problem for f in findings)


def test_source_of_truth_fails_when_deferred_rule_missing_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    matrix = _matrix()
    matrix["sourceOfTruthRules"]["external_timeout_is_outcome_unknown_not_failure"]["deferral"].pop("reason", None)
    monkeypatch.setattr(sot_gate, "load_matrix", lambda: matrix)

    findings = sot_gate.collect_findings()
    assert any("missing deferral fields" in f.problem and "reason" in f.problem for f in findings)


def test_source_of_truth_fails_when_enforced_rule_test_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    matrix = _matrix()
    matrix["sourceOfTruthRules"]["transform_input_version_is_pinned"]["tests"] = ["test_that_does_not_exist_anywhere"]
    monkeypatch.setattr(sot_gate, "load_matrix", lambda: matrix)

    findings = sot_gate.collect_findings()
    assert any("names a missing test" in f.problem for f in findings)


def test_gates_emit_json_and_markdown_artifacts() -> None:
    assert proof_gate.main() == 0
    assert (ARTIFACTS / "proof_matrix.json").is_file()
    assert (ARTIFACTS / "proof_matrix_summary.md").is_file()
    assert sot_gate.main() == 0
    assert (ARTIFACTS / "source_of_truth_contract.json").is_file()
    assert operator_gate.main() == 0
    assert (ARTIFACTS / "operator_evidence.json").is_file()


def test_proof_matrix_failure_output_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    matrix = _matrix()
    next(f for f in matrix["families"] if f["id"] == "spark")["proofs"].pop("failure-injection", None)
    monkeypatch.setattr(proof_gate, "load_matrix", lambda: matrix)

    finding = next(f for f in proof_gate.collect_findings() if f.area == "spark / failure-injection")
    block = finding.render_block()
    assert "why:" in block and "inspect:" in block  # root-cause + suggested files, not a bare assertion
