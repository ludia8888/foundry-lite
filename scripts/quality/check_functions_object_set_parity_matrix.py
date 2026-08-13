"""Validate the official-source Functions/ObjectSet public-behavior matrix."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _proof_matrix_lib import all_test_names  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / "docs" / "functions-object-set-parity-matrix.json"
ALLOWED_STATUSES = {"current", "partial", "planned"}
REQUIRED_IDS = {
    "domain-agent-function-generation",
    "filters-order-pages-aggregations",
    "full-object-set-vocabulary",
    "lazy-object-set-function-input",
    "typed-python-typescript-osdk",
}
REQUIRED_FIELDS = {
    "id",
    "publicBehavior",
    "sourceIds",
    "status",
    "implementationEvidence",
    "proofTests",
    "gaps",
}


def findings(root: Path = ROOT, matrix: Path = MATRIX) -> list[str]:
    if not matrix.exists():
        return ["matrix is missing"]
    payload = json.loads(matrix.read_text(encoding="utf-8"))
    issues = _top_level(payload)
    sources, source_issues = _sources(payload)
    issues.extend(source_issues)
    tests = all_test_names(root)
    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, list):
        return [*issues, "capabilities must be a list"]
    identifiers: set[str] = set()
    for capability in capabilities:
        if isinstance(capability, dict):
            issues.extend(_capability(root, capability, sources, tests, identifiers))
        else:
            issues.append("capability must be an object")
    missing = sorted(REQUIRED_IDS - identifiers)
    if missing:
        issues.append(f"required capabilities are missing: {', '.join(missing)}")
    return issues


def _top_level(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return ["matrix must be an object"]
    issues: list[str] = []
    if payload.get("schemaVersion") != 1:
        issues.append("schemaVersion must be 1")
    if set(_strings(payload.get("statusValues"))) != ALLOWED_STATUSES:
        issues.append("statusValues must be current, partial, and planned")
    if not isinstance(payload.get("lastVerified"), str):
        issues.append("lastVerified is required")
    return issues


def _sources(payload: dict[str, object]) -> tuple[set[str], list[str]]:
    identifiers: set[str] = set()
    issues: list[str] = []
    for source in _objects(payload.get("officialSources")):
        identifier, source_issues = _source_contract(source, identifiers)
        issues.extend(source_issues)
        if identifier is not None:
            identifiers.add(identifier)
    if not identifiers:
        issues.append("officialSources must not be empty")
    return identifiers, issues


def _source_contract(source: dict[str, object], identifiers: set[str]) -> tuple[str | None, list[str]]:
    identifier = source.get("id")
    if not isinstance(identifier, str) or identifier in identifiers:
        return None, ["official source id must be unique text"]
    issues: list[str] = []
    url = source.get("url")
    parsed = urlparse(url if isinstance(url, str) else "")
    if parsed.scheme != "https" or parsed.netloc not in {"palantir.com", "www.palantir.com"}:
        issues.append(f"{identifier} must use an official Palantir HTTPS URL")
    contract = source.get("contract")
    if not isinstance(contract, str) or not contract.strip():
        issues.append(f"{identifier} must state the observed public contract")
    return identifier, issues


def _capability(
    root: Path,
    capability: dict[str, object],
    sources: set[str],
    tests: set[str],
    identifiers: set[str],
) -> list[str]:
    identifier = capability.get("id")
    if not isinstance(identifier, str) or identifier in identifiers:
        return ["capability id must be unique text"]
    identifiers.add(identifier)
    issues = [f"{identifier} is missing {field}" for field in REQUIRED_FIELDS - capability.keys()]
    evidence = _strings(capability.get("implementationEvidence"))
    proof_tests = _strings(capability.get("proofTests"))
    gaps = _strings(capability.get("gaps"))
    issues.extend(_capability_contract(identifier, capability, sources, evidence, proof_tests, gaps))
    issues.extend(_capability_evidence(root, identifier, evidence, proof_tests, tests))
    return issues


def _capability_contract(
    identifier: str,
    capability: dict[str, object],
    sources: set[str],
    evidence: list[str],
    proof_tests: list[str],
    gaps: list[str],
) -> list[str]:
    issues: list[str] = []
    status = capability.get("status")
    if status not in ALLOWED_STATUSES:
        issues.append(f"{identifier} has an invalid status")
    if not set(_strings(capability.get("sourceIds"))).issubset(sources):
        issues.append(f"{identifier} references an unknown official source")
    status_issue = _status_evidence_issue(identifier, status, evidence, proof_tests, gaps)
    if status_issue is not None:
        issues.append(status_issue)
    return issues


def _status_evidence_issue(
    identifier: str,
    status: object,
    evidence: list[str],
    proof_tests: list[str],
    gaps: list[str],
) -> str | None:
    is_valid = {
        "current": _current_is_valid(evidence, proof_tests, gaps),
        "partial": _partial_is_valid(evidence, proof_tests, gaps),
        "planned": _planned_is_valid(evidence, proof_tests, gaps),
    }
    messages = {
        "current": "current status requires evidence/tests and zero gaps",
        "partial": "partial status requires evidence/tests/gaps",
        "planned": "planned status must have only explicit gaps",
    }
    if not isinstance(status, str) or status not in is_valid or is_valid[status]:
        return None
    return f"{identifier} {messages[status]}"


def _current_is_valid(evidence: list[str], proof_tests: list[str], gaps: list[str]) -> bool:
    return bool(evidence) and bool(proof_tests) and not gaps


def _partial_is_valid(evidence: list[str], proof_tests: list[str], gaps: list[str]) -> bool:
    return bool(evidence) and bool(proof_tests) and bool(gaps)


def _planned_is_valid(evidence: list[str], proof_tests: list[str], gaps: list[str]) -> bool:
    return not evidence and not proof_tests and bool(gaps)


def _capability_evidence(
    root: Path,
    identifier: str,
    evidence: list[str],
    proof_tests: list[str],
    tests: set[str],
) -> list[str]:
    issues: list[str] = []
    issues.extend(f"{identifier} missing evidence path {path}" for path in evidence if not (root / path).exists())
    issues.extend(f"{identifier} unknown proof test {name}" for name in proof_tests if name not in tests)
    return issues


def _objects(value: object) -> list[dict[str, object]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _strings(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def main() -> int:
    issues = findings()
    if issues:
        for issue in issues:
            print(f"Functions/ObjectSet parity: {issue}")
        return 1
    print("Functions/ObjectSet parity matrix passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
