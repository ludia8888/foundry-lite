from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.quality.codeql import fail_on_sarif_findings


def _write_sarif(path: Path, results: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": "2.1.0",
                "runs": [
                    {
                        "tool": {"driver": {"name": "CodeQL"}},
                        "results": results,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _result(rule_id: str) -> dict:
    return {
        "ruleId": rule_id,
        "message": {"text": "unsafe flow"},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": "apps/api/main.py"},
                    "region": {"startLine": 12},
                }
            }
        ],
    }


def test_codeql_sarif_gate_passes_empty_results(tmp_path: Path) -> None:
    sarif = _write_sarif(tmp_path / "results.sarif", [])

    assert fail_on_sarif_findings.collect_findings(sarif) == []
    assert fail_on_sarif_findings.main([str(sarif)]) == 0


def test_codeql_sarif_gate_fails_when_output_directory_has_no_sarif(tmp_path: Path) -> None:
    empty_dir = tmp_path / "codeql-results"
    empty_dir.mkdir()

    with pytest.raises(FileNotFoundError):
        fail_on_sarif_findings.collect_findings(empty_dir)


def test_codeql_sarif_gate_fails_on_any_finding(tmp_path: Path) -> None:
    sarif = _write_sarif(tmp_path / "results.sarif", [_result("foundry-lite/header-flows-to-sql")])

    findings = fail_on_sarif_findings.collect_findings(sarif)

    assert [(finding.path, finding.line, finding.rule_id) for finding in findings] == [
        ("apps/api/main.py", 12, "foundry-lite/header-flows-to-sql")
    ]
    assert fail_on_sarif_findings.main([str(sarif)]) == 1


def test_codeql_sarif_gate_can_scope_to_custom_rules(tmp_path: Path) -> None:
    sarif = _write_sarif(
        tmp_path / "results.sarif",
        [_result("py/unused-import"), _result("foundry-lite/raw-json-flows-to-service")],
    )

    findings = fail_on_sarif_findings.collect_findings(sarif, rule_id_prefix="foundry-lite/")

    assert [finding.rule_id for finding in findings] == ["foundry-lite/raw-json-flows-to-service"]
