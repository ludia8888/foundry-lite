"""Shared helpers for the proof-matrix / operator-evidence / source-of-truth gates.

These gates read the single infra matrix (`docs/infra-tricky-matrix.json`) and turn
contract violations into actionable, root-cause-style output instead of opaque
assertions. Each gate emits a JSON artifact, a Markdown summary, and (in GitHub
Actions) appends to the step summary so a developer sees the likely cause and the
files to inspect immediately.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MATRIX_PATH = ROOT / "docs" / "infra-tricky-matrix.json"
PACKAGE_JSON = ROOT / "package.json"
CI_SCRIPT = ROOT / "scripts" / "ci_gate.sh"
INFRA_RATCHET_DOC = ROOT / "docs" / "infra-ratchet.md"
ARTIFACTS = ROOT / "artifacts" / "quality"

REQUIRED_PROOF_CLASSES = (
    "adapter-contract",
    "normal-path",
    "failure-injection",
    "concurrency-race",
    "retry-idempotency",
    "partial-success",
    "recovery-cleanup",
    "operator-evidence",
)

_TEST_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+(test_[A-Za-z0-9_]+)\s*\(", re.MULTILINE)


@dataclass(frozen=True)
class Finding:
    """One actionable contract violation."""

    area: str
    problem: str
    why: str = ""
    suggested_files: tuple[str, ...] = ()
    docs: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()

    def render_block(self) -> str:
        lines = [f"  ▸ {self.area}: {self.problem}"]
        if self.why:
            lines.append(f"      why: {self.why}")
        if self.suggested_files:
            lines.append(f"      inspect: {', '.join(self.suggested_files)}")
        return "\n".join(lines)


@dataclass
class GateResult:
    """Outcome of one gate, rendered to console + artifacts + step summary."""

    gate: str
    title: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings


def load_matrix() -> dict[str, Any]:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def package_scripts() -> dict[str, str]:
    return dict(json.loads(PACKAGE_JSON.read_text(encoding="utf-8")).get("scripts", {}))


def ci_script_text() -> str:
    return CI_SCRIPT.read_text(encoding="utf-8") if CI_SCRIPT.exists() else ""


def infra_ratchet_text() -> str:
    return INFRA_RATCHET_DOC.read_text(encoding="utf-8") if INFRA_RATCHET_DOC.exists() else ""


def all_test_names(root: Path = ROOT) -> set[str]:
    """Collect every `def test_*` name under tests/ (cheap, import-free existence check)."""
    names: set[str] = set()
    tests_dir = root / "tests"
    for path in tests_dir.rglob("*.py"):
        names.update(_TEST_DEF_RE.findall(path.read_text(encoding="utf-8")))
    return names


def repo_relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def active_families(matrix: dict[str, Any]) -> list[dict[str, Any]]:
    active = set(matrix.get("activeStack", []))
    return [f for f in matrix.get("families", []) if f.get("id") in active or f.get("status") == "active-covered"]


def emit(result: GateResult) -> int:
    """Print human output, write JSON + Markdown artifacts, append step summary. Return exit code."""
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    status = "PASS" if result.ok else "FAIL"
    header = f"{'✅' if result.ok else '❌'} {result.title}"
    print(header)
    for finding in result.findings:
        print(finding.render_block())

    json_path = ARTIFACTS / f"{result.gate}.json"
    json_path.write_text(
        json.dumps(
            {
                "gate": result.gate,
                "status": status,
                "count": len(result.findings),
                "findings": [
                    {
                        "area": f.area,
                        "problem": f.problem,
                        "why": f.why,
                        "suggestedFiles": list(f.suggested_files),
                        "docs": list(f.docs),
                        "tests": list(f.tests),
                    }
                    for f in result.findings
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    md = _render_markdown(result, status)
    (ARTIFACTS / f"{result.gate}_summary.md").write_text(md, encoding="utf-8")
    _append_step_summary(md)

    if result.ok:
        print(f"{result.title}: PASS")
        return 0
    print(f"\n{result.title}: FAIL ({len(result.findings)} finding(s)). See artifacts/quality/{result.gate}_summary.md")
    return 1


def _render_markdown(result: GateResult, status: str) -> str:
    icon = "✅" if result.ok else "❌"
    lines = [f"## {result.title}", "", f"**Status:** {icon} {status}", ""]
    if result.ok:
        lines.append("All contract checks passed.")
        return "\n".join(lines) + "\n"
    lines += ["| Area | Problem | Inspect |", "|---|---|---|"]
    for f in result.findings:
        files = "<br>".join(f.suggested_files) or "—"
        lines.append(f"| {f.area} | {f.problem} | {files} |")
    lines.append("")
    for f in result.findings:
        if f.why:
            lines += [f"### {f.area}", f"- **Why this matters:** {f.why}"]
            if f.tests:
                lines.append(f"- **Related tests:** {', '.join(f.tests)}")
            if f.docs:
                lines.append(f"- **Related docs:** {', '.join(f.docs)}")
            lines.append("")
    return "\n".join(lines) + "\n"


def _append_step_summary(markdown: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write(markdown + "\n")
