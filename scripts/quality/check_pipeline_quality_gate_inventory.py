"""Validate that every Pipeline Builder quality gate runs real evidence."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "pipeline_quality_gate_inventory.json"
STATIC_DRIVER = Path("scripts/quality/run_static_checks.py")
E2E_DRIVER = Path("scripts/ci_gate.sh")


@dataclass(frozen=True)
class PipelineGateSpec:
    name: str
    lane: str
    evidence_paths: tuple[str, ...]


@dataclass(frozen=True)
class PipelineGateFinding:
    code: str
    gate: str
    message: str
    reference: str


PIPELINE_GATE_SPECS = (
    PipelineGateSpec(
        "quality:pipeline-graph-v2",
        "static",
        ("tests/unit/test_pipeline_graph_v2.py", "tests/unit/test_pipeline_plan_compiler.py"),
    ),
    PipelineGateSpec(
        "quality:pipeline-artifact-execution",
        "static",
        (
            "tests/contracts/test_pipeline_execution_repository_contract.py",
            "tests/integration/test_pipeline_multi_output_execution.py",
            "tests/integration/test_pipeline_governed_output_candidates.py",
            "tests/unit/test_pipeline_plan_compiler.py",
        ),
    ),
    PipelineGateSpec(
        "quality:pipeline-preview",
        "static",
        (
            "tests/unit/test_pipeline_api_routes.py",
            "tests/contracts/test_pipeline_execution_repository_contract.py",
        ),
    ),
    PipelineGateSpec(
        "quality:pipeline-multimodal",
        "static",
        (
            "tests/integration/test_pipeline_multimodal_preview.py",
            "tests/contracts/test_media_processor_registry_contract.py",
            "tests/unit/test_pipeline_preview_processing_bounds.py",
            "tests/unit/test_pdf_ocr_processor.py",
            "tests/unit/test_asr_processor.py",
            "tests/unit/test_video_probe_processor.py",
            "tests/unit/test_video_scene_frame_processor.py",
            "tests/unit/test_media_video_vision.py",
        ),
    ),
    PipelineGateSpec(
        "quality:pipeline-scheduler",
        "static",
        (
            "tests/integration/test_pipeline_builder_graph.py",
            "tests/unit/test_pipeline_api_routes.py",
            "tests/unit/test_quality_pipeline_builder_performance.py",
        ),
    ),
    PipelineGateSpec(
        "quality:pipeline-python-isolation",
        "static",
        (
            "tests/unit/test_pipeline_python_isolation.py",
            "tests/unit/test_python_transform_runner.py",
            "tests/contracts/test_code_execution_contract.py",
        ),
    ),
    PipelineGateSpec(
        "quality:pipeline-builder-performance",
        "static",
        ("scripts/quality/check_pipeline_builder_performance.py",),
    ),
    PipelineGateSpec(
        "quality:pipeline-builder-e2e",
        "e2e",
        (
            "tests/e2e-foundry/pipeline-builder-flow.spec.ts",
            "tests/e2e-foundry/document-intelligence-flow.spec.ts",
        ),
    ),
)


def collect_findings(
    root: Path = ROOT,
    *,
    specs: tuple[PipelineGateSpec, ...] = PIPELINE_GATE_SPECS,
) -> list[PipelineGateFinding]:
    scripts = _package_scripts(root)
    static_driver = _read(root / STATIC_DRIVER)
    e2e_driver = _read(root / E2E_DRIVER)
    findings: list[PipelineGateFinding] = []
    for spec in specs:
        command = scripts.get(spec.name)
        findings.extend(_command_findings(root, spec, command))
        findings.extend(_inventory_findings(spec, static_driver, e2e_driver))
    return findings


def write_report(
    findings: list[PipelineGateFinding],
    *,
    output_path: Path = DEFAULT_OUTPUT,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "gate": "pipeline_quality_gate_inventory",
        "gate_pass": not findings,
        "count": len(findings),
        "required_gates": [spec.name for spec in PIPELINE_GATE_SPECS],
        "violations": [asdict(finding) for finding in findings],
    }
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _package_scripts(root: Path) -> dict[str, str]:
    package_path = root / "package.json"
    if not package_path.exists():
        return {}
    payload = json.loads(package_path.read_text(encoding="utf-8"))
    raw_scripts = payload.get("scripts", {}) if isinstance(payload, dict) else {}
    if not isinstance(raw_scripts, dict):
        return {}
    return {
        str(name): str(command)
        for name, command in cast(dict[object, object], raw_scripts).items()
        if isinstance(name, str) and isinstance(command, str)
    }


def _command_findings(
    root: Path,
    spec: PipelineGateSpec,
    command: str | None,
) -> list[PipelineGateFinding]:
    if command is None:
        return [_finding("missing_package_script", spec, "package.json", "Package script is required.")]
    findings: list[PipelineGateFinding] = []
    if _is_placeholder(command):
        findings.append(
            _finding(
                "placeholder_package_script",
                spec,
                command,
                "Quality gate must execute tests or a checker, not a successful placeholder.",
            )
        )
    for evidence in spec.evidence_paths:
        path = evidence.split("::", maxsplit=1)[0]
        if evidence not in command:
            findings.append(
                _finding(
                    "evidence_not_executed",
                    spec,
                    evidence,
                    "Package script does not execute the declared evidence.",
                )
            )
        if not (root / path).exists():
            findings.append(
                _finding(
                    "missing_evidence_path",
                    spec,
                    path,
                    "Declared evidence path does not exist.",
                )
            )
    return findings


def _inventory_findings(
    spec: PipelineGateSpec,
    static_driver: str,
    e2e_driver: str,
) -> list[PipelineGateFinding]:
    findings: list[PipelineGateFinding] = []
    inventory_entry = f'("{spec.name}", "{spec.lane}")'
    if inventory_entry not in static_driver:
        findings.append(
            _finding(
                "missing_inventory_entry",
                spec,
                str(STATIC_DRIVER),
                "Pipeline quality gate is absent from the typed CI lane inventory.",
            )
        )
    if spec.lane == "static":
        static_command = f'["pnpm", "--silent", "{spec.name}"]'
        if static_command not in static_driver:
            findings.append(
                _finding(
                    "static_gate_not_executed",
                    spec,
                    str(STATIC_DRIVER),
                    "Static Pipeline Builder gate is inventoried but not executed.",
                )
            )
    elif f"pnpm --silent {spec.name}" not in e2e_driver:
        findings.append(
            _finding(
                "e2e_gate_not_executed",
                spec,
                str(E2E_DRIVER),
                "Pipeline Builder browser gate is not executed by the E2E lane.",
            )
        )
    return findings


def _is_placeholder(command: str) -> bool:
    normalized = command.strip().lower()
    return normalized in {"true", ":", "exit 0"} or normalized.startswith("echo ")


def _finding(
    code: str,
    spec: PipelineGateSpec,
    reference: str,
    message: str,
) -> PipelineGateFinding:
    return PipelineGateFinding(code=code, gate=spec.name, message=message, reference=reference)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    findings = collect_findings()
    write_report(findings, output_path=args.output)
    if findings:
        for finding in findings:
            print(f"{finding.gate}: {finding.code}: {finding.reference}: {finding.message}")
        return 1
    print(f"Pipeline quality gate inventory passed: report={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
