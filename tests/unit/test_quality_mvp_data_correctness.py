from __future__ import annotations

import sqlite3
from pathlib import Path

from foundry_lite.application.core import FoundryLiteCore
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies

from scripts.quality import check_mvp_data_correctness as gate


def test_mvp_data_correctness_gate_passes_supply_chain_demo(tmp_path: Path) -> None:
    storage_root = _demo_storage_root(tmp_path)

    findings = gate.collect_findings(storage_root)

    assert findings == []


def test_mvp_data_correctness_gate_flags_row_count_drift(tmp_path: Path) -> None:
    storage_root = _demo_storage_root(tmp_path)
    with sqlite3.connect(storage_root / "foundry-lite.db") as conn:
        conn.execute(
            """
            UPDATE dataset_files
            SET row_count = 999
            WHERE dataset_version_id = (
              SELECT dv.id
              FROM dataset_versions dv
              JOIN datasets d ON d.id = dv.dataset_id
              WHERE d.namespace = 'clean' AND d.name = 'orders'
              ORDER BY dv.version_number DESC
              LIMIT 1
            )
            """
        )

    findings = gate.collect_findings(storage_root)

    assert [finding.code for finding in findings] == ["dataset_row_count_mismatch"]
    assert findings[0].subject == "clean.orders"


def test_mvp_data_correctness_gate_writes_report(tmp_path: Path) -> None:
    storage_root = _demo_storage_root(tmp_path)
    output = tmp_path / "quality" / "mvp_data_correctness.json"

    findings = gate.collect_findings(storage_root)
    gate.write_report(output, findings, storage_root=storage_root, tenant_id="tenant-demo")

    report = output.read_text(encoding="utf-8")
    assert '"gate": "mvp_data_correctness"' in report
    assert '"gatePass": true' in report


def _demo_storage_root(tmp_path: Path) -> Path:
    storage_root = tmp_path / "flite"
    core = FoundryLiteCore(dependencies=create_local_core_dependencies(storage_root=storage_root))
    core.run_supply_chain_demo()
    return storage_root
