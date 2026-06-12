from __future__ import annotations

import json
from pathlib import Path

from scripts.quality import check_pr_root_cause_section as gate

VALID_BODY = """## Root Cause
The service wrote audit rows from a read path.

## Impact
Denied read attempts changed durable audit state.

## Regression Test
Added a test that denies read permission and asserts audit count is unchanged.
"""


def test_pr_root_cause_gate_accepts_required_sections() -> None:
    pr_body = gate.PrBody(body=VALID_BODY, source="unit", is_required=True)

    assert gate.collect_findings(pr_body) == []


def test_pr_root_cause_gate_flags_missing_sections() -> None:
    pr_body = gate.PrBody(body="## Summary\nSmall fix.\n", source="unit", is_required=True)

    findings = gate.collect_findings(pr_body)

    assert findings == [
        gate.PrRootCauseFinding(
            code="pr_missing_root_cause_section",
            section="root_cause",
            message="PR description must include Root Cause, Impact, and Regression Test sections",
        ),
        gate.PrRootCauseFinding(
            code="pr_missing_root_cause_section",
            section="impact",
            message="PR description must include Root Cause, Impact, and Regression Test sections",
        ),
        gate.PrRootCauseFinding(
            code="pr_missing_root_cause_section",
            section="regression_test",
            message="PR description must include Root Cause, Impact, and Regression Test sections",
        ),
    ]


def test_pr_root_cause_gate_loads_github_event_body(tmp_path: Path) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({"pull_request": {"body": VALID_BODY}}), encoding="utf-8")

    pr_body = gate.load_pr_body(event_path=event_path)

    assert pr_body.is_required is True
    assert gate.collect_findings(pr_body) == []


def test_pr_root_cause_gate_skips_non_pr_event(tmp_path: Path) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({"ref": "refs/heads/main"}), encoding="utf-8")

    pr_body = gate.load_pr_body(event_path=event_path)

    assert pr_body.is_required is False
    assert gate.collect_findings(pr_body) == []


def test_pr_root_cause_gate_writes_json_report(tmp_path: Path) -> None:
    output = tmp_path / "quality" / "pr_root_cause_section.json"
    pr_body = gate.PrBody(body="## Summary\nSmall fix.\n", source="unit", is_required=True)
    findings = gate.collect_findings(pr_body)

    gate.write_report(output, pr_body, findings)

    report = output.read_text(encoding="utf-8")
    assert '"gate_pass": false' in report
    assert '"pr_missing_root_cause_section"' in report
