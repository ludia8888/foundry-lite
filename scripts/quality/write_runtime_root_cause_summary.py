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
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts" / "quality"

_GATES = [
    ("proof_matrix", "Proof matrix"),
    ("source_of_truth_contract", "Source of truth"),
    ("operator_evidence", "Operator evidence"),
]


def _load(gate: str) -> dict[str, Any] | None:
    path = ARTIFACTS / f"{gate}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _load_runtime_failure() -> dict[str, Any] | None:
    path = ARTIFACTS / "runtime_lane_failure.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"failedStep": "unknown", "exitCode": "unknown"}
    return data if isinstance(data, dict) else {"failedStep": "unknown", "exitCode": "unknown"}


def _runtime_failure_rows(runtime_failure: dict[str, Any] | None) -> tuple[list[str], list[str], bool]:
    if runtime_failure is None:
        return [], [], False
    failed_step = runtime_failure.get("failedStep", "unknown")
    exit_code = runtime_failure.get("exitCode", "unknown")
    return (
        [f"| Runtime lane | ❌ | failed step: {failed_step} (exit {exit_code}) |"],
        [f"- **Runtime lane:** inspect the focused gate for `{failed_step}` first."],
        True,
    )


def _gate_status_rows() -> tuple[list[str], list[str], list[str], bool]:
    rows: list[str] = []
    root_cause: list[str] = []
    suggested: list[str] = []
    failed = False
    for gate, label in _GATES:
        gate_rows, gate_root_cause, gate_suggested, gate_failed = _summarize_gate(label, _load(gate))
        rows.extend(gate_rows)
        root_cause.extend(gate_root_cause)
        suggested.extend(gate_suggested)
        failed = failed or gate_failed
    return rows, root_cause, suggested, failed


def _summarize_gate(label: str, data: dict[str, Any] | None) -> tuple[list[str], list[str], list[str], bool]:
    if data is None:
        return [f"| {label} | ⚪ | not run (no artifact) |"], [], [], False
    findings = data.get("findings", [])
    if data.get("status") == "PASS":
        return [f"| {label} | ✅ | |"], [], [], False
    first = findings[0] if findings else {}
    root_cause = [f"- **{label}:** {first['why']}"] if first.get("why") else []
    suggested = [file for finding in findings for file in finding.get("suggestedFiles", [])]
    return [f"| {label} | ❌ | {first.get('problem', 'see artifact')} |"], root_cause, suggested, True


def _append_context_sections(rows: list[str], root_cause: list[str], suggested: list[str]) -> None:
    if root_cause:
        rows += ["", "### Root cause hint", *root_cause]
    if suggested:
        seen = list(dict.fromkeys(suggested))
        rows += ["", "### Suggested next files", *[f"- `{f}`" for f in seen]]


def _append_reference_sections(rows: list[str]) -> None:
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


def build_summary() -> tuple[str, bool]:
    rows = ["## Foundry-lite Runtime Evidence Gate", "", "| Area | Status | Problem |", "|---|---:|---|"]
    runtime_rows, runtime_root_cause, runtime_failed = _runtime_failure_rows(_load_runtime_failure())
    gate_rows, gate_root_cause, gate_suggested, gate_failed = _gate_status_rows()
    rows.extend(runtime_rows)
    rows.extend(gate_rows)
    _append_context_sections(rows, runtime_root_cause + gate_root_cause, gate_suggested)
    _append_reference_sections(rows)
    any_fail = runtime_failed or gate_failed
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
