from __future__ import annotations

import json
from pathlib import Path

from scripts.quality import check_dict_any_budget as gate


def _write(path: Path, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source.strip() + "\n", encoding="utf-8")
    return path


def test_dict_any_budget_counts_nested_signature_usage(tmp_path: Path) -> None:
    _write(
        tmp_path / "libs" / "foundry_lite" / "application" / "service.py",
        """
from typing import Any

def read_rows() -> list[dict[str, Any]]:
    return []
""",
    )

    metrics = gate.collect_metrics((tmp_path,))

    assert metrics.total == 1
    assert metrics.records[0].qualname == "read_rows"


def test_dict_any_budget_flags_total_growth(tmp_path: Path) -> None:
    _write(
        tmp_path / "libs" / "foundry_lite" / "application" / "service.py",
        """
from typing import Any

def passthrough(row: dict[str, Any]) -> dict[str, Any]:
    return row
""",
    )
    metrics = gate.collect_metrics((tmp_path,))

    findings = gate.collect_findings(metrics, total_baseline=1, category_baseline={"application_helpers": 2})

    assert [finding.code for finding in findings] == ["dict_any_total_budget_exceeded"]


def test_dict_any_budget_flags_category_growth(tmp_path: Path) -> None:
    _write(
        tmp_path / "libs" / "foundry_lite" / "application" / "services" / "service.py",
        """
from typing import Any

def passthrough(row: dict[str, Any]) -> dict[str, Any]:
    return row
""",
    )
    metrics = gate.collect_metrics((tmp_path,))

    findings = gate.collect_findings(metrics, total_baseline=2, category_baseline={"services": 1})

    assert [finding.code for finding in findings] == ["dict_any_category_budget_exceeded"]


def test_dict_any_budget_accepts_usage_within_baseline(tmp_path: Path) -> None:
    _write(
        tmp_path / "libs" / "foundry_lite" / "application" / "ports" / "port.py",
        """
from typing import Any

class Port:
    def load(self) -> dict[str, Any]:
        return {}
""",
    )
    metrics = gate.collect_metrics((tmp_path,))

    assert gate.collect_findings(metrics, total_baseline=1, category_baseline={"ports": 1}) == []


def test_dict_any_budget_writes_json_report(tmp_path: Path) -> None:
    _write(
        tmp_path / "libs" / "foundry_lite" / "application" / "service.py",
        """
from typing import Any

def passthrough(row: dict[str, Any]) -> dict[str, Any]:
    return row
""",
    )
    metrics = gate.collect_metrics((tmp_path,))
    findings = gate.collect_findings(metrics, total_baseline=1, category_baseline={"application_helpers": 2})
    output = tmp_path / "dict_any_budget.json"

    gate.write_report(
        output,
        metrics,
        findings,
        total_baseline=1,
        category_baseline={"application_helpers": 2},
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["count"] == 1
    assert report["baseline"]["total"] == 1
    assert report["summary"]["total"] == 2
    assert report["gate_pass"] is False


def test_default_baseline_matches_current_application_budget() -> None:
    metrics = gate.collect_metrics()

    assert metrics.total == gate.DEFAULT_TOTAL_BASELINE
    for category, baseline in gate.DEFAULT_CATEGORY_BASELINE.items():
        assert metrics.by_category.get(category, 0) == baseline
