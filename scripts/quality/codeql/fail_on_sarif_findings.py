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
    findings: list[SarifFinding] = []
    for sarif_file in _sarif_files(path):
        sarif = json.loads(sarif_file.read_text(encoding="utf-8"))
        for run in sarif.get("runs", []):
            for result in run.get("results", []):
                rule_id = str(result.get("ruleId", ""))
                if rule_id_prefix and not rule_id.startswith(rule_id_prefix):
                    continue
                findings.append(_finding_from_result(result))
    return findings


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

    findings = collect_findings(args.sarif_path, rule_id_prefix=args.rule_id_prefix)
    if findings:
        _print_findings(findings)
        return 1
    print("CodeQL hard gate OK: 0 findings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
