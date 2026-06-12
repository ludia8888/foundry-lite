from __future__ import annotations

from pathlib import Path

from scripts.quality import check_doc_drift as gate


def _write_doc(root: Path, text: str) -> Path:
    path = root / "docs" / "current.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_python(root: Path, relative_path: str, text: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_doc_drift_flags_missing_script_reference(tmp_path: Path) -> None:
    doc = _write_doc(tmp_path, "Release gate is `scripts/quality/check_missing_gate.py`.\n")
    _write_python(tmp_path, "libs/example.py", "class ExistingService:\n    pass\n")

    findings = gate.collect_findings(docs=(doc,), code_roots=(tmp_path / "libs",), root=tmp_path)

    assert findings == [
        gate.DocDriftFinding(
            code="doc_reference_missing_path",
            path="docs/current.md",
            line=1,
            reference="scripts/quality/check_missing_gate.py",
            message="Document references a source path/script that does not exist in the current tree",
        )
    ]


def test_doc_drift_flags_missing_current_python_symbol(tmp_path: Path) -> None:
    doc = _write_doc(tmp_path, "`MissingService` is now part of the application layer.\n")
    _write_python(tmp_path, "libs/example.py", "class ExistingService:\n    pass\n")

    findings = gate.collect_findings(docs=(doc,), code_roots=(tmp_path / "libs",), root=tmp_path)

    assert findings == [
        gate.DocDriftFinding(
            code="doc_reference_missing_symbol",
            path="docs/current.md",
            line=1,
            reference="MissingService",
            message="Document references a Python symbol that does not exist in the current tree",
        )
    ]


def test_doc_drift_accepts_existing_path_symbol_and_method(tmp_path: Path) -> None:
    doc = _write_doc(
        tmp_path,
        "\n".join(
            [
                "`libs/example.py` exists.",
                "`ExistingService` is current.",
                "`ExistingService.run` is current.",
                "`check_existing_gate.py` is wired.",
            ]
        ),
    )
    _write_python(
        tmp_path,
        "libs/example.py",
        "class ExistingService:\n    def run(self) -> None:\n        pass\n",
    )
    _write_python(tmp_path, "scripts/quality/check_existing_gate.py", "def main() -> int:\n    return 0\n")

    assert gate.collect_findings(docs=(doc,), code_roots=(tmp_path / "libs", tmp_path / "scripts"), root=tmp_path) == []


def test_doc_drift_skips_future_or_negative_gap_wording(tmp_path: Path) -> None:
    doc = _write_doc(
        tmp_path,
        "`WorkflowAdapter` remains unextracted, and `FoundryLiteCore.__getattr__` has been removed.\n",
    )
    _write_python(tmp_path, "libs/core.py", "class FoundryLiteCore:\n    pass\n")

    assert gate.collect_findings(docs=(doc,), code_roots=(tmp_path / "libs",), root=tmp_path) == []


def test_doc_drift_writes_json_report(tmp_path: Path) -> None:
    doc = _write_doc(tmp_path, "`MissingService` is now implemented.\n")
    output = tmp_path / "artifacts" / "quality" / "doc_drift.json"
    findings = gate.collect_findings(docs=(doc,), code_roots=(tmp_path / "libs",), root=tmp_path)

    gate.write_report(output, findings, docs=(doc,))

    report = output.read_text(encoding="utf-8")
    assert '"gate_pass": false' in report
    assert '"doc_reference_missing_symbol"' in report
