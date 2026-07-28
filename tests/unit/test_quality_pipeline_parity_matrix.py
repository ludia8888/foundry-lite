from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from scripts.quality import check_pipeline_parity_matrix as gate
from scripts.quality._proof_matrix_lib import all_test_names

JsonObject = dict[str, object]


def test_pipeline_parity_matrix_passes_current_repo() -> None:
    assert gate.collect_findings() == []


def test_pipeline_parity_matrix_requires_every_capability(tmp_path: Path) -> None:
    matrix = _matrix()
    capabilities = _objects(matrix["capabilities"])
    matrix["capabilities"] = capabilities[1:]
    path = _write_matrix(tmp_path, matrix)

    findings = gate.collect_findings(tmp_path, matrix_path=path, known_tests=_proof_tests(matrix))

    assert any(finding.code == "missing_required_capability" for finding in findings)


def test_pipeline_parity_matrix_rejects_unofficial_source(tmp_path: Path) -> None:
    matrix = _matrix()
    _objects(matrix["officialSources"])[0]["url"] = "https://example.com/copied-doc"
    path = _write_matrix(tmp_path, matrix)

    findings = gate.collect_findings(tmp_path, matrix_path=path, known_tests=_proof_tests(matrix))

    assert any(finding.code == "unofficial_source_url" for finding in findings)


def test_pipeline_parity_matrix_rejects_unproven_current_status(tmp_path: Path) -> None:
    matrix = _matrix()
    capability = _capability(matrix, "tabular-builder-compatibility")
    capability["implementationEvidence"] = []
    capability["proofTests"] = []
    capability["operatorEvidence"] = []
    path = _write_matrix(tmp_path, matrix)

    findings = gate.collect_findings(tmp_path, matrix_path=path, known_tests=set())

    assert any(finding.code == "unproven_implemented_status" for finding in findings)
    assert any(finding.code == "current_without_operator_evidence" for finding in findings)


def test_pipeline_parity_matrix_rejects_stale_evidence_and_test(tmp_path: Path) -> None:
    matrix = _matrix()
    capability = _capability(matrix, "typed-graph-v2")
    capability["implementationEvidence"] = ["libs/missing_graph.py"]
    capability["apiSurfaces"] = ["apps/api/missing_pipeline.py"]
    capability["proofTests"] = ["test_missing_graph_contract"]
    path = _write_matrix(tmp_path, matrix)
    (tmp_path / "libs" / "missing_graph.py").unlink()
    (tmp_path / "apps" / "api" / "missing_pipeline.py").unlink()

    findings = gate.collect_findings(tmp_path, matrix_path=path, known_tests=set())

    assert any(finding.code == "missing_implementation_evidence" for finding in findings)
    assert any(finding.code == "missing_api_surface" for finding in findings)
    assert any(finding.code == "unknown_proof_test" for finding in findings)


def test_pipeline_parity_matrix_rejects_invalid_lists_and_blank_completion_rule(tmp_path: Path) -> None:
    matrix = _matrix()
    capability = _capability(matrix, "typed-graph-v2")
    capability["uiSurfaces"] = ["apps/foundry", 7]
    capability["completionRule"] = ""
    path = _write_matrix(tmp_path, matrix)

    findings = gate.collect_findings(tmp_path, matrix_path=path, known_tests=_proof_tests(matrix))

    assert any(finding.code == "invalid_capability_list" for finding in findings)
    assert any(finding.code == "blank_capability_text" for finding in findings)


def test_pipeline_parity_matrix_keeps_planned_rows_free_of_code_claims(tmp_path: Path) -> None:
    matrix = _matrix()
    capability = _capability(matrix, "collaboration-production-parity")
    capability["status"] = "planned"
    capability["implementationEvidence"] = ["package.json"]
    path = _write_matrix(tmp_path, matrix)

    findings = gate.collect_findings(tmp_path, matrix_path=path, known_tests=_proof_tests(matrix))

    assert any(finding.code == "planned_has_implementation_evidence" for finding in findings)


def test_pipeline_parity_matrix_requires_the_selected_streaming_boundary(tmp_path: Path) -> None:
    matrix = _matrix()
    matrix["implementationConstraints"] = ["Kafka only"]
    path = _write_matrix(tmp_path, matrix)

    findings = gate.collect_findings(tmp_path, matrix_path=path, known_tests=_proof_tests(matrix))

    assert any(finding.code == "missing_streaming_scope_constraint" for finding in findings)


def test_pipeline_parity_matrix_rejects_excluded_stream_engine_in_active_scope(tmp_path: Path) -> None:
    matrix = _matrix()
    path = _write_matrix(tmp_path, matrix)
    source = tmp_path / "libs" / "streaming_runtime.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    excluded_engine = "fl" + "ink"
    source.write_text(f"runtime = '{excluded_engine}'\n", encoding="utf-8")

    findings = gate.collect_findings(tmp_path, matrix_path=path, known_tests=_proof_tests(matrix))

    assert any(finding.code == "excluded_stream_engine_reference" for finding in findings)


def test_pipeline_parity_matrix_writes_machine_report(tmp_path: Path) -> None:
    finding = gate.PipelineParityFinding("example", "matrix.json", "row", "message")
    output = tmp_path / "pipeline_builder_parity_matrix.json"

    gate.write_report([finding], output_path=output)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["count"] == 1
    assert report["gate_pass"] is False
    assert report["baseline"] == 0


def test_pipeline_parity_matrix_collects_playwright_proof_titles(tmp_path: Path) -> None:
    spec = tmp_path / "tests" / "e2e-foundry" / "pipeline-builder-flow.spec.ts"
    spec.parent.mkdir(parents=True)
    spec.write_text(
        """
test(
  "Stream and geospatial nodes expose executable typed configuration boards",
  async ({ page }) => {
    await page.goto("/pipelines");
  },
);
""",
        encoding="utf-8",
    )

    assert "Stream and geospatial nodes expose executable typed configuration boards" in all_test_names(tmp_path)


def _matrix() -> JsonObject:
    return cast(
        JsonObject,
        json.loads(gate.DEFAULT_MATRIX.read_text(encoding="utf-8")),
    )


def _write_matrix(root: Path, payload: JsonObject) -> Path:
    path = root / "docs" / "pipeline-builder-parity-matrix.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    for evidence_path in _evidence_paths(payload):
        target = root / evidence_path
        if (gate.ROOT / evidence_path).is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
    return path


def _evidence_paths(payload: JsonObject) -> set[str]:
    paths: set[str] = set()
    for capability in _objects(payload["capabilities"]):
        for field in ("implementationEvidence", "apiSurfaces", "sdkSurfaces", "uiSurfaces"):
            for value in capability.get(field, []):
                if isinstance(value, str):
                    paths.add(value)
    return paths


def _proof_tests(payload: JsonObject) -> set[str]:
    tests: set[str] = set()
    for capability in _objects(payload["capabilities"]):
        for value in capability.get("proofTests", []):
            if isinstance(value, str):
                tests.add(value)
    return tests


def _capability(payload: JsonObject, capability_id: str) -> JsonObject:
    return next(item for item in _objects(payload["capabilities"]) if item["id"] == capability_id)


def _objects(value: object) -> list[JsonObject]:
    return [cast(JsonObject, item) for item in cast(list[object], value)]
