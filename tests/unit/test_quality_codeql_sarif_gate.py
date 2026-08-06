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


def _suppressed_result(rule_id: str) -> dict:
    """CodeQL emits `suppressions` only for a result the source suppressed inline."""
    return {**_result(rule_id), "suppressions": [{"kind": "inSource", "justification": "reviewed"}]}


def test_a_suppressed_finding_does_not_block(tmp_path: Path) -> None:
    """A gate with no way to record a reviewed false positive gets fixed or gets ignored.

    This one was ignored: it stayed red for three days across every push, because the only way
    to clear the board was to change code that was already correct.
    """
    sarif = _write_sarif(tmp_path / "results.sarif", [_suppressed_result("py/clear-text-logging")])

    assert fail_on_sarif_findings.main([str(sarif)]) == 0


def test_a_suppressed_finding_is_still_printed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """An allowance that disappears from the output is an allowance nobody revisits."""
    sarif = _write_sarif(tmp_path / "results.sarif", [_suppressed_result("py/clear-text-logging")])

    fail_on_sarif_findings.main([str(sarif)])

    output = capsys.readouterr().out
    assert "suppressed in source: 1" in output
    assert "py/clear-text-logging" in output


def test_an_unsuppressed_finding_still_blocks_beside_a_suppressed_one(tmp_path: Path) -> None:
    """Suppressing one result must not excuse the next; they are partitioned, not merged."""
    sarif = _write_sarif(
        tmp_path / "results.sarif",
        [_suppressed_result("py/clear-text-logging"), _result("foundry-lite/raw-json-flows-to-service")],
    )

    assert fail_on_sarif_findings.main([str(sarif)]) == 1


def test_partitioning_reports_both_sides(tmp_path: Path) -> None:
    sarif = _write_sarif(tmp_path / "results.sarif", [_suppressed_result("a/rule"), _result("b/rule")])

    blocking, suppressed = fail_on_sarif_findings.partitioned_findings(sarif)

    assert [finding.rule_id for finding in blocking] == ["b/rule"]
    assert [finding.rule_id for finding in suppressed] == ["a/rule"]


def test_a_reviewed_false_positive_is_allowed_without_an_inline_comment(tmp_path: Path) -> None:
    """`codeql database analyze` never writes `suppressions`; GitHub applies them when rendering.

    Verified against the artifact from run 31087775875: all three results carried no
    `suppressions` field, so an inline `# codeql[rule]` comment could not have worked. The
    allowlist is the only mechanism that reaches this gate.
    """
    (rule_id, path), _reason = next(iter(fail_on_sarif_findings.REVIEWED_FALSE_POSITIVES.items()))
    result = _result(rule_id)
    result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] = path
    sarif = _write_sarif(tmp_path / "results.sarif", [result])

    assert fail_on_sarif_findings.main([str(sarif)]) == 0


def test_the_allowance_is_scoped_to_one_path(tmp_path: Path) -> None:
    """A rule-wide allowance would hide the next occurrence somewhere nobody reviewed."""
    (rule_id, _path), _reason = next(iter(fail_on_sarif_findings.REVIEWED_FALSE_POSITIVES.items()))
    result = _result(rule_id)
    result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] = "apps/api/somewhere_else.py"
    sarif = _write_sarif(tmp_path / "results.sarif", [result])

    assert fail_on_sarif_findings.main([str(sarif)]) == 1


def test_every_allowance_carries_a_reason() -> None:
    """An entry without one is inherited rather than re-judged."""
    for key, reason in fail_on_sarif_findings.REVIEWED_FALSE_POSITIVES.items():
        assert len(reason) > 80, key
