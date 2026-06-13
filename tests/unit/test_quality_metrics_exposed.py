from __future__ import annotations

import json
from pathlib import Path

from foundry_lite.observability import metrics

from scripts.quality import check_metrics_exposed as gate


def _payload_for(metric_names: list[str]) -> bytes:
    lines: list[str] = []
    for name in metric_names:
        lines.append(f"# HELP {name} unit metric")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} 0.0")
    return "\n".join(lines).encode("utf-8")


def test_metrics_exposed_extracts_help_type_and_sample_names() -> None:
    payload = b"""
# HELP foundry_lite_dataset_commit_seconds Dataset commit duration
# TYPE foundry_lite_dataset_commit_seconds histogram
foundry_lite_dataset_commit_seconds_count 0.0
foundry_lite_dlq_size 0.0
"""

    names = gate.extract_metric_names(payload)

    assert "foundry_lite_dataset_commit_seconds" in names
    assert "foundry_lite_dataset_commit_seconds_count" in names
    assert "foundry_lite_dlq_size" in names


def test_metrics_exposed_flags_missing_required_metric() -> None:
    exposed = set(gate.REQUIRED_METRICS) - {"foundry_lite_dlq_size"}

    findings = gate.collect_findings(exposed)

    assert [finding.metric for finding in findings] == ["foundry_lite_dlq_size"]


def test_metrics_exposed_accepts_all_required_metrics() -> None:
    payload = _payload_for(sorted(gate.REQUIRED_METRICS))

    assert gate.collect_findings(gate.extract_metric_names(payload)) == []


def test_metrics_exposed_actual_prometheus_payload_contains_required_metrics() -> None:
    metrics.record_dataset_commit(0.01)
    metrics.record_transform_run(0.02)
    metrics.record_action_apply(0.03)
    metrics.record_object_query(0.04)
    metrics.set_outbox_publish_lag(0.5)
    metrics.record_failed_run()
    metrics.set_dlq_size(1)

    payload, _media_type = metrics.prometheus_payload()

    assert gate.collect_findings(gate.extract_metric_names(payload)) == []


def test_metrics_exposed_writes_json_report(tmp_path: Path) -> None:
    exposed = set(gate.REQUIRED_METRICS) - {"foundry_lite_failed_runs_total"}
    findings = gate.collect_findings(exposed)
    output = tmp_path / "metrics_exposed.json"

    gate.write_report(output, exposed, findings)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["count"] == 1
    assert report["baseline"]["required_metric_count"] == len(gate.REQUIRED_METRICS)
    assert report["gate_pass"] is False
