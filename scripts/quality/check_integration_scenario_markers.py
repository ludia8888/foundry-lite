"""Enforces guideline §11.4: required integration scenarios must be marked.

The release gate cannot rely on one broad demo smoke as proof that every MVP
path is covered. This gate scans pytest tests for
`@pytest.mark.integration_scenario("<scenario>")` and requires all seven
release scenarios from the Python engineering guide to be present.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEST_DIRS = (ROOT / "tests",)
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "integration_scenario_markers.json"
MARKER_CALL = "pytest.mark.integration_scenario"
REQUIRED_SCENARIOS = {
    "connector_sync": "connector sync -> raw dataset commit",
    "transform_clean_dataset": "raw dataset -> DuckDB transform -> clean dataset commit",
    "ontology_index": "ontology import -> object index",
    "object_action_audit": "object query -> action apply -> object edit/outbox/audit",
    "materialization_downstream_transform": "action log/object snapshot materialization -> downstream transform",
    "permission_tenant_isolation": "permission denied and tenant isolation",
    "failed_run_replay_or_dlq": "failed run -> retry/replay or DLQ",
}
ADDITIONAL_SCENARIOS = {
    "closed_loop_repeatability": "one-command closed-loop demo repeated twice from the same checkout",
    "media-ocr": "real Tesseract OCR live: image -> ocr_v1 -> index -> search + FAILED run operator-evidence",
    "media-asr": "real faster-whisper ASR live: audio -> asr_v1 -> index -> search + FAILED run operator-evidence",
    "media-video": "real ffprobe video live: video -> video_probe metadata + audio -> asr_v1 -> search + FAILED run",
    "media-video-frame-ocr": (
        "real ffmpeg scene-frame + Tesseract OCR live: video -> scene frames -> OCR -> video_scene_frames "
        "-> index -> search on-screen text + FAILED run operator-evidence"
    ),
    "media-embeddings": "real fastembed embeddings: semantic query ranks via dense vectors (local + ES) + fail-closed",
    "media-workflow-temporal": "media Temporal workflow drives processing -> DB COMMITTED derivative",
    "media-external": "real s3-external connector: virtual media set HEADs live S3 object, drift fails closed",
    "media-golden-pipeline": (
        "GOLDEN end-to-end media plane: raw upload (live MinIO) -> real OCR/ASR -> content_units + SUCCEEDED "
        "media_processing_runs -> ontology media_reference binding -> live Elasticsearch index -> real hybrid/"
        "semantic search (cited text_hash + ACL) -> real Pillow preview cache; one version threads upstream->downstream"
    ),
    "action-external-writeback-live": (
        "real action external writeback to live MinIO: write timeout -> outcome_unknown + idempotent "
        "replay; external success + local failure -> compensation_required resolved by real remote_lookup"
    ),
}
KNOWN_SCENARIOS = {**REQUIRED_SCENARIOS, **ADDITIONAL_SCENARIOS}


@dataclass(frozen=True)
class IntegrationScenarioMarker:
    scenario: str
    path: str
    function: str
    line: int


@dataclass(frozen=True)
class IntegrationScenarioFinding:
    code: str
    scenario: str | None
    path: str | None
    function: str | None
    line: int | None
    message: str


def _repo_relative(path: Path, *, root: Path = ROOT) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _test_files(test_dirs: tuple[Path, ...]) -> list[Path]:
    files: list[Path] = []
    for test_dir in test_dirs:
        if test_dir.is_file() and test_dir.name.startswith("test_") and test_dir.suffix == ".py":
            files.append(test_dir)
        elif test_dir.is_dir():
            files.extend(sorted(path for path in test_dir.rglob("test_*.py") if path.is_file()))
    return sorted(files)


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _scenario_from_marker(decorator: ast.expr) -> tuple[str | None, str | None]:
    if not isinstance(decorator, ast.Call) or _call_name(decorator.func) != MARKER_CALL:
        return None, None
    if len(decorator.args) != 1 or decorator.keywords:
        return None, "integration_scenario marker must have exactly one string positional argument"
    arg = decorator.args[0]
    if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
        return None, "integration_scenario marker argument must be a string literal"
    return arg.value, None


def _markers_for_function(
    *,
    path: Path,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    root: Path,
) -> tuple[list[IntegrationScenarioMarker], list[IntegrationScenarioFinding]]:
    markers: list[IntegrationScenarioMarker] = []
    findings: list[IntegrationScenarioFinding] = []
    for decorator in node.decorator_list:
        scenario, error = _scenario_from_marker(decorator)
        if error is not None:
            findings.append(
                IntegrationScenarioFinding(
                    code="integration_scenario_marker_invalid",
                    scenario=None,
                    path=_repo_relative(path, root=root),
                    function=node.name,
                    line=getattr(decorator, "lineno", node.lineno),
                    message=error,
                )
            )
        if scenario is None:
            continue
        markers.append(
            IntegrationScenarioMarker(
                scenario=scenario,
                path=_repo_relative(path, root=root),
                function=node.name,
                line=getattr(decorator, "lineno", node.lineno),
            )
        )
    return markers, findings


def collect_markers(
    *,
    test_dirs: tuple[Path, ...] = DEFAULT_TEST_DIRS,
    root: Path = ROOT,
) -> tuple[list[IntegrationScenarioMarker], list[IntegrationScenarioFinding]]:
    markers: list[IntegrationScenarioMarker] = []
    findings: list[IntegrationScenarioFinding] = []
    for path in _test_files(test_dirs):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) or not node.name.startswith("test_"):
                continue
            node_markers, node_findings = _markers_for_function(path=path, node=node, root=root)
            markers.extend(node_markers)
            findings.extend(node_findings)
    return sorted(markers, key=lambda marker: (marker.scenario, marker.path, marker.line)), findings


def collect_findings(
    *,
    test_dirs: tuple[Path, ...] = DEFAULT_TEST_DIRS,
    root: Path = ROOT,
) -> list[IntegrationScenarioFinding]:
    markers, findings = collect_markers(test_dirs=test_dirs, root=root)
    present = {marker.scenario for marker in markers if marker.scenario in REQUIRED_SCENARIOS}
    for marker in markers:
        if marker.scenario not in KNOWN_SCENARIOS:
            findings.append(
                IntegrationScenarioFinding(
                    code="integration_scenario_unknown",
                    scenario=marker.scenario,
                    path=marker.path,
                    function=marker.function,
                    line=marker.line,
                    message="Unknown integration scenario marker",
                )
            )
    for scenario in sorted(set(REQUIRED_SCENARIOS) - present):
        findings.append(
            IntegrationScenarioFinding(
                code="integration_scenario_missing",
                scenario=scenario,
                path=None,
                function=None,
                line=None,
                message=f"Required integration scenario is not marked: {REQUIRED_SCENARIOS[scenario]}",
            )
        )
    return sorted(findings, key=lambda finding: (finding.code, finding.scenario or "", finding.path or ""))


def write_report(
    output: Path,
    findings: list[IntegrationScenarioFinding],
    *,
    test_dirs: tuple[Path, ...] = DEFAULT_TEST_DIRS,
    root: Path = ROOT,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    markers, _ = collect_markers(test_dirs=test_dirs, root=root)
    present = sorted({marker.scenario for marker in markers if marker.scenario in REQUIRED_SCENARIOS})
    present_additional = sorted({marker.scenario for marker in markers if marker.scenario in ADDITIONAL_SCENARIOS})
    report = {
        "gate": "integration_scenario_markers",
        "baseline": {"missing_required_scenarios": 0, "unknown_scenarios": 0},
        "required_scenarios": REQUIRED_SCENARIOS,
        "additional_scenarios": ADDITIONAL_SCENARIOS,
        "present_required_scenarios": present,
        "present_additional_scenarios": present_additional,
        "required_count": len(REQUIRED_SCENARIOS),
        "present_required_count": len(present),
        "markers": [asdict(marker) for marker in markers],
        "count": len(findings),
        "violations": [asdict(finding) for finding in findings],
        "gate_pass": not findings,
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(findings: list[IntegrationScenarioFinding], output: Path) -> None:
    print("Release gate blocked: required integration scenarios are not fully marked.")
    for finding in findings:
        location = ""
        if finding.path is not None and finding.line is not None:
            location = f" at {finding.path}:{finding.line}"
        scenario = f" {finding.scenario}" if finding.scenario else ""
        print(f"- {finding.code}{scenario}{location}: {finding.message}")
    print(f"Report: {_repo_relative(output)}")


def _resolve_test_dirs(paths: list[Path]) -> tuple[Path, ...]:
    if not paths:
        return DEFAULT_TEST_DIRS
    return tuple(path if path.is_absolute() else ROOT / path for path in paths)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate required integration scenario pytest markers.")
    parser.add_argument("--test-dir", action="append", type=Path, default=[])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    test_dirs = _resolve_test_dirs(args.test_dir)
    findings = collect_findings(test_dirs=test_dirs)
    write_report(args.output, findings, test_dirs=test_dirs)
    if findings:
        _print_failure(findings, args.output)
        return 1
    print("Integration-scenario marker gate passed: all 7 required scenarios are marked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
