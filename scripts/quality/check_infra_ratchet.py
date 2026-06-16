"""Enforces infra-ratchet rollout discipline for one-infrastructure-at-a-time hardening.

The gate keeps CI, docs, and the active infrastructure queue synchronized so
new scale infrastructure cannot bypass failure/concurrency/retry/recovery
evidence expectations.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "infra_ratchet.json"

REQUIRED_PROOF_CLASSES = {
    "adapter-contract",
    "normal-path",
    "failure-injection",
    "concurrency-race",
    "retry-idempotency",
    "partial-success",
    "recovery-cleanup",
    "operator-evidence",
    "docs-sync",
}

REQUIRED_INFRA_QUEUE_ITEMS = (
    "MinIO/S3 DatasetStorageAdapter",
    "Iceberg Catalog/TableAdapter",
)

REQUIRED_S3_TEST_NAMES = (
    "test_s3_dataset_storage_adapter_contract",
    "test_s3_partial_multipart_upload_never_becomes_committed_version",
    "test_s3_commit_storage_success_db_failure_creates_orphan_cleanup_evidence",
    "test_s3_committed_manifest_missing_marks_storage_corruption",
    "test_s3_abort_cleanup_never_deletes_committed_manifest",
    "test_s3_concurrent_dataset_commits_allocate_strictly_increasing_versions",
    "test_s3_retry_after_storage_timeout_does_not_duplicate_version",
    "test_s3_storage_failure_is_visible_in_operations",
)

S3_RATCHET_TEST_PATH = Path("tests/integration/test_s3_dataset_storage_adapter.py")


@dataclass(frozen=True)
class InfraRatchetFinding:
    code: str
    path: str
    message: str


def _repo_relative(path: Path, *, root: Path = ROOT) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _missing_terms(path: Path, text: str, terms: tuple[str, ...] | set[str], *, code: str) -> list[InfraRatchetFinding]:
    findings: list[InfraRatchetFinding] = []
    for term in sorted(terms):
        if term not in text:
            findings.append(
                InfraRatchetFinding(
                    code=code,
                    path=_repo_relative(path),
                    message=f"missing required infra-ratchet term: {term}",
                )
            )
    return findings


def _missing_file(path: Path) -> InfraRatchetFinding:
    return InfraRatchetFinding(
        code="missing_infra_ratchet_file",
        path=_repo_relative(path),
        message="required infra-ratchet source file is missing",
    )


def _document_findings(root: Path) -> list[InfraRatchetFinding]:
    doc_path = root / "docs" / "infra-ratchet.md"
    if not doc_path.exists():
        return [_missing_file(doc_path)]
    doc = _text(doc_path)
    findings: list[InfraRatchetFinding] = []
    findings.extend(_missing_terms(doc_path, doc, REQUIRED_PROOF_CLASSES, code="missing_proof_class"))
    findings.extend(_missing_terms(doc_path, doc, REQUIRED_INFRA_QUEUE_ITEMS, code="missing_queue_item"))
    findings.extend(_missing_terms(doc_path, doc, REQUIRED_S3_TEST_NAMES, code="missing_reserved_s3_test"))
    findings.extend(
        _missing_terms(
            doc_path,
            doc,
            (
                "One Infrastructure At A Time",
                "Active Ratchet Queue",
                "Pull Request Exit Checklist",
                "MinIO/Testcontainers before AWS S3",
            ),
            code="missing_ratchet_section",
        )
    )
    return findings


def _cross_document_findings(root: Path) -> list[InfraRatchetFinding]:
    required = {
        root / "docs" / "commit-point-risk-register.md": (
            "Production storage split-brain extensions",
            "test_partial_multipart_upload_never_becomes_committed_version",
        ),
        root / "docs" / "foundry_lite_tricky_failure_modes_checklist.md": (
            "T0-011",
            "test_s3_partial_multipart_upload_never_becomes_committed_version",
        ),
        root / "docs" / "implementation-status.md": (
            "Infra Ratchet",
            "S3DatasetStorageAdapter",
        ),
        root / "README.md": (
            "Infra Ratchet",
            "check_infra_ratchet.py",
        ),
    }
    findings: list[InfraRatchetFinding] = []
    for path, terms in required.items():
        if not path.exists():
            findings.append(_missing_file(path))
            continue
        findings.extend(_missing_terms(path, _text(path), terms, code="missing_cross_doc_term"))
    return findings


def _ci_findings(root: Path) -> list[InfraRatchetFinding]:
    package_path = root / "package.json"
    ci_path = root / "scripts" / "ci_gate.sh"
    findings: list[InfraRatchetFinding] = []
    if not package_path.exists():
        findings.append(_missing_file(package_path))
    else:
        package_text = _text(package_path)
        findings.extend(
            _missing_terms(
                package_path,
                package_text,
                (
                    '"quality:infra-ratchet"',
                    "pnpm quality:infra-ratchet",
                    '"quality:s3-storage"',
                    "tests/integration/test_s3_dataset_storage_adapter.py",
                ),
                code="missing_ci_wiring",
            )
        )
    if not ci_path.exists():
        findings.append(_missing_file(ci_path))
    else:
        findings.extend(
            _missing_terms(
                ci_path,
                _text(ci_path),
                (
                    "scripts/quality/check_infra_ratchet.py",
                    "pnpm --silent quality:s3-storage",
                ),
                code="missing_ci_wiring",
            )
        )
    return findings


def _s3_test_findings(root: Path) -> list[InfraRatchetFinding]:
    test_path = root / S3_RATCHET_TEST_PATH
    if not test_path.exists():
        return [_missing_file(test_path)]
    return _missing_terms(test_path, _text(test_path), REQUIRED_S3_TEST_NAMES, code="missing_s3_ratchet_test")


def collect_findings(root: Path = ROOT) -> list[InfraRatchetFinding]:
    findings: list[InfraRatchetFinding] = []
    findings.extend(_document_findings(root))
    findings.extend(_cross_document_findings(root))
    findings.extend(_ci_findings(root))
    findings.extend(_s3_test_findings(root))
    return findings


def write_report(output: Path, findings: list[InfraRatchetFinding]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "gate": "infra_ratchet",
        "baseline": 0,
        "count": len(findings),
        "gate_pass": not findings,
        "requiredProofClasses": sorted(REQUIRED_PROOF_CLASSES),
        "activeQueuePrefix": list(REQUIRED_INFRA_QUEUE_ITEMS),
        "reservedS3RegressionTests": list(REQUIRED_S3_TEST_NAMES),
        "violations": [asdict(finding) for finding in findings],
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(findings: list[InfraRatchetFinding], output: Path) -> None:
    print("Infra ratchet gate failed:")
    for finding in findings:
        print(f"- {finding.path}: {finding.code}: {finding.message}")
    print(f"Report: {_repo_relative(output)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate one-infrastructure-at-a-time rollout discipline.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    findings = collect_findings()
    write_report(args.output, findings)
    if findings:
        _print_failure(findings, args.output)
        return 1
    print("Infra ratchet gate passed: CI and docs enforce one-infrastructure-at-a-time hardening.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
