from __future__ import annotations

from pathlib import Path

from scripts.quality import check_checklist_evidence as gate


def test_checklist_evidence_gate_flags_checked_test_not_collected(tmp_path: Path) -> None:
    checklist = tmp_path / "docs" / "foundry_lite_tricky_failure_modes_checklist.md"
    checklist.parent.mkdir(parents=True)
    checklist.write_text(
        "- [x] **Regression Test:** `test_existing_evidence`\n"
        "- [x] **Regression Test:** `test_missing_evidence`\n"
        "- [ ] **Future Regression Test:** `test_future_evidence`\n",
        encoding="utf-8",
    )

    findings = gate.collect_findings(
        tmp_path,
        checklist_path=checklist,
        collected_test_names={"test_existing_evidence"},
    )

    assert [finding.reference for finding in findings] == ["test_missing_evidence"]
    assert findings[0].code == "checked_test_not_collected"
    assert findings[0].line == 2


def test_checklist_evidence_gate_ignores_fenced_checked_examples(tmp_path: Path) -> None:
    checklist = tmp_path / "docs" / "foundry_lite_tricky_failure_modes_checklist.md"
    checklist.parent.mkdir(parents=True)
    checklist.write_text(
        "- [x] `test_real_evidence`\n```md\n- [x] `test_example_not_real`\n```\n",
        encoding="utf-8",
    )

    result = gate.analyze_checklist_evidence(
        tmp_path,
        checklist_path=checklist,
        collected_test_names={"test_real_evidence"},
    )

    assert result.findings == []
    assert [reference.name for reference in result.checked_references] == ["test_real_evidence"]


def test_collected_test_parser_handles_methods_and_parametrized_node_ids() -> None:
    output = "\n".join(
        [
            "tests/unit/test_one.py::test_plain",
            "tests/unit/test_two.py::TestThing::test_method[case-a]",
            "tests/unit/test_three.py::not_a_test_helper",
        ]
    )

    assert gate.parse_collected_test_names(output) == {"test_plain", "test_method"}


def test_checklist_evidence_gate_writes_report(tmp_path: Path) -> None:
    checklist = tmp_path / "docs" / "foundry_lite_tricky_failure_modes_checklist.md"
    output = tmp_path / "artifacts" / "quality" / "checklist_evidence.json"
    checklist.parent.mkdir(parents=True)
    checklist.write_text("- [x] `test_existing_evidence`\n", encoding="utf-8")
    result = gate.analyze_checklist_evidence(
        tmp_path,
        checklist_path=checklist,
        collected_test_names={"test_existing_evidence"},
    )

    gate.write_report(output, result)

    report = output.read_text(encoding="utf-8")
    assert '"gate": "checklist_evidence"' in report
    assert '"gate_pass": true' in report
