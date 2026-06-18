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
    integration_tests = root / "tests" / "integration"
    docs.mkdir(parents=True)
    quality.mkdir(parents=True)
    integration_tests.mkdir(parents=True)
    (docs / "infra-ratchet.md").write_text(_minimum_ratchet_doc(), encoding="utf-8")
    (docs / "commit-point-risk-register.md").write_text(
        "Production storage split-brain extensions\ntest_partial_multipart_upload_never_becomes_committed_version\n",
        encoding="utf-8",
    )
    (docs / "foundry_lite_tricky_failure_modes_checklist.md").write_text(
        "T0-011\ntest_s3_partial_multipart_upload_never_becomes_committed_version\n"
        "test_iceberg_s3_storage_with_spark_compute_end_to_end\n"
        "quality:checklist-evidence\n"
        "quality:infra-tricky-matrix\n"
        + "\n".join(
            [
                *gate.REQUIRED_S3_TEST_NAMES,
                *gate.REQUIRED_ICEBERG_TEST_NAMES,
                *gate.REQUIRED_SPARK_TEST_NAMES,
                *gate.REQUIRED_COMPOSITION_TEST_NAMES,
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (docs / "implementation-status.md").write_text(
        "Infra Ratchet\nS3DatasetStorageAdapter\nquality:infra-composition\n"
        "quality:checklist-evidence\nquality:infra-tricky-matrix\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        "Infra Ratchet\ncheck_infra_ratchet.py\ncheck_infra_tricky_matrix.py\n",
        encoding="utf-8",
    )
    (root / "package.json").write_text(
        '{"scripts":{"quality:infra-ratchet":"uv run python scripts/quality/check_infra_ratchet.py",'
        '"quality:checklist-evidence":"uv run python scripts/quality/check_checklist_evidence.py",'
        '"quality:infra-tricky-matrix":"uv run python scripts/quality/check_infra_tricky_matrix.py",'
        '"quality:s3-storage":"uv run pytest tests/integration/test_s3_dataset_storage_adapter.py -q",'
        '"quality:iceberg":"uv run pytest tests/integration/test_iceberg_dataset_storage_adapter.py -q",'
        '"quality:spark":"uv run pytest tests/integration/test_spark_compute_adapter.py -q",'
        '"quality:infra-composition":"uv run pytest tests/integration/test_infra_composition_ratchet.py -q",'
        '"quality:static":"pnpm quality:checklist-evidence && pnpm quality:infra-tricky-matrix && '
        'pnpm quality:infra-ratchet && pnpm quality:s3-storage"}}',
        encoding="utf-8",
    )
    (scripts / "ci_gate.sh").write_text(
        "scripts/quality/check_infra_ratchet.py\n"
        "scripts/quality/check_checklist_evidence.py\n"
        "scripts/quality/check_infra_tricky_matrix.py\n"
        "pnpm --silent quality:cdc-stream-archive\n"
        "pnpm --silent quality:cdc-object-indexing\n"
        "pnpm --silent quality:cdc-live-debezium\n"
        "pnpm --silent quality:s3-storage\n"
        "pnpm --silent quality:iceberg\n"
        "pnpm --silent quality:spark\n"
        "pnpm --silent quality:infra-composition\n",
        encoding="utf-8",
    )
    (integration_tests / "test_s3_dataset_storage_adapter.py").write_text(_minimum_s3_tests(), encoding="utf-8")
    (integration_tests / "test_iceberg_dataset_storage_adapter.py").write_text(
        _minimum_iceberg_tests(),
        encoding="utf-8",
    )
    (integration_tests / "test_spark_compute_adapter.py").write_text(_minimum_spark_tests(), encoding="utf-8")
    (integration_tests / "test_infra_composition_ratchet.py").write_text(
        _minimum_composition_tests(),
        encoding="utf-8",
    )


def _minimum_ratchet_doc() -> str:
    required_terms = [
        "One Infrastructure At A Time",
        "Active Ratchet Queue",
        "Pull Request Exit Checklist",
        "MinIO/Testcontainers before AWS S3",
        "Infra Tricky Matrix",
        "MinIO/S3 DatasetStorageAdapter",
        "Iceberg Catalog/TableAdapter",
        "Spark ComputeAdapter",
        "Self And Composition Tests",
        *sorted(gate.REQUIRED_PROOF_CLASSES),
        *gate.REQUIRED_S3_TEST_NAMES,
        *gate.REQUIRED_ICEBERG_TEST_NAMES,
        *gate.REQUIRED_SPARK_TEST_NAMES,
        *gate.REQUIRED_COMPOSITION_TEST_NAMES,
    ]
    return "\n".join(required_terms)


def _minimum_s3_tests() -> str:
    return "\n".join(f"def {name}(): pass" for name in gate.REQUIRED_S3_TEST_NAMES)


def _minimum_iceberg_tests() -> str:
    return "\n".join(f"def {name}(): pass" for name in gate.REQUIRED_ICEBERG_TEST_NAMES)


def _minimum_spark_tests() -> str:
    return "\n".join(f"def {name}(): pass" for name in gate.REQUIRED_SPARK_TEST_NAMES)


def _minimum_composition_tests() -> str:
    return "\n".join(f"def {name}(): pass" for name in gate.REQUIRED_COMPOSITION_TEST_NAMES)
