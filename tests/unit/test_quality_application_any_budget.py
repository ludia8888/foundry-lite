from __future__ import annotations

import json
from pathlib import Path

from scripts.quality import check_application_any_budget as gate


def _write(path: Path, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source.strip() + "\n", encoding="utf-8")
    return path


def test_application_any_budget_counts_imports_names_and_attributes(tmp_path: Path) -> None:
    _write(
        tmp_path / "app.py",
        """
import typing
from typing import Any

def passthrough(value: Any) -> typing.Any:
    return value
""",
    )

    records = gate.collect_records((tmp_path,))

    assert [record.kind for record in records] == [
        "typing_any_import",
        "any_name",
        "typing_any_attribute",
    ]


def test_application_any_budget_flags_growth(tmp_path: Path) -> None:
    _write(
        tmp_path / "app.py",
        """
from typing import Any

value: Any = None
""",
    )
    records = gate.collect_records((tmp_path,))

    findings = gate.collect_findings(records, max_count=0)

    assert [finding.code for finding in findings] == ["application_any_budget_exceeded"]


def test_application_any_budget_accepts_object_boundaries(tmp_path: Path) -> None:
    _write(
        tmp_path / "app.py",
        """
def passthrough(value: object) -> object:
    return value
""",
    )
    records = gate.collect_records((tmp_path,))

    assert records == []
    assert gate.collect_findings(records, max_count=0) == []


def test_application_any_budget_writes_json_report(tmp_path: Path) -> None:
    _write(
        tmp_path / "app.py",
        """
from typing import Any

value: Any = None
""",
    )
    records = gate.collect_records((tmp_path,))
    findings = gate.collect_findings(records, max_count=0)
    output = tmp_path / "application_any_budget.json"

    gate.write_report(output, records, findings, max_count=0)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["count"] == 1
    assert report["baseline"]["max_count"] == 0
    assert report["summary"]["total"] == 2
    assert report["gate_pass"] is False


def test_default_application_any_budget_is_zero() -> None:
    records = gate.collect_records()

    assert records == []
    assert gate.collect_findings(records) == []
