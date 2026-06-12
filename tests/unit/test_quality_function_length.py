from __future__ import annotations

import json
from pathlib import Path

from scripts.quality import check_function_length as gate


def _write(path: Path, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source.strip() + "\n", encoding="utf-8")
    return path


def test_function_length_gate_flags_new_over_limit_function(tmp_path: Path) -> None:
    _write(
        tmp_path / "app.py",
        """
def too_big() -> None:
    one = 1
    two = 2
    three = 3
""",
    )

    findings = gate.collect_findings((tmp_path,), baseline={}, max_lines=3)

    assert [finding.code for finding in findings] == ["function_over_limit"]


def test_function_length_gate_accepts_baseline_function_at_same_length(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "service.py",
        """
class Service:
    def existing_long(self) -> None:
        one = 1
        two = 2
        three = 3
""",
    )
    record = gate.collect_functions((path,))[0]

    findings = gate.collect_findings((path,), baseline={gate.function_key(record): record.length}, max_lines=3)

    assert findings == []


def test_function_length_gate_flags_baseline_function_growth(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "service.py",
        """
class Service:
    def existing_long(self) -> None:
        one = 1
        two = 2
        three = 3
        four = 4
""",
    )
    record = gate.collect_functions((path,))[0]

    findings = gate.collect_findings((path,), baseline={gate.function_key(record): 4}, max_lines=3)

    assert [finding.code for finding in findings] == ["baseline_function_grew"]


def test_function_length_gate_keeps_warning_count_out_of_failures(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "ok.py",
        """
def warning_only() -> None:
    one = 1
    two = 2
    three = 3
""",
    )
    records = gate.collect_functions((path,))
    output = tmp_path / "function_length.json"

    gate.write_report(
        output,
        records,
        [],
        baseline={},
        max_lines=10,
        warning_lines=2,
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["summary"]["warning_count"] == 1
    assert report["gate_pass"] is True


def test_function_length_gate_writes_json_report(tmp_path: Path) -> None:
    _write(
        tmp_path / "bad.py",
        """
def too_big() -> None:
    one = 1
    two = 2
    three = 3
""",
    )
    records = gate.collect_functions((tmp_path,))
    findings = gate.collect_findings((tmp_path,), baseline={}, max_lines=3)
    output = tmp_path / "function_length.json"

    gate.write_report(output, records, findings, baseline={}, max_lines=3, warning_lines=2)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["count"] == 1
    assert report["baseline"]["max_lines"] == 3
    assert report["gate_pass"] is False
    assert report["violations"][0]["code"] == "function_over_limit"
