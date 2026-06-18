"""Aggregate the runtime-evidence gates into one root-cause GitHub step summary.

Reads the JSON artifacts produced by the proof-matrix, source-of-truth, and
operator-evidence gates and writes a single combined summary table plus a
root-cause hint and suggested next files. Informational: exits 0 (the individual
gates own pass/fail), but surfaces *why* a developer's PR likely failed.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts" / "quality"

_GATES = [
    ("proof_matrix", "Proof matrix"),
    ("source_of_truth_contract", "Source of truth"),
    ("operator_evidence", "Operator evidence"),
]


def _load(gate: str) -> dict | None:
    path = ARTIFACTS / f"{gate}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def build_summary() -> tuple[str, bool]:
    rows = ["## Foundry-lite Runtime Evidence Gate", "", "| Area | Status | Problem |", "|---|---:|---|"]
    suggested: list[str] = []
    root_cause: list[str] = []
    any_fail = False

    for gate, label in _GATES:
        data = _load(gate)
        if data is None:
            rows.append(f"| {label} | ⚪ | not run (no artifact) |")
            continue
        status = data.get("status", "?")
        findings = data.get("findings", [])
        if status == "PASS":
            rows.append(f"| {label} | ✅ | |")
            continue
        any_fail = True
        first = findings[0] if findings else {}
        rows.append(f"| {label} | ❌ | {first.get('problem', 'see artifact')} |")
        if first.get("why"):
            root_cause.append(f"- **{label}:** {first['why']}")
        for finding in findings:
            suggested.extend(finding.get("suggestedFiles", []))

    if root_cause:
        rows += ["", "### Root cause hint", *root_cause]
    if suggested:
        seen = list(dict.fromkeys(suggested))
        rows += ["", "### Suggested next files", *[f"- `{f}`" for f in seen]]
    rows += [
        "",
        "### Artifacts",
        "- `artifacts/quality/proof_matrix.json` / `_summary.md`",
        "- `artifacts/quality/source_of_truth_contract.json` / `source_of_truth_summary.md`",
        "- `artifacts/quality/operator_evidence.json` / `_summary.md`",
        "",
        "### Docs",
        "- `docs/infra-tricky-matrix.json` (single proof/source-of-truth/operator-evidence matrix)",
        "- `docs/infra-ratchet.md` (lanes, proof classes, how to debug a gate)",
    ]
    return "\n".join(rows) + "\n", any_fail


def main() -> int:
    summary, any_fail = build_summary()
    print(summary)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / "runtime_root_cause_summary.md").write_text(summary, encoding="utf-8")
    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step:
        with open(step, "a", encoding="utf-8") as handle:
            handle.write(summary + "\n")
    print("runtime root-cause summary written" + (" (some gates reported findings)" if any_fail else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
