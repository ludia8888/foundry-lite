from __future__ import annotations

import json
from pathlib import Path

from scripts.quality import check_failed_mutation_state_runtime as gate


def _expected_context() -> dict[str, str]:
    return {
        "tenant_id": "tenant-failure-state",
        "actor_user_id": "actor-failure-state",
        "request_id": "request-failure-state",
        "adapter": "compute_adapter.csv_to_parquet",
    }


def _error_payload() -> dict[str, object]:
    return {
        "type": "RuntimeError",
        "message": "adapter exploded",
        "trace": {
            **_expected_context(),
            "run_id": "sync_run_1",
            "correlation_id": "sync_run_1",
        },
    }


def _passing_rows() -> gate.FailureRows:
    error = _error_payload()
    return gate.FailureRows(
        sync_runs=[
            {
                "id": "sync_run_1",
                "status": "FAILED",
                "transaction_id": "dstx_1",
                "error": error,
                "completed_at": "2026-06-12T00:00:00Z",
            }
        ],
        dataset_transactions=[
            {
                "id": "dstx_1",
                "status": "ABORTED",
                "metadata": {"error": error},
            }
        ],
        dataset_versions=[],
        audit_events=[
            {
                "event_type": "dataset.transaction.aborted",
                "resource_type": "dataset_transaction",
                "resource_id": "dstx_1",
                "action": "abort",
                "correlation_id": "sync_run_1",
                "after_ref": {"error": error},
            }
        ],
    )


def test_failed_mutation_state_gate_passes_dynamic_probe(tmp_path: Path) -> None:
    result = gate.run_gate(
        storage_root=tmp_path / "failure-state-probe",
        output=tmp_path / "failed_mutation_state.json",
    )

    assert result == 0


def test_failed_mutation_state_gate_accepts_complete_failure_evidence() -> None:
    findings = gate.collect_findings(_passing_rows(), _expected_context())

    assert findings == []


def test_failed_mutation_state_gate_flags_missing_abort_audit() -> None:
    rows = _passing_rows()
    rows = gate.FailureRows(
        sync_runs=rows.sync_runs,
        dataset_transactions=rows.dataset_transactions,
        dataset_versions=[],
        audit_events=[],
    )

    findings = gate.collect_findings(rows, _expected_context())

    assert "missing_abort_audit" in {finding.code for finding in findings}


def test_failed_mutation_state_gate_flags_committed_version_for_failed_transaction() -> None:
    rows = _passing_rows()
    rows = gate.FailureRows(
        sync_runs=rows.sync_runs,
        dataset_transactions=rows.dataset_transactions,
        dataset_versions=[{"id": "dsv_1", "transaction_id": "dstx_1"}],
        audit_events=rows.audit_events,
    )

    findings = gate.collect_findings(rows, _expected_context())

    assert "failed_transaction_committed_version" in {finding.code for finding in findings}


def test_failed_mutation_state_gate_flags_missing_error_trace() -> None:
    rows = _passing_rows()
    failed_run = {**rows.sync_runs[0], "error": {"type": "RuntimeError"}}
    rows = gate.FailureRows(
        sync_runs=[failed_run],
        dataset_transactions=rows.dataset_transactions,
        dataset_versions=[],
        audit_events=rows.audit_events,
    )

    findings = gate.collect_findings(rows, _expected_context())

    assert "missing_error_trace" in {finding.code for finding in findings}


def test_failed_mutation_state_gate_writes_json_report(tmp_path: Path) -> None:
    rows = _passing_rows()
    findings = [gate.FailedMutationFinding(code="missing_abort_audit", message="missing", resource_id="dstx_1")]
    output = tmp_path / "failed_mutation_state.json"

    gate.write_report(output, database_url="sqlite:///probe.db", rows=rows, findings=findings)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["count"] == 1
    assert report["baseline"]["max_violations"] == 0
    assert report["summary"]["failed_sync_runs"] == 1
    assert report["gate_pass"] is False
