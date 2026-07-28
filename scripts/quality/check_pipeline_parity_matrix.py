"""Validate the public-behavior Pipeline Builder parity matrix.

This is a documentation/evidence meta gate. It prevents an official Palantir
capability from being marked current without repository evidence and prevents
planned work from being presented as an implemented product surface.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _proof_matrix_lib import all_test_names  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MATRIX = ROOT / "docs" / "pipeline-builder-parity-matrix.json"
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "pipeline_builder_parity_matrix.json"

ALLOWED_STATUSES = {"current", "foundation", "planned"}
REQUIRED_CAPABILITY_IDS = {
    "async-dag-scheduler",
    "builder-p0-correctness",
    "collaboration-production-parity",
    "document-intelligence-lab",
    "media-plane-runtime",
    "media-processor-registry",
    "multi-output-plane-commit",
    "named-artifact-ports",
    "no-commit-multimodal-preview",
    "ontology-aip-handoff",
    "pipeline-execution-evidence",
    "python-code-isolation",
    "server-node-descriptor-catalog",
    "streaming-geospatial-runtime",
    "tabular-builder-compatibility",
    "trained-model-node",
    "typed-graph-v2",
    "use-llm-board",
}
REQUIRED_CAPABILITY_FIELDS = {
    "id",
    "publicBehavior",
    "sourceIds",
    "status",
    "implementationEvidence",
    "apiSurfaces",
    "sdkSurfaces",
    "uiSurfaces",
    "proofTests",
    "operatorEvidence",
    "gaps",
    "rolloutPhase",
    "completionRule",
}
CAPABILITY_LIST_FIELDS = {
    "sourceIds",
    "implementationEvidence",
    "apiSurfaces",
    "sdkSurfaces",
    "uiSurfaces",
    "proofTests",
    "operatorEvidence",
    "gaps",
}
STREAMING_SCOPE_ROOTS = (
    Path("libs"),
    Path("apps"),
)
STREAMING_SCOPE_FILES = (
    Path("package.json"),
    Path("foundry_lite_python_engineering_guidelines_ko.md"),
    Path("foundry_lite_development_plan_ko_sprintified.md"),
    Path("foundry_lite_sprint_breakdown_ko.md"),
    Path("docs/implementation-status.md"),
    Path("docs/pipeline-builder-parity-matrix.json"),
    Path("docs/adr/0002-public-behavior-mmdp-pipeline-graph-v2.md"),
)
STREAMING_CONSTRAINT_TOKENS = ("kafka", "cdc", "websocket", "checkpoint", "lease", "fencing")
EXCLUDED_STREAM_ENGINE = "fl" + "ink"
JsonObject = dict[str, object]


@dataclass(frozen=True)
class PipelineParityFinding:
    code: str
    path: str
    reference: str
    message: str


def collect_findings(
    root: Path = ROOT,
    *,
    matrix_path: Path = DEFAULT_MATRIX,
    known_tests: set[str] | None = None,
) -> list[PipelineParityFinding]:
    matrix = matrix_path if matrix_path.is_absolute() else root / matrix_path
    if not matrix.exists():
        return [_finding(root, matrix, "missing_matrix", str(matrix), "Parity matrix is required.")]
    payload = _load_json(matrix)
    tests = known_tests if known_tests is not None else all_test_names(root)
    findings = _top_level_findings(root, matrix, payload)
    findings.extend(_streaming_scope_findings(root, matrix, payload))
    sources, source_findings = _official_sources(root, matrix, payload)
    findings.extend(source_findings)
    findings.extend(_capability_findings(root, matrix, payload, sources, tests))
    return findings


def _load_json(path: Path) -> JsonObject:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return cast(JsonObject, payload)


def _top_level_findings(root: Path, matrix: Path, payload: JsonObject) -> list[PipelineParityFinding]:
    findings: list[PipelineParityFinding] = []
    if payload.get("schemaVersion") != 1:
        findings.append(_finding(root, matrix, "invalid_schema_version", "schemaVersion", "Expected schemaVersion 1."))
    if set(_string_list(payload.get("statusValues"))) != ALLOWED_STATUSES:
        findings.append(
            _finding(
                root,
                matrix,
                "invalid_status_values",
                "statusValues",
                "Statuses must exactly be current, foundation, and planned.",
            )
        )
    if not _object_list(payload.get("officialSources")):
        findings.append(_finding(root, matrix, "missing_sources", "officialSources", "Official sources are required."))
    if not _object_list(payload.get("capabilities")):
        findings.append(_finding(root, matrix, "missing_capabilities", "capabilities", "Capabilities are required."))
    return findings


def _streaming_scope_findings(
    root: Path,
    matrix: Path,
    payload: JsonObject,
) -> list[PipelineParityFinding]:
    constraints = _string_list(payload.get("implementationConstraints"))
    constraint_text = " ".join(constraints).lower()
    findings: list[PipelineParityFinding] = []
    if not constraints or any(token not in constraint_text for token in STREAMING_CONSTRAINT_TOKENS):
        findings.append(
            _finding(
                root,
                matrix,
                "missing_streaming_scope_constraint",
                "implementationConstraints",
                "Streaming scope must name Kafka, CDC, WebSocket, checkpoint, lease, and fencing.",
            )
        )
    for path in _streaming_scope_paths(root):
        if EXCLUDED_STREAM_ENGINE in path.read_text(encoding="utf-8", errors="ignore").lower():
            findings.append(
                _finding(
                    root,
                    path,
                    "excluded_stream_engine_reference",
                    str(path.relative_to(root)),
                    "The excluded streaming engine cannot re-enter active code or planning scope.",
                )
            )
    return findings


def _streaming_scope_paths(root: Path) -> list[Path]:
    paths = [root / path for path in STREAMING_SCOPE_FILES if (root / path).is_file()]
    for directory in STREAMING_SCOPE_ROOTS:
        base = root / directory
        if base.is_dir():
            paths.extend(path for path in base.rglob("*") if path.is_file() and path.suffix in {".py", ".ts", ".tsx"})
    return paths


def _official_sources(
    root: Path,
    matrix: Path,
    payload: JsonObject,
) -> tuple[set[str], list[PipelineParityFinding]]:
    seen: set[str] = set()
    findings: list[PipelineParityFinding] = []
    for source in _object_list(payload.get("officialSources")):
        source_id = _text(source.get("id"))
        url = _text(source.get("url"))
        if not source_id or not _text(source.get("title")) or not _text(source.get("observedBehavior")):
            findings.append(
                _finding(
                    root,
                    matrix,
                    "incomplete_official_source",
                    source_id or "<missing>",
                    "Source fields are required.",
                )
            )
        if source_id in seen:
            findings.append(_finding(root, matrix, "duplicate_source_id", source_id, "Source id is duplicated."))
        seen.add(source_id)
        if not _is_official_palantir_url(url):
            findings.append(
                _finding(
                    root,
                    matrix,
                    "unofficial_source_url",
                    url or source_id,
                    "Source must be an HTTPS palantir.com/docs/foundry URL.",
                )
            )
    return seen, findings


def _capability_findings(
    root: Path,
    matrix: Path,
    payload: JsonObject,
    source_ids: set[str],
    known_tests: set[str],
) -> list[PipelineParityFinding]:
    capabilities = _object_list(payload.get("capabilities"))
    findings: list[PipelineParityFinding] = []
    seen: set[str] = set()
    for capability in capabilities:
        capability_id = _text(capability.get("id")) or "<missing>"
        findings.extend(_one_capability_findings(root, matrix, capability, capability_id, source_ids, known_tests))
        if capability_id in seen:
            findings.append(
                _finding(
                    root,
                    matrix,
                    "duplicate_capability_id",
                    capability_id,
                    "Capability id is duplicated.",
                )
            )
        seen.add(capability_id)
    findings.extend(_required_capability_findings(root, matrix, seen))
    return findings


def _one_capability_findings(
    root: Path,
    matrix: Path,
    capability: JsonObject,
    capability_id: str,
    source_ids: set[str],
    known_tests: set[str],
) -> list[PipelineParityFinding]:
    findings = _required_field_findings(root, matrix, capability, capability_id)
    status = _text(capability.get("status"))
    if status not in ALLOWED_STATUSES:
        findings.append(_finding(root, matrix, "invalid_status", capability_id, f"Unsupported status: {status}"))
    findings.extend(_source_reference_findings(root, matrix, capability, capability_id, source_ids))
    findings.extend(_evidence_path_findings(root, matrix, capability, capability_id))
    findings.extend(_proof_test_findings(root, matrix, capability, capability_id, known_tests))
    findings.extend(_status_boundary_findings(root, matrix, capability, capability_id, status))
    return findings


def _required_field_findings(
    root: Path,
    matrix: Path,
    capability: JsonObject,
    capability_id: str,
) -> list[PipelineParityFinding]:
    missing = sorted(REQUIRED_CAPABILITY_FIELDS - set(capability))
    findings = [
        _finding(root, matrix, "missing_capability_field", f"{capability_id}:{field}", "Capability field is required.")
        for field in missing
    ]
    for field in sorted(CAPABILITY_LIST_FIELDS & set(capability)):
        if not _is_string_list(capability.get(field)):
            findings.append(
                _finding(
                    root,
                    matrix,
                    "invalid_capability_list",
                    f"{capability_id}:{field}",
                    "Capability list fields must contain only strings.",
                )
            )
    for field in ("publicBehavior", "completionRule"):
        if field in capability and not _text(capability.get(field)).strip():
            findings.append(
                _finding(
                    root,
                    matrix,
                    "blank_capability_text",
                    f"{capability_id}:{field}",
                    "Capability behavior and completion rule cannot be blank.",
                )
            )
    return findings


def _source_reference_findings(
    root: Path,
    matrix: Path,
    capability: JsonObject,
    capability_id: str,
    source_ids: set[str],
) -> list[PipelineParityFinding]:
    refs = _string_list(capability.get("sourceIds"))
    findings: list[PipelineParityFinding] = []
    if not refs:
        findings.append(
            _finding(
                root,
                matrix,
                "missing_source_reference",
                capability_id,
                "At least one source is required.",
            )
        )
    for source_id in refs:
        if source_id not in source_ids:
            findings.append(
                _finding(
                    root,
                    matrix,
                    "unknown_source_reference",
                    f"{capability_id}:{source_id}",
                    "Source id is unknown.",
                )
            )
    return findings


def _evidence_path_findings(
    root: Path,
    matrix: Path,
    capability: JsonObject,
    capability_id: str,
) -> list[PipelineParityFinding]:
    findings: list[PipelineParityFinding] = []
    path_fields = {
        "implementationEvidence": "missing_implementation_evidence",
        "apiSurfaces": "missing_api_surface",
        "sdkSurfaces": "missing_sdk_surface",
        "uiSurfaces": "missing_ui_surface",
    }
    for field, code in path_fields.items():
        for reference in _string_list(capability.get(field)):
            if (root / reference).exists():
                continue
            findings.append(
                _finding(
                    root,
                    matrix,
                    code,
                    f"{capability_id}:{reference}",
                    f"{field} path does not exist.",
                )
            )
    return findings


def _proof_test_findings(
    root: Path,
    matrix: Path,
    capability: JsonObject,
    capability_id: str,
    known_tests: set[str],
) -> list[PipelineParityFinding]:
    findings: list[PipelineParityFinding] = []
    for test_name in _string_list(capability.get("proofTests")):
        if test_name in known_tests:
            continue
        findings.append(
            _finding(
                root,
                matrix,
                "unknown_proof_test",
                f"{capability_id}:{test_name}",
                "Proof test is not collected from the repository.",
            )
        )
    return findings


def _status_boundary_findings(
    root: Path,
    matrix: Path,
    capability: JsonObject,
    capability_id: str,
    status: str,
) -> list[PipelineParityFinding]:
    evidence = _string_list(capability.get("implementationEvidence"))
    proof_tests = _string_list(capability.get("proofTests"))
    gaps = _string_list(capability.get("gaps"))
    operator_evidence = _string_list(capability.get("operatorEvidence"))
    findings = _implemented_status_findings(
        root,
        matrix,
        capability_id,
        status,
        evidence,
        proof_tests,
        operator_evidence,
    )
    findings.extend(_incomplete_status_findings(root, matrix, capability_id, status, evidence, gaps))
    findings.extend(_rollout_phase_findings(root, matrix, capability_id, capability.get("rolloutPhase")))
    return findings


def _implemented_status_findings(
    root: Path,
    matrix: Path,
    capability_id: str,
    status: str,
    evidence: list[str],
    proof_tests: list[str],
    operator_evidence: list[str],
) -> list[PipelineParityFinding]:
    findings: list[PipelineParityFinding] = []
    if status in {"current", "foundation"} and (not evidence or not proof_tests):
        findings.append(
            _finding(
                root,
                matrix,
                "unproven_implemented_status",
                capability_id,
                "Current/foundation rows require implementation evidence and proof tests.",
            )
        )
    if status == "current" and not operator_evidence:
        findings.append(
            _finding(
                root,
                matrix,
                "current_without_operator_evidence",
                capability_id,
                "Current rows need operator evidence.",
            )
        )
    return findings


def _incomplete_status_findings(
    root: Path,
    matrix: Path,
    capability_id: str,
    status: str,
    evidence: list[str],
    gaps: list[str],
) -> list[PipelineParityFinding]:
    findings: list[PipelineParityFinding] = []
    if status in {"foundation", "planned"} and not gaps:
        findings.append(
            _finding(
                root,
                matrix,
                "gapless_incomplete_status",
                capability_id,
                "Foundation/planned rows must name gaps.",
            )
        )
    if status == "planned" and evidence:
        findings.append(
            _finding(
                root,
                matrix,
                "planned_has_implementation_evidence",
                capability_id,
                "Planned rows cannot claim code evidence.",
            )
        )
    return findings


def _rollout_phase_findings(
    root: Path,
    matrix: Path,
    capability_id: str,
    rollout_phase: object,
) -> list[PipelineParityFinding]:
    if not isinstance(rollout_phase, int) or isinstance(rollout_phase, bool) or not 1 <= rollout_phase <= 12:
        return [
            _finding(
                root,
                matrix,
                "invalid_rollout_phase",
                capability_id,
                "rolloutPhase must be an integer from 1 to 12.",
            )
        ]
    return []


def _required_capability_findings(
    root: Path,
    matrix: Path,
    capability_ids: set[str],
) -> list[PipelineParityFinding]:
    return [
        _finding(root, matrix, "missing_required_capability", capability_id, "Required parity row is missing.")
        for capability_id in sorted(REQUIRED_CAPABILITY_IDS - capability_ids)
    ]


def _is_official_palantir_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.netloc in {"palantir.com", "www.palantir.com"}
        and parsed.path.startswith("/docs/foundry/")
    )


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _object_list(value: object) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    return [cast(JsonObject, item) for item in value if isinstance(item, dict)]


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _finding(
    root: Path,
    path: Path,
    code: str,
    reference: str,
    message: str,
) -> PipelineParityFinding:
    try:
        relative_path = str(path.relative_to(root))
    except ValueError:
        relative_path = str(path)
    return PipelineParityFinding(code, relative_path, reference, message)


def write_report(
    findings: list[PipelineParityFinding],
    *,
    output_path: Path = DEFAULT_OUTPUT,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "count": len(findings),
        "violations": [asdict(finding) for finding in findings],
        "baseline": 0,
        "gate_pass": not findings,
        "requiredCapabilities": sorted(REQUIRED_CAPABILITY_IDS),
    }
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    findings = collect_findings(matrix_path=args.matrix)
    write_report(findings, output_path=args.output)
    for finding in findings:
        print(f"{finding.code}: {finding.reference}: {finding.message}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
