from __future__ import annotations

import json
from pathlib import Path

from scripts.quality import check_evidence_ledger_commands as gate


def test_evidence_ledger_command_gate_passes_current_repo() -> None:
    assert gate.collect_findings() == []


def test_evidence_ledger_command_gate_rejects_missing_package_script(tmp_path: Path) -> None:
    _write_fixture(tmp_path, "`pnpm --silent quality:missing-proof`")

    findings = _collect(tmp_path)

    assert any(finding.code == "missing_package_script" for finding in findings)
    assert any(finding.reference == "quality:missing-proof" for finding in findings)


def test_evidence_ledger_command_gate_rejects_missing_python_script(tmp_path: Path) -> None:
    _write_fixture(tmp_path, "`uv run python scripts/quality/missing_gate.py`")

    findings = _collect(tmp_path)

    assert any(finding.code == "missing_python_script" for finding in findings)
    assert any(finding.reference == "scripts/quality/missing_gate.py" for finding in findings)


def test_evidence_ledger_command_gate_rejects_missing_test_node(tmp_path: Path) -> None:
    _write_fixture(tmp_path, "`uv run pytest tests/unit/test_sample.py::test_missing -q`")

    findings = _collect(tmp_path)

    assert any(finding.code == "missing_test_node" for finding in findings)
    assert any(finding.reference == "tests/unit/test_sample.py::test_missing" for finding in findings)


def test_evidence_ledger_command_gate_writes_json_report(tmp_path: Path) -> None:
    _write_fixture(tmp_path, "`pnpm --silent quality:doc-drift`; `uv run pytest tests/unit/test_sample.py -q`")
    output = tmp_path / "artifacts" / "quality" / "evidence_ledger_commands.json"

    findings = _collect(tmp_path)
    gate.write_report(output, findings)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "PASS"
    assert report["count"] == 0


def _collect(root: Path) -> list[gate.EvidenceCommandFinding]:
    return gate.collect_findings(
        root,
        ledger_path=root / "docs" / "sprint-evidence-ledger.md",
        package_path=root / "package.json",
    )


def _write_fixture(root: Path, command_cell: str) -> None:
    (root / "docs").mkdir(parents=True)
    (root / "tests" / "unit").mkdir(parents=True)
    (root / "scripts" / "quality").mkdir(parents=True)
    (root / "docs" / "sprint-evidence-ledger.md").write_text(
        f"| ID | Command | Evidence |\n|---|---|---|\n| `VERIFY-SAMPLE` | {command_cell} | PASS |\n",
        encoding="utf-8",
    )
    (root / "package.json").write_text(
        json.dumps({"scripts": {"quality:doc-drift": "echo ok"}}),
        encoding="utf-8",
    )
    (root / "scripts" / "quality" / "existing_gate.py").write_text("print('ok')\n", encoding="utf-8")
    (root / "tests" / "unit" / "test_sample.py").write_text(
        "def test_existing() -> None:\n    assert True\n",
        encoding="utf-8",
    )
