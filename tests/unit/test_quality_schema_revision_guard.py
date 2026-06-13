from __future__ import annotations

import json
from pathlib import Path

from scripts.quality import check_schema_revision_guard as gate


def _current_payload() -> dict[str, object]:
    return {
        "schema": {
            "tables": [
                {
                    "columns": [
                        {
                            "name": "id",
                            "nullable": False,
                            "primary_key": True,
                            "type": "String",
                        }
                    ],
                    "name": "tenants",
                    "unique_constraints": [],
                }
            ]
        },
        "schema_fingerprint": "fingerprint-1",
        "summary": {"column_count": 1, "table_count": 1, "table_names": ["tenants"]},
    }


def _write_revision(root: Path, payload: dict[str, object] | None = None, *, name: str = "20260611_0001") -> Path:
    revision = root / "infra" / "schema_revisions" / f"{name}.json"
    revision.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "description": "test schema",
        "revision": name,
        **(payload or _current_payload()),
    }
    revision.write_text(json.dumps(body, sort_keys=True), encoding="utf-8")
    return revision


def test_schema_revision_guard_passes_matching_snapshot(tmp_path: Path) -> None:
    current = _current_payload()
    revision = _write_revision(tmp_path, current)

    findings = gate.collect_findings(revision_dir=revision.parent, current_revision=current)

    assert findings == []


def test_schema_revision_guard_flags_missing_revision_dir(tmp_path: Path) -> None:
    findings = gate.collect_findings(revision_dir=tmp_path / "missing", current_revision=_current_payload())

    assert [finding.code for finding in findings] == ["schema_revision_dir_missing"]


def test_schema_revision_guard_flags_fingerprint_mismatch(tmp_path: Path) -> None:
    current = _current_payload()
    revision_payload = {**current, "schema_fingerprint": "old-fingerprint"}
    revision = _write_revision(tmp_path, revision_payload)

    findings = gate.collect_findings(revision_dir=revision.parent, current_revision=current)

    assert any(finding.code == "schema_revision_fingerprint_mismatch" for finding in findings)


def test_schema_revision_guard_flags_schema_snapshot_mismatch(tmp_path: Path) -> None:
    current = _current_payload()
    revision_payload = {
        **current,
        "schema": {
            "tables": [
                {
                    "columns": [],
                    "name": "tenants",
                    "unique_constraints": [],
                }
            ]
        },
    }
    revision = _write_revision(tmp_path, revision_payload)

    findings = gate.collect_findings(revision_dir=revision.parent, current_revision=current)

    assert any(finding.code == "schema_revision_snapshot_mismatch" for finding in findings)


def test_schema_revision_guard_flags_revision_id_mismatch(tmp_path: Path) -> None:
    current = _current_payload()
    revision = _write_revision(tmp_path, {**current, "revision": "wrong"}, name="20260611_0001")

    findings = gate.collect_findings(revision_dir=revision.parent, current_revision=current)

    assert [finding.code for finding in findings] == ["schema_revision_id_mismatch"]


def test_schema_revision_guard_writes_json_report(tmp_path: Path) -> None:
    current = _current_payload()
    revision = _write_revision(tmp_path, current)
    findings = [
        gate.SchemaRevisionFinding(
            code="schema_revision_fingerprint_mismatch",
            message="mismatch",
            revision_path="infra/schema_revisions/20260611_0001.json",
        )
    ]
    output = tmp_path / "artifacts" / "quality" / "schema_revision_guard.json"

    gate.write_report(output, findings, revision_dir=revision.parent, current_revision=current)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["count"] == 1
    assert report["gate_pass"] is False
    assert report["current_schema_fingerprint"] == "fingerprint-1"


def test_schema_revision_guard_builds_real_metadata_snapshot() -> None:
    current = gate.build_current_revision_payload()

    assert current["schema_fingerprint"]
    assert current["summary"]["table_count"] > 0
    assert "datasets" in current["summary"]["table_names"]
