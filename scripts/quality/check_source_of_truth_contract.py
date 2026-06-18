"""Source-of-truth contract gate.

Source-of-truth rules must not exist only in prose. Every rule in the matrix
registry must be either:
- enforced: backed by at least one regression test that exists, or
- deferred: with an explicit reason, risk tier, future test name, and owning doc.

Every per-family rule reference must resolve to the registry. This blocks the
failure mode where a serving-truth invariant is documented but nothing enforces it.

Writes artifacts/quality/source_of_truth_contract.json + source_of_truth_summary.md.
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
_DEFERRAL_KEYS = ("reason", "riskTier", "futureTest", "owningDoc")


def collect_findings() -> list[Finding]:
    matrix = load_matrix()
    rules = matrix.get("sourceOfTruthRules", {})
    known_tests = all_test_names()
    if not rules:
        return [
            Finding(
                area="source-of-truth",
                problem="no sourceOfTruthRules registry in the matrix",
                why="Serving-truth invariants must be enforced by test/deferral linkage, not prose.",
                suggested_files=(_MATRIX,),
            )
        ]
    findings: list[Finding] = []
    for rule_id, rule in rules.items():
        findings.extend(_rule_findings(rule_id, rule, known_tests))
    for family in active_families(matrix):
        findings.extend(_family_ref_findings(family, rules))
    return findings


def _rule_findings(rule_id: str, rule: dict, known_tests: set[str]) -> list[Finding]:
    status = rule.get("status")
    if status == "enforced":
        return _enforced_findings(rule_id, rule, known_tests)
    if status == "deferred":
        return _deferred_findings(rule_id, rule, known_tests)
    return [
        Finding(
            area=f"rule:{rule_id}",
            problem=f"unknown status '{status}' (expected enforced|deferred)",
            suggested_files=(_MATRIX,),
        )
    ]


def _enforced_findings(rule_id: str, rule: dict, known_tests: set[str]) -> list[Finding]:
    tests = rule.get("tests") or []
    out: list[Finding] = []
    if not tests:
        out.append(
            Finding(
                area=f"rule:{rule_id}",
                problem="enforced source-of-truth rule has no tests",
                why="An enforced invariant must name the regression test(s) that prove it.",
                suggested_files=(_MATRIX,),
            )
        )
    for test_name in tests:
        if test_name not in known_tests:
            out.append(
                Finding(
                    area=f"rule:{rule_id}",
                    problem=f"enforced rule names a missing test: {test_name}",
                    suggested_files=(_MATRIX,),
                    tests=(test_name,),
                )
            )
    return out


def _deferred_findings(rule_id: str, rule: dict, known_tests: set[str]) -> list[Finding]:
    deferral = rule.get("deferral") or {}
    out: list[Finding] = []
    missing = [k for k in _DEFERRAL_KEYS if not deferral.get(k)]
    if missing:
        out.append(
            Finding(
                area=f"rule:{rule_id}",
                problem=f"deferred rule is missing deferral fields: {', '.join(missing)}",
                why="A deferred rule must record reason, risk tier, future test, and owning doc.",
                suggested_files=(_MATRIX,),
            )
        )
    future = deferral.get("futureTest")
    if future and future in known_tests:
        out.append(
            Finding(
                area=f"rule:{rule_id}",
                problem=f"rule is marked deferred but its futureTest already exists: {future}",
                why="If the proving test now exists, promote the rule from deferred to enforced.",
                suggested_files=(_MATRIX,),
                tests=(future,),
            )
        )
    return out


def _family_ref_findings(family: dict, rules: dict) -> list[Finding]:
    fid = family.get("id", "?")
    return [
        Finding(
            area=f"{fid} / source-of-truth",
            problem=f"family references unknown rule: {rule_id}",
            suggested_files=(_MATRIX,),
        )
        for rule_id in family.get("sourceOfTruthRules", [])
        if rule_id not in rules
    ]


def main() -> int:
    result = GateResult(
        gate="source_of_truth_contract",
        title="Foundry-lite Source-of-Truth Contract",
        findings=collect_findings(),
    )
    return emit(result)


if __name__ == "__main__":
    sys.exit(main())
