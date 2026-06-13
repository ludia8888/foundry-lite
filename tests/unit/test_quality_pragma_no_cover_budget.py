from __future__ import annotations

from pathlib import Path

from scripts.quality import check_pragma_no_cover_budget as gate


def _write(root: Path, relative_path: str, source: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def test_pragma_no_cover_gate_flags_missing_reason(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "libs/foundry_lite/example.py",
        "def defensive_branch(value: object) -> str:\n"
        "    if value is None:  # pragma: no cover\n"
        "        return 'impossible'\n"
        "    return 'ok'\n",
    )

    findings = gate.collect_findings((tmp_path / "libs",))

    assert [(finding.path, finding.line, finding.violation) for finding in findings] == [
        (str(path.relative_to(gate.ROOT)) if gate.ROOT in path.parents else str(path), 2, "missing_reason")
    ]


def test_pragma_no_cover_gate_accepts_reason_within_baseline(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "libs/foundry_lite/example.py",
        "def glue() -> None:\n    raise SystemExit  # pragma: no cover  # reason: CLI process exit glue\n",
    )

    findings = gate.collect_findings((tmp_path / "libs",))

    assert len(findings) == 1
    assert findings[0].violation == ""
    assert findings[0].reason == "CLI process exit glue"


def test_pragma_no_cover_gate_ignores_string_literals(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "scripts/example.py",
        "MESSAGE = '# pragma: no cover is only text here'\n",
    )

    assert gate.collect_findings((tmp_path / "scripts",)) == []


def test_pragma_no_cover_gate_writes_failing_report_when_budget_exceeded(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "libs/foundry_lite/example.py",
        "def glue() -> None:\n    raise SystemExit  # pragma: no cover  # reason: CLI process exit glue\n",
    )
    report = tmp_path / "quality" / "pragma_no_cover_budget.json"
    findings = gate.collect_findings((tmp_path / "libs",))

    gate.write_report(report, findings, baseline=0)

    text = report.read_text(encoding="utf-8")
    assert '"gate_pass": false' in text
    assert '"budget_delta": 1' in text
