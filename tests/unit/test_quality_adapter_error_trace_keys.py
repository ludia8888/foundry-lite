from __future__ import annotations

import json
from pathlib import Path

from scripts.quality import check_adapter_error_trace_keys as gate


def test_adapter_error_trace_gate_passes_dynamic_probe(tmp_path: Path) -> None:
    findings = gate.run_gate(tmp_path / "adapter-error-probe")

    assert findings == []


def test_adapter_error_trace_gate_flags_missing_trace() -> None:
    run = {"id": "sync_run_1", "status": "FAILED", "error": {"type": "RuntimeError"}}

    findings = gate.collect_findings(run, {"tenant_id": "tenant-1"})

    assert [finding.code for finding in findings] == ["missing_error_trace"]


def test_adapter_error_trace_gate_flags_missing_required_key() -> None:
    run = {
        "id": "sync_run_1",
        "status": "FAILED",
        "error": {"trace": {"tenant_id": "tenant-1"}},
    }

    findings = gate.collect_findings(run, {"tenant_id": "tenant-1"})

    assert findings
    assert findings[0].code == "missing_trace_keys"
    assert "request_id" in findings[0].message


def test_adapter_error_trace_gate_flags_wrong_request_context() -> None:
    run = {
        "id": "sync_run_1",
        "status": "FAILED",
        "error": {
            "trace": {
                "tenant_id": "tenant-1",
                "actor_user_id": "actor-1",
                "request_id": "wrong",
                "run_id": "sync_run_1",
                "correlation_id": "sync_run_1",
                "adapter": "compute_adapter.csv_to_parquet",
            }
        },
    }

    findings = gate.collect_findings(run, {"tenant_id": "tenant-1", "request_id": "request-1"})

    assert any(finding.code == "trace_key_mismatch" for finding in findings)


def test_adapter_error_trace_gate_writes_json_report(tmp_path: Path) -> None:
    output = tmp_path / "adapter_error_trace_keys.json"
    findings = [gate.AdapterTraceFinding(code="missing_error_trace", message="no trace", run_id="sync_run_1")]

    gate.write_report(output, findings)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["count"] == 1
    assert report["baseline"] == 0
    assert report["gate_pass"] is False
    assert "request_id" in report["required_trace_keys"]
