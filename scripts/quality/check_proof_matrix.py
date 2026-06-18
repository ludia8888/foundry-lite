"""Proof-matrix contract gate.

Fails (with actionable, root-cause-style output) when the single infra matrix
(`docs/infra-tricky-matrix.json`) is internally or cross-file inconsistent:
- an active infra is missing a required proof class
- a proof class has no test mapping, or names a test that does not exist
- the focused quality gate is missing from package.json or not wired into ci_gate.sh
- docs claim active-covered but the matrix has no entry
- per-family source-of-truth rules reference an unknown rule in the registry
- the operator-evidence proof has no required payload paths

Writes artifacts/quality/proof_matrix.json + proof_matrix_summary.md and appends
to the GitHub step summary.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _proof_matrix_lib import (  # noqa: E402
    REQUIRED_PROOF_CLASSES,
    Finding,
    GateResult,
    active_families,
    all_test_names,
    ci_script_text,
    emit,
    infra_ratchet_text,
    load_matrix,
    package_scripts,
)

_MATRIX = "docs/infra-tricky-matrix.json"


def collect_findings() -> list[Finding]:
    matrix = load_matrix()
    scripts = package_scripts()
    ci_text = ci_script_text()
    ratchet = infra_ratchet_text()
    known_tests = all_test_names()
    rules = matrix.get("sourceOfTruthRules", {})
    findings: list[Finding] = []

    for family in active_families(matrix):
        fid = family.get("id", "?")
        test_path = (family.get("testPaths") or ["tests/integration/"])[0]
        proofs = family.get("proofs", {})

        for proof_class in REQUIRED_PROOF_CLASSES:
            mapped = proofs.get(proof_class) or []
            if not mapped:
                findings.append(
                    Finding(
                        area=f"{fid} / {proof_class}",
                        problem="required proof class has no test mapping",
                        why="An active ratchet must prove every proof class; a gap is an untested failure mode.",
                        suggested_files=(_MATRIX, test_path),
                    )
                )
                continue
            for test_name in mapped:
                if test_name not in known_tests:
                    findings.append(
                        Finding(
                            area=f"{fid} / {proof_class}",
                            problem=f"mapped test does not exist in the repo: {test_name}",
                            why="A test name no test defines is silent coverage drift (renamed/deleted test).",
                            suggested_files=(_MATRIX, test_path),
                            tests=(test_name,),
                        )
                    )

        findings.extend(_gate_findings(family, fid, scripts, ci_text))
        findings.extend(_source_of_truth_ref_findings(family, fid, rules))
        findings.extend(_operator_evidence_findings(family, fid, test_path))

    findings.extend(_docs_consistency_findings(matrix, ratchet))
    return findings


def _gate_findings(family: dict, fid: str, scripts: dict[str, str], ci_text: str) -> list[Finding]:
    out: list[Finding] = []
    gate = family.get("qualityScript")
    if not gate:
        out.append(Finding(area=fid, problem="family has no focused qualityScript", suggested_files=(_MATRIX,)))
        return out
    if gate not in scripts:
        out.append(
            Finding(
                area=f"{fid} / {gate}",
                problem="focused quality gate is missing from package.json scripts",
                why="The focused gate must exist so CI and developers can run this infra's proof suite.",
                suggested_files=("package.json", _MATRIX),
            )
        )
    if f"pnpm --silent {gate}" not in ci_text and gate not in ci_text:
        out.append(
            Finding(
                area=f"{fid} / {gate}",
                problem="focused gate is not wired into scripts/ci_gate.sh",
                why="An active infra's proof suite must run in a CI lane, not only on demand.",
                suggested_files=("scripts/ci_gate.sh", _MATRIX),
            )
        )
    return out


def _source_of_truth_ref_findings(family: dict, fid: str, rules: dict) -> list[Finding]:
    out: list[Finding] = []
    for rule_id in family.get("sourceOfTruthRules", []):
        if rule_id not in rules:
            out.append(
                Finding(
                    area=f"{fid} / source-of-truth",
                    problem=f"references unknown source-of-truth rule: {rule_id}",
                    why="A per-family rule must resolve to the sourceOfTruthRules registry to be enforced, not prose.",
                    suggested_files=(_MATRIX,),
                )
            )
    return out


def _operator_evidence_findings(family: dict, fid: str, test_path: str) -> list[Finding]:
    evidence = family.get("operatorEvidence")
    if evidence is None:
        return [
            Finding(
                area=f"{fid} / operator-evidence",
                problem="family has no operatorEvidence block",
                why="Operator evidence must declare the durable payload paths a failure persists, not only logs.",
                suggested_files=(_MATRIX, test_path),
            )
        ]
    if not (evidence.get("requiredPayloadPaths") or []):
        return [
            Finding(
                area=f"{fid} / operator-evidence",
                problem="operatorEvidence.requiredPayloadPaths is empty",
                why="A dangerous failure must persist in a durable run/error payload, not only logs.",
                suggested_files=(_MATRIX, test_path, "scripts/ci_gate.sh"),
            )
        ]
    return []


def _docs_consistency_findings(matrix: dict, ratchet: str) -> list[Finding]:
    out: list[Finding] = []
    for family in matrix.get("families", []):
        if family.get("status") == "active-covered" and family.get("id") not in {
            f.get("id") for f in active_families(matrix)
        }:
            out.append(
                Finding(
                    area=family.get("id", "?"),
                    problem="status is active-covered but family is not in activeStack",
                    suggested_files=(_MATRIX,),
                )
            )
        name = family.get("name", "")
        if family.get("status") == "active-covered" and name and name not in ratchet:
            out.append(
                Finding(
                    area=family.get("id", "?"),
                    problem=f"active-covered infra '{name}' is not described in docs/infra-ratchet.md",
                    why="The matrix and infra-ratchet queue must agree on active-covered infrastructures.",
                    suggested_files=("docs/infra-ratchet.md", _MATRIX),
                )
            )
    return out


def main() -> int:
    result = GateResult(gate="proof_matrix", title="Foundry-lite Proof Matrix Contract", findings=collect_findings())
    return emit(result)


if __name__ == "__main__":
    sys.exit(main())
