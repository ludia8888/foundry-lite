"""Operator-evidence contract gate.

A dangerous runtime failure must be visible in durable evidence (run/audit/
transaction/error/outbox/trace payloads), never only in a log line. This gate
enforces, per active infra, that the operator-evidence proof:
- names at least one regression test that is collected by pytest,
- declares the durable payload paths that failure must persist,
- declares at least one durable evidence category.
- maps every required payload path to the test(s) that assert it.

The runtime payload keys themselves are asserted by the named integration tests
(e.g. `test_s3_storage_failure_is_visible_in_operations`), which inject the
failure; this gate guarantees that contract is declared, mapped to a run surface,
and proven by a real test.

Writes artifacts/quality/operator_evidence.json + operator_evidence_summary.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _proof_matrix_lib import (  # noqa: E402
    Finding,
    GateResult,
    active_families,
    all_test_names,
    emit,
    load_matrix,
)

_MATRIX = "docs/infra-tricky-matrix.json"


def collect_findings() -> list[Finding]:
    matrix = load_matrix()
    known_tests = all_test_names()
    findings: list[Finding] = []
    for family in active_families(matrix):
        findings.extend(_family_findings(family, known_tests))
    # The runtime payload keys themselves are asserted by the named integration tests
    # (which inject the failure); this gate enforces that the contract is declared and proven.
    return findings


def _family_findings(family: dict, known_tests: set[str]) -> list[Finding]:
    fid = family.get("id", "?")
    test_path = (family.get("testPaths") or ["tests/integration/"])[0]
    evidence = family.get("operatorEvidence")
    if evidence is None:
        return [
            Finding(
                area=f"{fid} / operator-evidence",
                problem="no operatorEvidence contract declared",
                why="Failure must persist in durable run/error payload; declare requiredPayloadPaths.",
                suggested_files=(_MATRIX, test_path),
            )
        ]
    oe_tests = (family.get("proofs", {}) or {}).get("operator-evidence") or []
    findings = _test_findings(fid, test_path, oe_tests, known_tests)
    findings.extend(_field_findings(fid, test_path, evidence))
    findings.extend(_assertion_findings(fid, test_path, evidence, oe_tests, known_tests))
    return findings


def _test_findings(fid: str, test_path: str, oe_tests: list[str], known_tests: set[str]) -> list[Finding]:
    out: list[Finding] = []
    if not oe_tests:
        out.append(
            Finding(
                area=f"{fid} / operator-evidence",
                problem="operator-evidence proof has no test",
                why="The durable-evidence contract needs a regression test that asserts the payload keys.",
                suggested_files=(_MATRIX, test_path),
            )
        )
    for test_name in oe_tests:
        if test_name not in known_tests:
            out.append(
                Finding(
                    area=f"{fid} / operator-evidence",
                    problem=f"operator-evidence test does not exist: {test_name}",
                    suggested_files=(_MATRIX, test_path),
                    tests=(test_name,),
                )
            )
    return out


def _field_findings(fid: str, test_path: str, evidence: dict) -> list[Finding]:
    required = {
        "requiredPayloadPaths": "A failure with no required durable payload path can hide in logs only.",
        "durableEvidenceCategories": "Declare which durable surfaces hold the evidence (run/audit/error/outbox/trace).",
        "runField": "Declare which run surface (syncRuns/transformRuns/...) carries the failure payload.",
    }
    out: list[Finding] = []
    for field_name, why in required.items():
        if not (evidence.get(field_name) or None):
            out.append(
                Finding(
                    area=f"{fid} / operator-evidence",
                    problem=f"operatorEvidence.{field_name} is empty or missing",
                    why=why,
                    suggested_files=(_MATRIX, test_path),
                )
            )
    return out


def _assertion_findings(
    fid: str,
    test_path: str,
    evidence: dict,
    oe_tests: list[str],
    known_tests: set[str],
) -> list[Finding]:
    required_paths = evidence.get("requiredPayloadPaths") or []
    assertions = evidence.get("testAssertions") or {}
    if not isinstance(assertions, dict) or not assertions:
        return [
            Finding(
                area=f"{fid} / operator-evidence",
                problem="operatorEvidence.testAssertions is empty or missing",
                why="Each required payload path must name the pytest evidence that asserts it.",
                suggested_files=(_MATRIX, test_path),
            )
        ]
    findings: list[Finding] = []
    for path in required_paths:
        findings.extend(_path_assertion_findings(fid, test_path, str(path), assertions, oe_tests, known_tests))
    return findings


def _path_assertion_findings(
    fid: str,
    test_path: str,
    path: str,
    assertions: dict,
    oe_tests: list[str],
    known_tests: set[str],
) -> list[Finding]:
    mapped_tests = assertions.get(path) or []
    if not mapped_tests:
        return [
            Finding(
                area=f"{fid} / operator-evidence",
                problem=f"required payload path has no assertion mapping: {path}",
                why="A declared operator payload path must be tied to a specific regression test.",
                suggested_files=(_MATRIX, test_path),
            )
        ]
    return _assertion_test_findings(fid, test_path, path, mapped_tests, oe_tests, known_tests)


def _assertion_test_findings(
    fid: str,
    test_path: str,
    path: str,
    mapped_tests: list[str],
    oe_tests: list[str],
    known_tests: set[str],
) -> list[Finding]:
    findings: list[Finding] = []
    for test_name in mapped_tests:
        if test_name not in oe_tests:
            findings.append(
                Finding(
                    area=f"{fid} / operator-evidence",
                    problem=f"assertion test is not listed under proofs.operator-evidence: {test_name}",
                    why=f"The path '{path}' must be asserted by an operator-evidence proof test.",
                    suggested_files=(_MATRIX, test_path),
                    tests=(test_name,),
                )
            )
        if test_name not in known_tests:
            findings.append(
                Finding(
                    area=f"{fid} / operator-evidence",
                    problem=f"assertion test is not collected by pytest: {test_name}",
                    suggested_files=(_MATRIX, test_path),
                    tests=(test_name,),
                )
            )
    return findings


def main() -> int:
    result = GateResult(
        gate="operator_evidence",
        title="Foundry-lite Operator Evidence Contract",
        findings=collect_findings(),
    )
    return emit(result)


if __name__ == "__main__":
    sys.exit(main())
