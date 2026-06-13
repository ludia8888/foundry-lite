"""Enforces guideline §12.2: required operational metrics are exposed."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIBS = ROOT / "libs"
if str(LIBS) not in sys.path:
    sys.path.insert(0, str(LIBS))

DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "metrics_exposed.json"
REQUIRED_METRICS = {
    "foundry_lite_dataset_commit_seconds": "dataset commit duration",
    "foundry_lite_transform_run_seconds": "transform run duration",
    "foundry_lite_action_apply_seconds": "action apply latency",
    "foundry_lite_object_query_seconds": "object query latency",
    "foundry_lite_outbox_publish_lag_seconds": "outbox publish lag",
    "foundry_lite_stream_archive_lag_events": "stream archive lag",
    "foundry_lite_failed_runs_total": "failed run count",
    "foundry_lite_dlq_size": "DLQ size",
}


@dataclass(frozen=True)
class MetricFinding:
    metric: str
    purpose: str
    message: str


def extract_metric_names(payload: bytes) -> set[str]:
    names: set[str] = set()
    for raw_line in payload.decode("utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("# HELP ") or line.startswith("# TYPE "):
            parts = line.split()
            if len(parts) >= 3:
                names.add(parts[2])
            continue
        sample_name = line.split()[0].split("{", 1)[0]
        names.add(sample_name)
    return names


def collect_findings(exposed_metric_names: set[str]) -> list[MetricFinding]:
    findings: list[MetricFinding] = []
    for metric, purpose in sorted(REQUIRED_METRICS.items()):
        if metric not in exposed_metric_names:
            findings.append(
                MetricFinding(
                    metric=metric,
                    purpose=purpose,
                    message="required operational metric is not exposed in the Prometheus payload",
                )
            )
    return findings


def current_prometheus_payload() -> bytes:
    from foundry_lite.observability.metrics import prometheus_payload

    payload, _media_type = prometheus_payload()
    return payload


def write_report(output: Path, exposed_metric_names: set[str], findings: list[MetricFinding]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "count": len(findings),
        "baseline": {
            "required_metric_count": len(REQUIRED_METRICS),
            "required_metrics": sorted(REQUIRED_METRICS),
        },
        "gate_pass": not findings,
        "violations": [asdict(finding) for finding in findings],
        "summary": {
            "exposed_required_count": len(REQUIRED_METRICS) - len(findings),
            "required_count": len(REQUIRED_METRICS),
            "exposed_metrics": sorted(exposed_metric_names),
        },
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(findings: list[MetricFinding], output: Path) -> None:
    print("Required operational metrics gate failed:")
    for finding in findings:
        print(f"- {finding.metric}: {finding.purpose}")
    print(f"Report: {output.relative_to(ROOT)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check required Prometheus metrics exposure.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    exposed_metric_names = extract_metric_names(current_prometheus_payload())
    findings = collect_findings(exposed_metric_names)
    write_report(args.output, exposed_metric_names, findings)
    if findings:
        _print_failure(findings, args.output)
        return 1
    print(f"Required operational metrics gate passed: {len(REQUIRED_METRICS)} metrics are exposed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
