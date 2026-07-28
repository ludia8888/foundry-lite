from __future__ import annotations

import json
from pathlib import Path

from scripts.quality import check_pipeline_quality_gate_inventory as gate


def test_pipeline_quality_gate_inventory_passes_current_repo() -> None:
    assert gate.collect_findings() == []


def test_pipeline_quality_gate_inventory_rejects_placeholder_command(tmp_path: Path) -> None:
    spec = gate.PipelineGateSpec(
        name="quality:pipeline-example",
        lane="static",
        evidence_paths=("tests/unit/test_pipeline_example.py",),
    )
    _write_fixture(tmp_path, spec, command="true")

    findings = gate.collect_findings(tmp_path, specs=(spec,))

    assert any(finding.code == "placeholder_package_script" for finding in findings)
    assert any(finding.code == "evidence_not_executed" for finding in findings)


def test_pipeline_quality_gate_inventory_requires_lane_execution(tmp_path: Path) -> None:
    spec = gate.PipelineGateSpec(
        name="quality:pipeline-example",
        lane="static",
        evidence_paths=("tests/unit/test_pipeline_example.py",),
    )
    command = "uv run pytest tests/unit/test_pipeline_example.py -q"
    _write_fixture(tmp_path, spec, command=command, include_execution=False)

    findings = gate.collect_findings(tmp_path, specs=(spec,))

    assert any(finding.code == "static_gate_not_executed" for finding in findings)


def test_pipeline_quality_gate_inventory_writes_machine_report(tmp_path: Path) -> None:
    finding = gate.PipelineGateFinding("example", "quality:pipeline-example", "message", "reference")
    output = tmp_path / "pipeline-quality-gates.json"

    gate.write_report([finding], output_path=output)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["gate_pass"] is False
    assert report["count"] == 1
    assert len(report["required_gates"]) == 8


def _write_fixture(
    root: Path,
    spec: gate.PipelineGateSpec,
    *,
    command: str,
    include_execution: bool = True,
) -> None:
    package = {"scripts": {spec.name: command}}
    (root / "package.json").write_text(json.dumps(package), encoding="utf-8")
    evidence = root / spec.evidence_paths[0]
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.touch()
    driver = root / gate.STATIC_DRIVER
    driver.parent.mkdir(parents=True, exist_ok=True)
    inventory = f'("{spec.name}", "{spec.lane}")'
    execution = f'["pnpm", "--silent", "{spec.name}"]' if include_execution else ""
    driver.write_text(f"{inventory}\n{execution}\n", encoding="utf-8")
    e2e = root / gate.E2E_DRIVER
    e2e.parent.mkdir(parents=True, exist_ok=True)
    e2e.write_text("", encoding="utf-8")
