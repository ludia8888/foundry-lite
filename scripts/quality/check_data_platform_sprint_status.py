"""Validate S46-S64 sprint status wording across operating docs.

This gate keeps the data-platform expansion docs from drifting apart. The
detailed sprint plan and main sprint breakdown must agree on which sprints are
complete, partial, or proposed, and the high-level status docs must keep the
same current/future boundary.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "data_platform_sprint_status.json"

SPRINT_PLAN = ROOT / "docs" / "data-platform-expansion-sprint-plan-ko.md"
SPRINT_BREAKDOWN = ROOT / "foundry_lite_sprint_breakdown_ko.md"
README = ROOT / "README.md"
IMPLEMENTATION_STATUS = ROOT / "docs" / "implementation-status.md"

SPRINT_ROW_RE = re.compile(r"^\|\s*(S\d+[A-Z]?)\s*\|")
STATUS_TOKEN_RE = re.compile(r"\[(x|~| )\]")
STATUS_LABELS = ("complete", "partial", "proposed")


@dataclass(frozen=True)
class SprintExpectation:
    sprint_id: str
    token: str
    label: str


@dataclass(frozen=True)
class SprintStatusFinding:
    code: str
    path: str
    reference: str
    message: str


EXPECTED_SPRINTS = (
    SprintExpectation("S46", "[x]", "Complete"),
    SprintExpectation("S47", "[~]", "Partial"),
    SprintExpectation("S48", "[~]", "Partial"),
    SprintExpectation("S49", "[~]", "Partial"),
    SprintExpectation("S50", "[~]", "Partial"),
    SprintExpectation("S51", "[~]", "Partial"),
    SprintExpectation("S52", "[~]", "Partial"),
    SprintExpectation("S53", "[~]", "Partial"),
    SprintExpectation("S54", "[~]", "Partial"),
    SprintExpectation("S55", "[~]", "Partial"),
    SprintExpectation("S56", "[~]", "Partial"),
    SprintExpectation("S57", "[~]", "Partial"),
    SprintExpectation("S58A", "[~]", "Partial"),
    SprintExpectation("S58B", "[~]", "Partial"),
    SprintExpectation("S58C", "[~]", "Partial"),
    SprintExpectation("S59", "[ ]", "Proposed"),
    SprintExpectation("S60", "[~]", "Partial"),
    SprintExpectation("S61", "[~]", "Partial"),
    SprintExpectation("S62", "[~]", "Partial"),
    SprintExpectation("S63", "[~]", "Partial"),
    SprintExpectation("S64", "[~]", "Partial"),
)

STATUS_TABLES = (
    (SPRINT_PLAN, "token"),
    (SPRINT_BREAKDOWN, "token+label"),
)

REQUIRED_STATUS_PHRASES = {
    SPRINT_PLAN: (
        "S46 완료, S47-S64 부분 구현",
        "현재 구현 완료 여부의 source of truth는 [Implementation Status]",
    ),
    README: (
        "S46-S64 데이터 플랫폼 확장 로드맵은 현재 브랜치에서 부분 구현 중입니다.",
        "S62 visual dataset browser/preview grid/version pin/lineage graph UX",
        "S63 evidence panel UI, S63 action execution orchestration",
    ),
    IMPLEMENTATION_STATUS: (
        "S61 Frontend Foundation + Generated SDK is partial",
        "S62 Dataset Explorer is partial",
        "S63 Insight/Action Workspace now has a partial backend/API/SDK slice",
        "S64 Operations/Recovery Console now has a partial backend/API/SDK recovery overview "
        "and post-restore validation slice",
        "full catalog-driven S62-S64 workspace UX",
    ),
}


def _repo_relative(path: Path, *, root: Path = ROOT) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def collect_findings(root: Path = ROOT) -> list[SprintStatusFinding]:
    paths = _resolved_paths(root)
    missing = _missing_file_findings(root, paths)
    if missing:
        return missing

    findings: list[SprintStatusFinding] = []
    for path, mode in STATUS_TABLES:
        resolved = paths[path]
        findings.extend(_table_findings(resolved, mode, root=root))
    for path, phrases in REQUIRED_STATUS_PHRASES.items():
        findings.extend(_phrase_findings(paths[path], phrases, root=root))
    return findings


def _resolved_paths(root: Path) -> dict[Path, Path]:
    paths = {path for path, _mode in STATUS_TABLES}
    paths.update(REQUIRED_STATUS_PHRASES)
    return {path: _resolve_path(root, path) for path in paths}


def _resolve_path(root: Path, path: Path) -> Path:
    if not path.is_absolute():
        return root / path
    try:
        return root / path.relative_to(ROOT)
    except ValueError:
        return path


def _missing_file_findings(root: Path, paths: dict[Path, Path]) -> list[SprintStatusFinding]:
    findings: list[SprintStatusFinding] = []
    for path in paths.values():
        if path.exists():
            continue
        findings.append(
            _finding(
                "missing_required_file",
                path,
                _repo_relative(path, root=root),
                "Data-platform sprint status gate cannot run without this file.",
                root=root,
            )
        )
    return findings


def _table_findings(path: Path, mode: str, *, root: Path) -> list[SprintStatusFinding]:
    rows = _sprint_rows(path)
    findings: list[SprintStatusFinding] = []
    for expected in EXPECTED_SPRINTS:
        cells = rows.get(expected.sprint_id, [])
        if not cells:
            findings.append(
                _finding(
                    "missing_sprint_status_row",
                    path,
                    expected.sprint_id,
                    "S46-S64 status tables must include every tracked sprint.",
                    root=root,
                )
            )
            continue
        if len(cells) > 1:
            findings.append(
                _finding(
                    "duplicate_sprint_status_row",
                    path,
                    expected.sprint_id,
                    "S46-S64 status tables must not duplicate sprint rows.",
                    root=root,
                )
            )
        status = cells[0][-1]
        if "token" in mode:
            findings.extend(_token_findings(path, expected, status, root=root))
        if "label" in mode:
            findings.extend(_label_findings(path, expected, status, root=root))
    findings.extend(_unexpected_sprint_findings(path, rows, root=root))
    return findings


def _sprint_rows(path: Path) -> dict[str, list[list[str]]]:
    rows: dict[str, list[list[str]]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = SPRINT_ROW_RE.match(line)
        if match is None:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        rows.setdefault(match.group(1), []).append(cells)
    return rows


def _token_findings(
    path: Path, expected: SprintExpectation, status_cell: str, *, root: Path
) -> list[SprintStatusFinding]:
    token = _status_token(status_cell)
    if token == expected.token:
        return []
    return [
        _finding(
            "sprint_status_token_mismatch",
            path,
            expected.sprint_id,
            f"Expected status token {expected.token}, found {token or '<missing>'}.",
            root=root,
        )
    ]


def _label_findings(
    path: Path, expected: SprintExpectation, status_cell: str, *, root: Path
) -> list[SprintStatusFinding]:
    label = _status_label(status_cell)
    if label == expected.label.lower():
        return []
    return [
        _finding(
            "sprint_status_label_mismatch",
            path,
            expected.sprint_id,
            f"Expected status label {expected.label}, found {label or '<missing>'}.",
            root=root,
        )
    ]


def _unexpected_sprint_findings(
    path: Path, rows: dict[str, list[list[str]]], *, root: Path
) -> list[SprintStatusFinding]:
    expected_ids = {expected.sprint_id for expected in EXPECTED_SPRINTS}
    return [
        _finding(
            "unexpected_sprint_status_row",
            path,
            sprint_id,
            "Only S46-S64 data-platform sprint status rows are allowed in these summary tables.",
            root=root,
        )
        for sprint_id in sorted(set(rows) - expected_ids)
    ]


def _phrase_findings(path: Path, phrases: tuple[str, ...], *, root: Path) -> list[SprintStatusFinding]:
    text = path.read_text(encoding="utf-8")
    return [
        _finding(
            "missing_status_boundary_phrase",
            path,
            phrase,
            "High-level docs must preserve the S46 complete, S47-S64 partial boundary.",
            root=root,
        )
        for phrase in phrases
        if phrase not in text
    ]


def _status_token(status_cell: str) -> str:
    match = STATUS_TOKEN_RE.search(status_cell)
    if match is None:
        return ""
    return f"[{match.group(1)}]"


def _status_label(status_cell: str) -> str:
    lowered = status_cell.lower()
    return next((label for label in STATUS_LABELS if label in lowered), "")


def _finding(code: str, path: Path, reference: str, message: str, *, root: Path) -> SprintStatusFinding:
    return SprintStatusFinding(
        code=code,
        path=_repo_relative(path, root=root),
        reference=reference,
        message=message,
    )


def write_report(output: Path, findings: list[SprintStatusFinding]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "FAIL" if findings else "PASS",
        "count": len(findings),
        "findings": [asdict(finding) for finding in findings],
        "expectedSprints": [asdict(expected) for expected in EXPECTED_SPRINTS],
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(findings: list[SprintStatusFinding], output: Path) -> None:
    print("Data-platform sprint status gate failed:")
    for finding in findings:
        print(f"- {finding.code} {finding.path} [{finding.reference}]: {finding.message}")
    print(f"Report: {_repo_relative(output)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check S46-S64 sprint status consistency.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    findings = collect_findings()
    write_report(args.output, findings)
    if findings:
        _print_failure(findings, args.output)
        return 1
    print("Data-platform sprint status gate passed: S46-S64 status boundaries are aligned.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
