from __future__ import annotations

import json
from pathlib import Path

from scripts.quality import check_pipeline_builder_performance as gate


def test_pipeline_builder_performance_gate_measures_current_boundaries(tmp_path: Path) -> None:
    report, findings = gate.run_benchmark(graph_iterations=3, preview_iterations=2, schedule_iterations=3)
    metrics = report["metrics"]

    assert findings == []
    assert report["gate"] == "pipeline_builder_performance"
    assert report["gatePass"] is True
    assert report["completionStatus"] == "current_enforced"
    assert report["unprovenTargets"] == []
    assert report["profile"] == {
        "graphNodes": 200,
        "graphEdges": 600,
        "previewRows": 50,
        "graphIterations": 3,
        "previewIterations": 2,
        "scheduleIterations": 3,
    }
    assert metrics["graphValidation"]["status"] == "current_enforced"
    assert metrics["graphValidation"]["p95Seconds"] < 0.3
    assert metrics["planCompilation"]["p95Seconds"] < 2.0
    assert metrics["previewAcknowledgement"]["rowCount"] == 50
    assert metrics["previewAcknowledgement"]["p95Seconds"] < 0.5
    assert metrics["previewExecution"]["p95Seconds"] < 5.0
    assert metrics["scheduleDueScan"]["status"] == "current_enforced"
    assert metrics["scheduleDueScan"]["population"] == 10_000
    assert metrics["scheduleDueScan"]["resultLimit"] == 100
    assert metrics["scheduleDueScan"]["p95Seconds"] < 1.0

    output = tmp_path / "pipeline-builder-performance.json"
    gate.write_report(output, report)
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["gatePass"] is True


def test_pipeline_builder_performance_target_findings_are_enforced() -> None:
    metrics = {
        "graphValidation": {"p95Seconds": 0.301, "targetP95Seconds": 0.3},
        "planCompilation": {"p95Seconds": 2.001, "targetP95Seconds": 2.0},
        "previewAcknowledgement": {"p95Seconds": 0.501, "targetP95Seconds": 0.5},
        "previewExecution": {"p95Seconds": 5.001, "targetP95Seconds": 5.0},
        "scheduleDueScan": {"p95Seconds": 1.001, "targetP95Seconds": 1.0},
    }

    findings = gate.target_findings(metrics)

    assert [finding.metric for finding in findings] == [
        "graphValidation",
        "planCompilation",
        "previewAcknowledgement",
        "previewExecution",
        "scheduleDueScan",
    ]


def test_pipeline_scheduler_due_scan_measures_current_boundary() -> None:
    metric = gate.measure_schedule_scan(iterations=2)

    assert metric["status"] == "current_enforced"
    assert metric["population"] == 10_000
    assert metric["p95Seconds"] < 1.0


def test_pipeline_builder_scale_graph_is_exact_and_valid() -> None:
    graph = gate.build_scale_graph()

    assert len(graph["nodes"]) == 200
    assert len(graph["edges"]) == 600
