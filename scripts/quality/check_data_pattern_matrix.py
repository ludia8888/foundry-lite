"""Validate the S46 data-engineering pattern matrix.

The matrix is the small machine-readable companion to the post-MVP roadmap. It
keeps "current", "partial", "deferred", and "not applicable" claims attached to
evidence, owner, risk, and future tests instead of leaving them as prose.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _proof_matrix_lib import all_test_names  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MATRIX = ROOT / "docs" / "data-engineering-pattern-matrix.json"
DEFAULT_INFRA_MATRIX = ROOT / "docs" / "infra-tricky-matrix.json"
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "data_pattern_matrix.json"

ALLOWED_STATUSES = {"enforced", "partial", "deferred", "not-applicable"}
ALLOWED_RISK_TIERS = {"P0", "P1", "P2", "Product"}
REQUIRED_FIELDS = ("id", "name", "status", "riskTier", "owner", "owningDoc", "reason", "proofLevel")

JsonObject = dict[str, Any]


@dataclass(frozen=True)
class DataPatternMatrixFinding:
    code: str
    path: str
    reference: str
    message: str


def _repo_relative(path: Path, *, root: Path = ROOT) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _load_json(path: Path) -> JsonObject:
    return json.loads(path.read_text(encoding="utf-8"))


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _object_list(value: object) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def collect_findings(
    root: Path = ROOT,
    *,
    matrix_path: Path = DEFAULT_MATRIX,
    infra_matrix_path: Path = DEFAULT_INFRA_MATRIX,
    known_tests: set[str] | None = None,
) -> list[DataPatternMatrixFinding]:
    matrix = matrix_path if matrix_path.is_absolute() else root / matrix_path
    infra_matrix = infra_matrix_path if infra_matrix_path.is_absolute() else root / infra_matrix_path
    missing = _missing_file_findings(root, matrix, infra_matrix)
    if missing:
        return missing

    pattern_matrix = _load_json(matrix)
    infra = _load_json(infra_matrix)
    proof_levels = _proof_levels(pattern_matrix)
    active_refs = _active_infra_refs(infra)
    collected_tests = known_tests if known_tests is not None else all_test_names(root)

    findings: list[DataPatternMatrixFinding] = []
    findings.extend(_top_level_findings(pattern_matrix, matrix, root=root))
    findings.extend(_pattern_findings(pattern_matrix, matrix, proof_levels, active_refs, collected_tests, root=root))
    return findings


def _missing_file_findings(root: Path, *paths: Path) -> list[DataPatternMatrixFinding]:
    findings: list[DataPatternMatrixFinding] = []
    for path in paths:
        if path.exists():
            continue
        findings.append(
            _finding(
                "missing_required_file",
                path,
                _repo_relative(path, root=root),
                "Data pattern matrix gate cannot run without this file.",
                root=root,
            )
        )
    return findings


def _proof_levels(matrix: JsonObject) -> set[str]:
    return {entry["id"] for entry in _object_list(matrix.get("proofLevels")) if isinstance(entry.get("id"), str)}


def _active_infra_refs(infra: JsonObject) -> set[str]:
    refs = set(_string_list(infra.get("activeStack")))
    refs.update(_string_list(infra.get("crossCuttingFlows")))
    for key in ("families", "crossCuttingFlowProofs"):
        for entry in _object_list(infra.get(key)):
            entry_id = entry.get("id")
            if isinstance(entry_id, str) and entry.get("status") == "active-covered":
                refs.add(entry_id)
    return refs


def _top_level_findings(matrix: JsonObject, matrix_path: Path, *, root: Path) -> list[DataPatternMatrixFinding]:
    findings: list[DataPatternMatrixFinding] = []
    if matrix.get("schemaVersion") != 1:
        findings.append(
            _finding("invalid_schema_version", matrix_path, "schemaVersion", "Expected schemaVersion 1.", root=root)
        )
    status_values = set(_string_list(matrix.get("statusValues")))
    if status_values != ALLOWED_STATUSES:
        findings.append(
            _finding(
                "invalid_status_values",
                matrix_path,
                "statusValues",
                "statusValues must exactly match enforced, partial, deferred, and not-applicable.",
                root=root,
            )
        )
    if not _object_list(matrix.get("patterns")):
        findings.append(
            _finding("missing_patterns", matrix_path, "patterns", "At least one pattern is required.", root=root)
        )
    if not _proof_levels(matrix):
        findings.append(
            _finding(
                "missing_proof_levels", matrix_path, "proofLevels", "At least one proof level is required.", root=root
            )
        )
    findings.extend(_active_covered_meaning_findings(matrix, matrix_path, root=root))
    return findings


def _active_covered_meaning_findings(
    matrix: JsonObject, matrix_path: Path, *, root: Path
) -> list[DataPatternMatrixFinding]:
    findings: list[DataPatternMatrixFinding] = []
    required = {
        "activeCoveredMeaning": "Explain what active-covered proves and what it does not prove.",
        "activeCoveredMinimumProofLevel": "Declare the minimum proof level for active-covered.",
        "activeStackCompositionProofLevel": "Declare the proof level for active-stack composition.",
    }
    proof_levels = _proof_levels(matrix)
    for field_name, message in required.items():
        value = matrix.get(field_name)
        if isinstance(value, str) and value:
            continue
        findings.append(_finding("missing_active_covered_meaning", matrix_path, field_name, message, root=root))
    for field_name in ("activeCoveredMinimumProofLevel", "activeStackCompositionProofLevel"):
        value = matrix.get(field_name)
        if not isinstance(value, str) or value in proof_levels:
            continue
        findings.append(
            _finding(
                "unknown_active_covered_proof_level",
                matrix_path,
                f"{field_name}:{value}",
                "Active-covered proof-level field must reference a declared proofLevel.",
                root=root,
            )
        )
    return findings


def _pattern_findings(
    matrix: JsonObject,
    matrix_path: Path,
    proof_levels: set[str],
    active_refs: set[str],
    known_tests: set[str],
    *,
    root: Path,
) -> list[DataPatternMatrixFinding]:
    findings: list[DataPatternMatrixFinding] = []
    seen: set[str] = set()
    for pattern in _object_list(matrix.get("patterns")):
        pattern_id = _field(pattern, "id", "<missing-id>")
        if pattern_id in seen:
            findings.append(
                _finding("duplicate_pattern_id", matrix_path, pattern_id, "Pattern id is duplicated.", root=root)
            )
        seen.add(pattern_id)
        findings.extend(_required_field_findings(pattern, pattern_id, matrix_path, root=root))
        findings.extend(_enum_findings(pattern, pattern_id, matrix_path, proof_levels, root=root))
        findings.extend(_owning_doc_findings(pattern, pattern_id, matrix_path, root=root))
        findings.extend(_infra_ref_findings(pattern, pattern_id, matrix_path, active_refs, root=root))
        findings.extend(_status_specific_findings(pattern, pattern_id, matrix_path, known_tests, root=root))
    return findings


def _field(pattern: JsonObject, field_name: str, default: str = "") -> str:
    value = pattern.get(field_name)
    return value if isinstance(value, str) else default


def _required_field_findings(
    pattern: JsonObject, pattern_id: str, matrix_path: Path, *, root: Path
) -> list[DataPatternMatrixFinding]:
    findings: list[DataPatternMatrixFinding] = []
    for field_name in REQUIRED_FIELDS:
        if _field(pattern, field_name):
            continue
        findings.append(
            _finding(
                "missing_required_pattern_field",
                matrix_path,
                f"{pattern_id}:{field_name}",
                "Pattern is missing a required string field.",
                root=root,
            )
        )
    return findings


def _enum_findings(
    pattern: JsonObject,
    pattern_id: str,
    matrix_path: Path,
    proof_levels: set[str],
    *,
    root: Path,
) -> list[DataPatternMatrixFinding]:
    findings: list[DataPatternMatrixFinding] = []
    status = _field(pattern, "status")
    if status and status not in ALLOWED_STATUSES:
        findings.append(
            _finding(
                "invalid_status", matrix_path, f"{pattern_id}:{status}", "Pattern status is not allowed.", root=root
            )
        )
    risk_tier = _field(pattern, "riskTier")
    if risk_tier and risk_tier not in ALLOWED_RISK_TIERS:
        findings.append(
            _finding(
                "invalid_risk_tier",
                matrix_path,
                f"{pattern_id}:{risk_tier}",
                "Pattern riskTier is not allowed.",
                root=root,
            )
        )
    proof_level = _field(pattern, "proofLevel")
    if proof_level and proof_level not in proof_levels:
        findings.append(
            _finding(
                "unknown_proof_level",
                matrix_path,
                f"{pattern_id}:{proof_level}",
                "Pattern proofLevel is not declared in proofLevels.",
                root=root,
            )
        )
    return findings


def _owning_doc_findings(
    pattern: JsonObject, pattern_id: str, matrix_path: Path, *, root: Path
) -> list[DataPatternMatrixFinding]:
    owning_doc = _field(pattern, "owningDoc")
    if not owning_doc:
        return []
    if (root / owning_doc).exists():
        return []
    return [
        _finding(
            "missing_owning_doc",
            matrix_path,
            f"{pattern_id}:{owning_doc}",
            "Pattern owningDoc does not exist.",
            root=root,
        )
    ]


def _infra_ref_findings(
    pattern: JsonObject,
    pattern_id: str,
    matrix_path: Path,
    active_refs: set[str],
    *,
    root: Path,
) -> list[DataPatternMatrixFinding]:
    return [
        _finding(
            "unknown_active_infra_ref",
            matrix_path,
            f"{pattern_id}:{infra_ref}",
            "activeInfraRefs must point to an active family or cross-cutting flow in infra-tricky-matrix.",
            root=root,
        )
        for infra_ref in _string_list(pattern.get("activeInfraRefs"))
        if infra_ref not in active_refs
    ]


def _status_specific_findings(
    pattern: JsonObject,
    pattern_id: str,
    matrix_path: Path,
    known_tests: set[str],
    *,
    root: Path,
) -> list[DataPatternMatrixFinding]:
    status = _field(pattern, "status")
    if status == "enforced":
        return _enforced_findings(pattern, pattern_id, matrix_path, known_tests, root=root)
    if status == "partial":
        return _partial_findings(pattern, pattern_id, matrix_path, known_tests, root=root)
    if status == "deferred":
        return _deferred_findings(pattern, pattern_id, matrix_path, known_tests, root=root)
    if status == "not-applicable":
        return _not_applicable_findings(pattern, pattern_id, matrix_path, root=root)
    return []


def _enforced_findings(
    pattern: JsonObject,
    pattern_id: str,
    matrix_path: Path,
    known_tests: set[str],
    *,
    root: Path,
) -> list[DataPatternMatrixFinding]:
    evidence_refs = _string_list(pattern.get("evidenceRefs"))
    if not evidence_refs:
        return [
            _finding(
                "enforced_pattern_missing_evidence",
                matrix_path,
                pattern_id,
                "Enforced pattern must name evidenceRefs.",
                root=root,
            )
        ]
    return _missing_test_findings(
        "evidence_test_missing", pattern_id, evidence_refs, matrix_path, known_tests, root=root
    )


def _partial_findings(
    pattern: JsonObject,
    pattern_id: str,
    matrix_path: Path,
    known_tests: set[str],
    *,
    root: Path,
) -> list[DataPatternMatrixFinding]:
    findings: list[DataPatternMatrixFinding] = []
    evidence_refs = _string_list(pattern.get("evidenceRefs"))
    future_tests = _string_list(pattern.get("futureTests"))
    readme_aliases = _string_list(pattern.get("readmeAliases"))
    if not evidence_refs:
        findings.append(
            _finding(
                "partial_pattern_missing_evidence",
                matrix_path,
                pattern_id,
                "Partial pattern needs evidenceRefs.",
                root=root,
            )
        )
    if not future_tests:
        findings.append(
            _finding(
                "partial_pattern_missing_future_tests",
                matrix_path,
                pattern_id,
                "Partial pattern needs futureTests.",
                root=root,
            )
        )
    if not readme_aliases:
        findings.append(
            _finding(
                "partial_pattern_missing_readme_aliases",
                matrix_path,
                pattern_id,
                "Partial pattern needs readmeAliases for semantic README comparison.",
                root=root,
            )
        )
    findings.extend(
        _missing_test_findings("evidence_test_missing", pattern_id, evidence_refs, matrix_path, known_tests, root=root)
    )
    findings.extend(_future_test_name_findings(pattern_id, future_tests, matrix_path, known_tests, root=root))
    return findings


def _deferred_findings(
    pattern: JsonObject,
    pattern_id: str,
    matrix_path: Path,
    known_tests: set[str],
    *,
    root: Path,
) -> list[DataPatternMatrixFinding]:
    future_tests = _string_list(pattern.get("futureTests"))
    findings: list[DataPatternMatrixFinding] = []
    if not future_tests:
        findings.append(
            _finding(
                "deferred_pattern_missing_future_tests",
                matrix_path,
                pattern_id,
                "Deferred pattern must name futureTests.",
                root=root,
            )
        )
    if not _string_list(pattern.get("readmeAliases")):
        findings.append(
            _finding(
                "deferred_pattern_missing_readme_aliases",
                matrix_path,
                pattern_id,
                "Deferred pattern needs readmeAliases for semantic README comparison.",
                root=root,
            )
        )
    findings.extend(_future_test_name_findings(pattern_id, future_tests, matrix_path, known_tests, root=root))
    return findings


def _not_applicable_findings(
    pattern: JsonObject, pattern_id: str, matrix_path: Path, *, root: Path
) -> list[DataPatternMatrixFinding]:
    if _field(pattern, "notApplicableReason"):
        return []
    return [
        _finding(
            "not_applicable_missing_reason",
            matrix_path,
            pattern_id,
            "Not-applicable pattern must explain notApplicableReason.",
            root=root,
        )
    ]


def _missing_test_findings(
    code: str,
    pattern_id: str,
    test_names: list[str],
    matrix_path: Path,
    known_tests: set[str],
    *,
    root: Path,
) -> list[DataPatternMatrixFinding]:
    return [
        _finding(
            code,
            matrix_path,
            f"{pattern_id}:{test_name}",
            "Evidence test is not collected by pytest.",
            root=root,
        )
        for test_name in test_names
        if test_name.startswith("test_") and test_name not in known_tests
    ]


def _future_test_name_findings(
    pattern_id: str,
    future_tests: list[str],
    matrix_path: Path,
    known_tests: set[str],
    *,
    root: Path,
) -> list[DataPatternMatrixFinding]:
    findings: list[DataPatternMatrixFinding] = []
    for test_name in future_tests:
        if not test_name.startswith("test_"):
            findings.append(
                _finding(
                    "future_test_name_invalid",
                    matrix_path,
                    f"{pattern_id}:{test_name}",
                    "futureTests entries must be pytest-style test names.",
                    root=root,
                )
            )
        if test_name in known_tests:
            findings.append(
                _finding(
                    "future_test_already_exists",
                    matrix_path,
                    f"{pattern_id}:{test_name}",
                    "A futureTest already exists; promote the pattern or move the test to evidenceRefs.",
                    root=root,
                )
            )
    return findings


def _finding(
    code: str,
    path: Path,
    reference: str,
    message: str,
    *,
    root: Path,
) -> DataPatternMatrixFinding:
    return DataPatternMatrixFinding(
        code=code,
        path=_repo_relative(path, root=root),
        reference=reference,
        message=message,
    )


def write_report(output: Path, findings: list[DataPatternMatrixFinding]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "gate": "data_pattern_matrix",
        "baseline": 0,
        "gate_pass": not findings,
        "allowedStatuses": sorted(ALLOWED_STATUSES),
        "violations": [asdict(finding) for finding in findings],
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _print_failure(findings: list[DataPatternMatrixFinding], output: Path) -> None:
    print("Data pattern matrix gate failed:")
    for finding in findings:
        print(f"- {finding.path}: {finding.code}: {finding.reference} - {finding.message}")
    print(f"Report: {_repo_relative(output)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate data-engineering pattern matrix.")
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--infra-matrix", type=Path, default=DEFAULT_INFRA_MATRIX)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    findings = collect_findings(matrix_path=args.matrix, infra_matrix_path=args.infra_matrix)
    write_report(args.output, findings)
    if findings:
        _print_failure(findings, args.output)
        return 1
    print("Data pattern matrix gate passed: pattern status, evidence, and future tests are registered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
