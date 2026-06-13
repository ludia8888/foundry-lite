"""Enforces guideline §18.1/§18.2: PRs need root-cause evidence.

This gate reads a pull request body and requires three sections:
Root Cause, Impact, and Regression Test. Local non-PR runs are skipped so
developers can still run `pnpm ci:gate` before opening a PR.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "pr_root_cause_section.json"
HEADING_RE = re.compile(r"^\s{0,3}#{2,3}\s+(.+?)\s*$", re.MULTILINE)
REQUIRED_SECTIONS: dict[str, tuple[str, ...]] = {
    "root_cause": ("root cause", "원인"),
    "impact": ("impact", "영향"),
    "regression_test": ("regression test", "regression tests", "회귀"),
}


@dataclass(frozen=True)
class PrRootCauseFinding:
    code: str
    section: str
    message: str


@dataclass(frozen=True)
class PrBody:
    body: str
    source: str
    is_required: bool


def _repo_relative(path: Path, *, root: Path = ROOT) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _normalize_heading(title: str) -> str:
    cleaned = re.sub(r"[*_`:\-]+", " ", title).strip().lower()
    return re.sub(r"\s+", " ", cleaned)


def _body_headings(body: str) -> set[str]:
    return {_normalize_heading(match.group(1)) for match in HEADING_RE.finditer(body)}


def _section_present(headings: set[str], aliases: tuple[str, ...]) -> bool:
    normalized_aliases = tuple(_normalize_heading(alias) for alias in aliases)
    return any(any(alias in heading for alias in normalized_aliases) for heading in headings)


def _load_event_body(event_path: Path) -> PrBody:
    event = json.loads(event_path.read_text(encoding="utf-8"))
    pull_request = event.get("pull_request")
    if not isinstance(pull_request, dict):
        return PrBody(body="", source=str(event_path), is_required=False)
    body = pull_request.get("body") or ""
    return PrBody(body=str(body), source=str(event_path), is_required=True)


def load_pr_body(
    *,
    body: str | None = None,
    body_file: Path | None = None,
    event_path: Path | None = None,
) -> PrBody:
    if body is not None:
        return PrBody(body=body, source="--body", is_required=True)
    if body_file is not None:
        return PrBody(body=body_file.read_text(encoding="utf-8"), source=str(body_file), is_required=True)
    if event_path is not None:
        return _load_event_body(event_path)
    env_event_path = os.environ.get("GITHUB_EVENT_PATH")
    if env_event_path:
        return _load_event_body(Path(env_event_path))
    return PrBody(body="", source="local", is_required=False)


def collect_findings(pr_body: PrBody) -> list[PrRootCauseFinding]:
    if not pr_body.is_required:
        return []
    headings = _body_headings(pr_body.body)
    findings: list[PrRootCauseFinding] = []
    for section, aliases in REQUIRED_SECTIONS.items():
        if _section_present(headings, aliases):
            continue
        findings.append(
            PrRootCauseFinding(
                code="pr_missing_root_cause_section",
                section=section,
                message="PR description must include Root Cause, Impact, and Regression Test sections",
            )
        )
    return findings


def write_report(output: Path, pr_body: PrBody, findings: list[PrRootCauseFinding]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "gate": "pr_root_cause_section",
        "baseline": 0,
        "source": pr_body.source,
        "required": pr_body.is_required,
        "skipped": not pr_body.is_required,
        "headings": sorted(_body_headings(pr_body.body)),
        "count": len(findings),
        "violations": [asdict(finding) for finding in findings],
        "gate_pass": not findings,
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(findings: list[PrRootCauseFinding], output: Path) -> None:
    print("Release gate blocked: PR description needs root-cause evidence.")
    for finding in findings:
        print(f"- missing {finding.section}: add a matching section to the PR body")
    print(f"Report: {_repo_relative(output)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate PR root-cause sections.")
    parser.add_argument("--body")
    parser.add_argument("--body-file", type=Path)
    parser.add_argument("--event-path", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    pr_body = load_pr_body(body=args.body, body_file=args.body_file, event_path=args.event_path)
    findings = collect_findings(pr_body)
    write_report(args.output, pr_body, findings)
    if findings:
        _print_failure(findings, args.output)
        return 1
    if pr_body.is_required:
        print("PR root-cause section gate passed: root cause, impact, and regression test are documented.")
    else:
        print("PR root-cause section gate skipped: no pull request body in local context.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
