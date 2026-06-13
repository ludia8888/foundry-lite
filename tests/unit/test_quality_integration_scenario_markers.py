from __future__ import annotations

from pathlib import Path

from scripts.quality import check_integration_scenario_markers as gate


def _write_test_file(path: Path, scenarios: list[str]) -> None:
    lines = ["import pytest", ""]
    for index, scenario in enumerate(scenarios):
        lines.extend(
            [
                f'@pytest.mark.integration_scenario("{scenario}")',
                f"def test_release_scenario_{index}():",
                "    assert True",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def test_integration_scenario_marker_gate_passes_when_all_required_scenarios_are_marked(tmp_path: Path) -> None:
    test_file = tmp_path / "test_release_scenarios.py"
    _write_test_file(test_file, list(gate.REQUIRED_SCENARIOS))

    findings = gate.collect_findings(test_dirs=(tmp_path,), root=tmp_path)

    assert findings == []


def test_integration_scenario_marker_gate_flags_missing_required_scenario(tmp_path: Path) -> None:
    test_file = tmp_path / "test_release_scenarios.py"
    scenarios = [scenario for scenario in gate.REQUIRED_SCENARIOS if scenario != "object_action_audit"]
    _write_test_file(test_file, scenarios)

    findings = gate.collect_findings(test_dirs=(tmp_path,), root=tmp_path)

    assert [finding.code for finding in findings] == ["integration_scenario_missing"]
    assert findings[0].scenario == "object_action_audit"


def test_integration_scenario_marker_gate_flags_unknown_scenario(tmp_path: Path) -> None:
    test_file = tmp_path / "test_release_scenarios.py"
    _write_test_file(test_file, [*gate.REQUIRED_SCENARIOS, "surprise_scenario"])

    findings = gate.collect_findings(test_dirs=(tmp_path,), root=tmp_path)

    assert [finding.code for finding in findings] == ["integration_scenario_unknown"]
    assert findings[0].scenario == "surprise_scenario"


def test_integration_scenario_marker_gate_flags_invalid_marker_shape(tmp_path: Path) -> None:
    test_file = tmp_path / "test_release_scenarios.py"
    test_file.write_text(
        "\n".join(
            [
                "import pytest",
                "",
                "@pytest.mark.integration_scenario()",
                "def test_bad_marker():",
                "    assert True",
            ]
        ),
        encoding="utf-8",
    )

    findings = gate.collect_findings(test_dirs=(tmp_path,), root=tmp_path)

    assert findings[0].code == "integration_scenario_marker_invalid"


def test_integration_scenario_marker_gate_writes_json_report(tmp_path: Path) -> None:
    test_file = tmp_path / "test_release_scenarios.py"
    report = tmp_path / "quality" / "integration_scenario_markers.json"
    _write_test_file(test_file, list(gate.REQUIRED_SCENARIOS))

    findings = gate.collect_findings(test_dirs=(tmp_path,), root=tmp_path)
    gate.write_report(report, findings, test_dirs=(tmp_path,), root=tmp_path)

    text = report.read_text(encoding="utf-8")
    assert '"gate": "integration_scenario_markers"' in text
    assert '"present_required_count": 7' in text
    assert '"gate_pass": true' in text
