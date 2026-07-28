from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from foundry_lite.application.foundry import FoundryLite
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


def test_mvp_replay_clone_relocates_database_and_manifest_file_uris(tmp_path: Path) -> None:
    storage_root = _demo_storage_root(tmp_path)
    clone_root = tmp_path / "clone"

    gate._clone_local_store_for_replay(storage_root, clone_root)

    with sqlite3.connect(clone_root / "foundry-lite.db") as conn:
        manifest_uris = [row[0] for row in conn.execute("SELECT manifest_uri FROM dataset_versions")]
        file_uris = [row[0] for row in conn.execute("SELECT uri FROM dataset_files")]
    assert manifest_uris
    assert file_uris
    assert all(Path(uri).is_relative_to(clone_root) for uri in [*manifest_uris, *file_uris])
    for manifest_uri in manifest_uris:
        manifest = json.loads(Path(manifest_uri).read_text(encoding="utf-8"))
        assert all(Path(item["uri"]).is_relative_to(clone_root) for item in manifest["files"])


def _demo_storage_root(tmp_path: Path) -> Path:
    storage_root = tmp_path / "flite"
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=storage_root))
    foundry.demo.run()
    return storage_root
