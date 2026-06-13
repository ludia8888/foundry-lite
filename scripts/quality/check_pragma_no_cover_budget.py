"""Enforces guideline §11.4: coverage exclusions require a shrinking budget.

`# pragma: no cover` can be legitimate for framework glue or impossible
branches, but it is also an easy way to hide untested behavior. This gate reads
Python comments only, requires every exclusion to state a reason, and blocks any
count above the configured baseline.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "pragma_no_cover_budget.json"
DEFAULT_ROOTS = (ROOT / "libs", ROOT / "apps", ROOT / "scripts")


@dataclass(frozen=True)
class PragmaFinding:
    path: str
    line: int
    comment: str
    reason: str
    violation: str


def _repo_relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _reason_from_comment(comment: str) -> str:
    marker = "reason:"
    if marker not in comment:
        return ""
    return comment.split(marker, 1)[1].strip()


def _findings_for_file(path: Path) -> list[PragmaFinding]:
    findings: list[PragmaFinding] = []
    source = path.read_text(encoding="utf-8")
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        comment = token.string.strip()
        if "pragma: no cover" not in comment:
            continue
        reason = _reason_from_comment(comment)
        violation = "" if reason else "missing_reason"
        findings.append(
            PragmaFinding(
                path=_repo_relative(path),
                line=token.start[0],
                comment=comment,
                reason=reason,
                violation=violation,
            )
        )
    return findings


def collect_findings(scan_roots: tuple[Path, ...] = DEFAULT_ROOTS) -> list[PragmaFinding]:
    findings: list[PragmaFinding] = []
    for root in scan_roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            findings.extend(_findings_for_file(path))
    return findings


def write_report(output: Path, findings: list[PragmaFinding], baseline: int) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    count = len(findings)
    reason_violations = [finding for finding in findings if finding.violation]
    report = {
        "count": count,
        "baseline": baseline,
        "gate_pass": count <= baseline and not reason_violations,
        "violations": [finding.__dict__ for finding in findings if count > baseline or finding.violation],
        "summary": {
            "reason_violations": len(reason_violations),
            "budget_delta": count - baseline,
        },
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(findings: list[PragmaFinding], baseline: int, output: Path) -> None:
    count = len(findings)
    print("Release gate blocked: coverage exclusions exceeded the allowed budget or lack reasons.")
    print(f"- pragma no cover count: {count}, baseline: {baseline}")
    for finding in findings:
        if count > baseline or finding.violation:
            reason = f" reason={finding.reason!r}" if finding.reason else " missing reason"
            print(f"- {finding.path}:{finding.line}{reason}")
    print(f"Report: {_repo_relative(output)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the # pragma: no cover budget.")
    parser.add_argument("--baseline", type=int, default=0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--root", action="append", type=Path, dest="roots")
    args = parser.parse_args(argv)

    scan_roots = tuple(args.roots) if args.roots else DEFAULT_ROOTS
    findings = collect_findings(scan_roots)
    write_report(args.output, findings, args.baseline)
    reason_violations = [finding for finding in findings if finding.violation]
    if len(findings) > args.baseline or reason_violations:
        _print_failure(findings, args.baseline, args.output)
        return 1
    print(f"pragma no cover budget passed ({len(findings)}/{args.baseline}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
