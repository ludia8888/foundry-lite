from __future__ import annotations

import json
from pathlib import Path

from scripts.quality import check_data_platform_sprint_status as gate


def test_data_platform_sprint_status_gate_passes_current_repo() -> None:
    assert gate.collect_findings() == []


def test_data_platform_sprint_status_rejects_token_drift(tmp_path: Path) -> None:
    _write_status_docs(tmp_path)
    plan = tmp_path / "docs" / "data-platform-expansion-sprint-plan-ko.md"
    plan.write_text(
        plan.read_text(encoding="utf-8").replace(
            "| S63 | Product | Insight/Action Workspace | S61, S53, S60 | [~] |",
            "| S63 | Product | Insight/Action Workspace | S61, S53, S60 | [x] |",
        ),
        encoding="utf-8",
    )

    findings = gate.collect_findings(tmp_path)

    assert any(finding.code == "sprint_status_token_mismatch" for finding in findings)
    assert any(finding.reference == "S63" for finding in findings)


def test_data_platform_sprint_status_rejects_label_drift(tmp_path: Path) -> None:
    _write_status_docs(tmp_path)
    breakdown = tmp_path / "foundry_lite_sprint_breakdown_ko.md"
    breakdown.write_text(
        breakdown.read_text(encoding="utf-8").replace(
            "| S64 | Product | Operations/Recovery Console | S47, S51, S52, S56, S57 | [ ] Proposed |",
            "| S64 | Product | Operations/Recovery Console | S47, S51, S52, S56, S57 | [ ] Partial |",
        ),
        encoding="utf-8",
    )

    findings = gate.collect_findings(tmp_path)

    assert any(finding.code == "sprint_status_label_mismatch" for finding in findings)
    assert any(finding.reference == "S64" for finding in findings)


def test_data_platform_sprint_status_rejects_missing_boundary_phrase(tmp_path: Path) -> None:
    _write_status_docs(tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8").replace(
            "S46-S63 데이터 플랫폼 확장 로드맵은 현재 브랜치에서 부분 구현 중입니다.",
            "S46-S64 데이터 플랫폼 확장 로드맵은 완료되었습니다.",
        ),
        encoding="utf-8",
    )

    findings = gate.collect_findings(tmp_path)

    assert any(finding.code == "missing_status_boundary_phrase" for finding in findings)
    assert any("S46-S63" in finding.reference for finding in findings)


def test_data_platform_sprint_status_writes_json_report(tmp_path: Path) -> None:
    _write_status_docs(tmp_path)
    output = tmp_path / "artifacts" / "quality" / "data_platform_sprint_status.json"

    findings = gate.collect_findings(tmp_path)
    gate.write_report(output, findings)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "PASS"
    assert report["expectedSprints"]


def _write_status_docs(root: Path) -> None:
    (root / "docs").mkdir(parents=True)
    _write_sprint_plan(root / "docs" / "data-platform-expansion-sprint-plan-ko.md")
    _write_sprint_breakdown(root / "foundry_lite_sprint_breakdown_ko.md")
    (root / "README.md").write_text(
        "S46-S63 데이터 플랫폼 확장 로드맵은 현재 브랜치에서 부분 구현 중입니다.\n"
        "S62 visual dataset browser/preview grid/version pin/lineage graph UX remains future.\n"
        "S63 evidence panel UI, S63 action execution orchestration remain future.\n",
        encoding="utf-8",
    )
    (root / "docs" / "implementation-status.md").write_text(
        "S61 Frontend Foundation + Generated SDK is partial.\n"
        "S62 Dataset Explorer is partial.\n"
        "S63 Insight/Action Workspace now has a partial backend/API/SDK slice.\n"
        "full catalog-driven S62-S64 workspace UX remains future.\n",
        encoding="utf-8",
    )


def _write_sprint_plan(path: Path) -> None:
    rows = _status_rows(include_labels=False)
    path.write_text(
        "**문서 상태:** Repo-integrated 확장 계획 / S46 완료, S47-S63 부분 구현\n"
        "현재 구현 완료 여부의 source of truth는 [Implementation Status](./implementation-status.md)다.\n"
        "| Sprint | 우선순위 | 핵심 결과 | 주요 의존성 | 상태 |\n"
        "|---|---:|---|---|---|\n"
        f"{rows}\n",
        encoding="utf-8",
    )


def _write_sprint_breakdown(path: Path) -> None:
    rows = _status_rows(include_labels=True)
    path.write_text(
        f"| Sprint | 우선순위 | 핵심 결과 | 주요 의존성 | 상태 |\n|---|---:|---|---|---|\n{rows}\n",
        encoding="utf-8",
    )


def _status_rows(*, include_labels: bool) -> str:
    return "\n".join(_status_row(expectation, include_labels=include_labels) for expectation in gate.EXPECTED_SPRINTS)


def _status_row(expectation: gate.SprintExpectation, *, include_labels: bool) -> str:
    details = _details(expectation.sprint_id)
    status = expectation.token
    if include_labels:
        status = f"{status} {expectation.label}"
    return f"| {expectation.sprint_id} | {details[0]} | {details[1]} | {details[2]} | {status} |"


def _details(sprint_id: str) -> tuple[str, str, str]:
    details = {
        "S46": ("P0", "Semantic SSOT + Data Pattern Matrix", "현재 CI 하네스"),
        "S47": ("P0", "Record DLQ + Replay", "S46"),
        "S48": ("P1", "Late Data + Watermark", "S47"),
        "S49": ("P1", "Multi-file Dataset + Partitioning", "S46"),
        "S50": ("P1", "Iceberg Maintenance", "S49"),
        "S51": ("P0", "Continuous CDC Worker + Rebalance Safety", "S47"),
        "S52": ("P0", "Temporal Engine Integration", "S51"),
        "S53": ("P0", "External Writeback + Saga/Reconciliation", "S52"),
        "S54": ("P1", "Data Quality Contracts", "S47, S48"),
        "S55": ("P1", "DB/Dataset/Ontology Schema Migration", "S54"),
        "S56": ("P1", "Proactive Observability + SLO", "S48, S51, S52"),
        "S57": ("P0", "Backup/Restore Commit-point Ratchet", "S50, S52, S53"),
        "S58A": ("P1", "OIDC/JWT + Secret Provider", "독립 가능"),
        "S58B": ("P1", "Anonymization/Pseudonymization", "S58A"),
        "S58C": ("P1", "Right-to-Erasure Lifecycle", "S50, S57, S58B"),
        "S59": ("P2", "Real Cluster/Cloud/Chaos Proofs", "관련 sprint"),
        "S60": ("P1", "Fine-grained Lineage + AI Evidence", "S54, S55"),
        "S61": ("Product", "Frontend Foundation + Generated SDK", "현재 API"),
        "S62": ("Product", "Object/Dataset Explorer", "S61"),
        "S63": ("Product", "Insight/Action Workspace", "S61, S53, S60"),
        "S64": ("Product", "Operations/Recovery Console", "S47, S51, S52, S56, S57"),
    }
    return details[sprint_id]
