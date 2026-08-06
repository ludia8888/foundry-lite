"""Fail a workflow when CodeQL SARIF contains findings.

GitHub code scanning uploads SARIF alerts, but an upload with alerts can still
leave the workflow green. Foundry-lite treats root-cause security/data-flow
findings as release blockers, so this parser turns SARIF results into a hard
status check.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SarifFinding:
    path: str
    line: int | str
    rule_id: str
    message: str


def _sarif_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.exists():
        raise FileNotFoundError(f"CodeQL SARIF path does not exist: {path}")
    files = sorted(path.rglob("*.sarif"))
    if not files:
        raise FileNotFoundError(f"CodeQL SARIF path contains no .sarif files: {path}")
    return files


def _finding_from_result(result: dict[str, Any]) -> SarifFinding:
    location = (result.get("locations") or [{}])[0]
    physical = location.get("physicalLocation", {})
    artifact = physical.get("artifactLocation", {})
    region = physical.get("region", {})
    message = result.get("message", {}).get("text", "")
    return SarifFinding(
        path=str(artifact.get("uri", "?")),
        line=region.get("startLine", "?"),
        rule_id=str(result.get("ruleId", "?")),
        message=str(message),
    )


def collect_findings(path: Path, *, rule_id_prefix: str | None = None) -> list[SarifFinding]:
    """Findings that block the gate. Suppressed results are reported separately, never silently."""
    blocking, _suppressed = partitioned_findings(path, rule_id_prefix=rule_id_prefix)
    return blocking


def partitioned_findings(
    path: Path, *, rule_id_prefix: str | None = None
) -> tuple[list[SarifFinding], list[SarifFinding]]:
    """Split results into blocking and suppressed.

    A gate with no way to record a reviewed false positive is a gate that gets ignored, and this
    one was: it stayed red for three days across every push. CodeQL only emits a `suppressions`
    entry when the source carries an inline `# codeql[rule-id]` comment, so a suppression has to
    be written next to the code, in the diff, where a reviewer sees the justification. They are
    still printed on every run -- an allowance that disappears from the output is an allowance
    nobody revisits.
    """
    blocking: list[SarifFinding] = []
    suppressed: list[SarifFinding] = []
    for sarif_file in _sarif_files(path):
        sarif = json.loads(sarif_file.read_text(encoding="utf-8"))
        for run in sarif.get("runs", []):
            for result in run.get("results", []):
                rule_id = str(result.get("ruleId", ""))
                if rule_id_prefix and not rule_id.startswith(rule_id_prefix):
                    continue
                target = suppressed if result.get("suppressions") else blocking
                target.append(_finding_from_result(result))
    return blocking, suppressed


def _print_suppressed(findings: list[SarifFinding]) -> None:
    if not findings:
        return
    print(f"CodeQL suppressed in source: {len(findings)} finding(s). Each carries its reason at the call site.")
    for finding in findings:
        print(f"~ {finding.path}:{finding.line} [{finding.rule_id}]")


def _print_findings(findings: list[SarifFinding]) -> None:
    print(f"CodeQL hard gate failed: {len(findings)} finding(s).")
    for finding in findings:
        print(f"- {finding.path}:{finding.line} [{finding.rule_id}] {finding.message}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("sarif_path", type=Path)
    parser.add_argument(
        "--rule-id-prefix",
        default=None,
        help="Optional prefix for limiting the hard gate to a custom rule family.",
    )
    args = parser.parse_args(argv)

    findings, suppressed = partitioned_findings(args.sarif_path, rule_id_prefix=args.rule_id_prefix)
    _print_suppressed(suppressed)
    if findings:
        _print_findings(findings)
        return 1
    print(f"CodeQL hard gate OK: 0 findings ({len(suppressed)} suppressed in source).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
