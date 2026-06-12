from __future__ import annotations

from pathlib import Path

from scripts.quality import check_mvp_performance_smoke as gate


def test_mvp_performance_smoke_runs_product_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(gate.CSV_UPLOAD_LIMIT_ENV, raising=False)

    report, findings = gate.run_smoke(
        storage_root=tmp_path / "store",
        workspace_root=tmp_path / "workspace",
        csv_rows=20,
        object_rows=20,
        query_iterations=2,
        action_iterations=2,
    )

    assert findings == []
    assert report["gate"] == "mvp_performance_smoke"
    assert report["gatePass"] is True
    assert report["csvIngest"]["rows"] == 20
    assert report["objectIndex"]["objectsUpserted"] == 20
    assert report["objectQuery"]["populationRows"] == 20
    assert report["actionApplyNoWriteback"]["statuses"] == ["succeeded", "succeeded"]


def test_mvp_performance_smoke_writes_report(tmp_path: Path) -> None:
    report, findings = gate.run_smoke(
        storage_root=tmp_path / "store",
        workspace_root=tmp_path / "workspace",
        csv_rows=10,
        object_rows=10,
        query_iterations=1,
        action_iterations=1,
    )
    output = tmp_path / "mvp_performance_smoke.json"

    gate.write_report(output, report)

    assert findings == []
    assert '"gate": "mvp_performance_smoke"' in output.read_text(encoding="utf-8")


def test_mvp_performance_target_findings_are_explicit() -> None:
    report = {
        "csvIngest": {"rows": 1_000_000, "seconds": 301.0},
        "objectQuery": {"p95Seconds": 0.6, "populationRows": 100_000},
        "actionApplyNoWriteback": {"p95Seconds": 0.4},
    }

    findings = gate.target_findings(report)

    assert [finding.code for finding in findings] == [
        "csv_ingest_target_missed",
        "object_query_target_missed",
        "action_apply_target_missed",
    ]
