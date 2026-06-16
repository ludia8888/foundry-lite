from __future__ import annotations

from pathlib import Path

from scripts.quality import check_infra_ratchet as gate


def test_infra_ratchet_gate_passes_current_docs_and_ci() -> None:
    assert gate.collect_findings() == []


def test_infra_ratchet_gate_flags_missing_proof_class(tmp_path: Path) -> None:
    _write_minimum_ratchet_tree(tmp_path)
    doc_path = tmp_path / "docs" / "infra-ratchet.md"
    doc_path.write_text(
        doc_path.read_text(encoding="utf-8").replace("partial-success", "partial success"),
        encoding="utf-8",
    )

    findings = gate.collect_findings(tmp_path)

    assert any(finding.code == "missing_proof_class" and "partial-success" in finding.message for finding in findings)


def test_infra_ratchet_gate_writes_report(tmp_path: Path) -> None:
    output = tmp_path / "infra_ratchet.json"

    gate.write_report(output, [])

    assert '"gate": "infra_ratchet"' in output.read_text(encoding="utf-8")


def _write_minimum_ratchet_tree(root: Path) -> None:
    docs = root / "docs"
    scripts = root / "scripts"
    quality = scripts / "quality"
    s3_tests = root / "tests" / "integration"
    docs.mkdir(parents=True)
    quality.mkdir(parents=True)
    s3_tests.mkdir(parents=True)
    (docs / "infra-ratchet.md").write_text(_minimum_ratchet_doc(), encoding="utf-8")
    (docs / "commit-point-risk-register.md").write_text(
        "Production storage split-brain extensions\ntest_partial_multipart_upload_never_becomes_committed_version\n",
        encoding="utf-8",
    )
    (docs / "foundry_lite_tricky_failure_modes_checklist.md").write_text(
        "T0-011\ntest_s3_partial_multipart_upload_never_becomes_committed_version\n",
        encoding="utf-8",
    )
    (docs / "implementation-status.md").write_text(
        "Infra Ratchet\nS3DatasetStorageAdapter\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("Infra Ratchet\ncheck_infra_ratchet.py\n", encoding="utf-8")
    (root / "package.json").write_text(
        '{"scripts":{"quality:infra-ratchet":"uv run python scripts/quality/check_infra_ratchet.py",'
        '"quality:s3-storage":"uv run pytest tests/integration/test_s3_dataset_storage_adapter.py -q",'
        '"quality:static":"pnpm quality:infra-ratchet && pnpm quality:s3-storage"}}',
        encoding="utf-8",
    )
    (scripts / "ci_gate.sh").write_text(
        "scripts/quality/check_infra_ratchet.py\npnpm --silent quality:s3-storage\n",
        encoding="utf-8",
    )
    (s3_tests / "test_s3_dataset_storage_adapter.py").write_text(_minimum_s3_tests(), encoding="utf-8")


def _minimum_ratchet_doc() -> str:
    required_terms = [
        "One Infrastructure At A Time",
        "Active Ratchet Queue",
        "Pull Request Exit Checklist",
        "MinIO/Testcontainers before AWS S3",
        "MinIO/S3 DatasetStorageAdapter",
        "Iceberg Catalog/TableAdapter",
        *sorted(gate.REQUIRED_PROOF_CLASSES),
        *gate.REQUIRED_S3_TEST_NAMES,
    ]
    return "\n".join(required_terms)


def _minimum_s3_tests() -> str:
    return "\n".join(f"def {name}(): pass" for name in gate.REQUIRED_S3_TEST_NAMES)
