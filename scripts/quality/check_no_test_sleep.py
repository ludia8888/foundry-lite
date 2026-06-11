"""Enforces guideline §18.1/§18.3: tests must not hide races with sleep.

Sleeping in tests is a root-cause smell: it turns a real ordering or readiness
problem into a timing lottery. Tests should wait on explicit state, events,
fixtures, or polling helpers with a clear timeout instead.
"""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "no_test_sleep.json"
BANNED_CALLS = {"time.sleep", "asyncio.sleep"}


@dataclass(frozen=True)
class SleepFinding:
    path: str
    line: int
    call: str


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _findings_for_file(path: Path) -> list[SleepFinding]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    findings: list[SleepFinding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call = _call_name(node.func)
        if call in BANNED_CALLS:
            findings.append(SleepFinding(path=str(path.relative_to(ROOT)), line=node.lineno, call=call))
    return findings


def collect_findings() -> list[SleepFinding]:
    findings: list[SleepFinding] = []
    for path in sorted(TESTS.rglob("*.py")):
        findings.extend(_findings_for_file(path))
    return findings


def write_report(output: Path, findings: list[SleepFinding]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "count": len(findings),
        "baseline": 0,
        "gate_pass": not findings,
        "violations": [finding.__dict__ for finding in findings],
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(findings: list[SleepFinding], output: Path) -> None:
    print("Release gate blocked: tests must not use sleep as a race-condition workaround.")
    for finding in findings:
        print(f"- {finding.path}:{finding.line} calls {finding.call}")
    print(f"Report: {output.relative_to(ROOT)}")


def main(argv: list[str] | None = None) -> int:
    output = Path(argv[0]) if argv else DEFAULT_OUTPUT
    findings = collect_findings()
    write_report(output, findings)
    if findings:
        _print_failure(findings, output)
        return 1
    print("No test sleep calls found.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
