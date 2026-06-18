"""Verify completed tricky-failure checklist items have collectable pytest evidence.

The tricky failure checklist is allowed to contain future `[ ]` test ideas. Once
an item is marked `[x]`, though, every `test_*` name on that checked line must be
real pytest evidence that `pytest --collect-only` can see. This blocks the most
dangerous documentation drift: a completed shield claim whose named regression
test was renamed, deleted, or never added.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess  # nosec B404
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHECKLIST = ROOT / "docs" / "foundry_lite_tricky_failure_modes_checklist.md"
DEFAULT_TEST_ROOTS = (ROOT / "tests",)
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "checklist_evidence.json"

CHECKED_LINE_RE = re.compile(r"^\s*[-*]\s*\[[xX]\]")
TEST_NAME_RE = re.compile(r"\btest_[A-Za-z0-9_]+\b")


@dataclass(frozen=True)
class ChecklistTestReference:
    path: str
    line: int
    name: str
    checked_line: str


@dataclass(frozen=True)
class ChecklistEvidenceFinding:
    code: str
    path: str
    line: int
    reference: str
    message: str


@dataclass(frozen=True)
class PytestCollection:
    test_names: set[str]
    error: str | None


@dataclass(frozen=True)
class ChecklistEvidenceResult:
    checklist_path: str
    checked_references: list[ChecklistTestReference]
    collected_test_names: set[str]
    findings: list[ChecklistEvidenceFinding]


def _repo_relative(path: Path, *, root: Path = ROOT) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _resolve_path(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def extract_checked_test_references(checklist_path: Path, *, root: Path = ROOT) -> list[ChecklistTestReference]:
    references: list[ChecklistTestReference] = []
    in_fence = False
    for line_number, line in enumerate(checklist_path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence or CHECKED_LINE_RE.search(line) is None:
            continue
        for name in sorted(set(TEST_NAME_RE.findall(line))):
            references.append(
                ChecklistTestReference(
                    path=_repo_relative(checklist_path, root=root),
                    line=line_number,
                    name=name,
                    checked_line=stripped,
                )
            )
    return references


def parse_collected_test_names(output: str) -> set[str]:
    names: set[str] = set()
    for line in output.splitlines():
        for segment in line.strip().split("::"):
            base_name = segment.split("[", maxsplit=1)[0].strip()
            if TEST_NAME_RE.fullmatch(base_name):
                names.add(base_name)
    return names


def collect_pytest_test_names(root: Path = ROOT, test_roots: tuple[Path, ...] = DEFAULT_TEST_ROOTS) -> PytestCollection:
    resolved_roots = tuple(_resolve_path(root, path) for path in test_roots)
    command = [
        sys.executable,
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        "-p",
        "no:tach",
        *(str(path) for path in resolved_roots),
    ]
    # Fixed argv, shell disabled, and inputs are repo-local pytest collection
    # roots. This gate intentionally executes pytest to prove collectability.
    completed = subprocess.run(command, cwd=root, check=False, capture_output=True, text=True)  # nosec B603
    output = f"{completed.stdout}\n{completed.stderr}"
    if completed.returncode != 0:
        return PytestCollection(test_names=set(), error=_compact_collection_error(output))
    return PytestCollection(test_names=parse_collected_test_names(output), error=None)


def _compact_collection_error(output: str) -> str:
    compact = "\n".join(line.rstrip() for line in output.strip().splitlines() if line.strip())
    if len(compact) <= 4000:
        return compact
    return compact[-4000:]


def analyze_checklist_evidence(
    root: Path = ROOT,
    *,
    checklist_path: Path | None = None,
    test_roots: tuple[Path, ...] = DEFAULT_TEST_ROOTS,
    collected_test_names: set[str] | None = None,
) -> ChecklistEvidenceResult:
    checklist = _resolve_path(root, checklist_path or DEFAULT_CHECKLIST)
    findings: list[ChecklistEvidenceFinding] = []
    if not checklist.exists():
        findings.append(
            ChecklistEvidenceFinding(
                code="missing_checklist",
                path=_repo_relative(checklist, root=root),
                line=0,
                reference=_repo_relative(checklist, root=root),
                message="Tricky failure checklist is missing.",
            )
        )
        return ChecklistEvidenceResult(
            checklist_path=_repo_relative(checklist, root=root),
            checked_references=[],
            collected_test_names=set(),
            findings=findings,
        )

    references = extract_checked_test_references(checklist, root=root)
    if collected_test_names is None:
        collection = collect_pytest_test_names(root, test_roots)
        collected_names = collection.test_names
        if collection.error is not None:
            findings.append(
                ChecklistEvidenceFinding(
                    code="pytest_collection_failed",
                    path="tests",
                    line=0,
                    reference="pytest --collect-only",
                    message=collection.error,
                )
            )
    else:
        collected_names = set(collected_test_names)

    for reference in references:
        if reference.name in collected_names:
            continue
        findings.append(
            ChecklistEvidenceFinding(
                code="checked_test_not_collected",
                path=reference.path,
                line=reference.line,
                reference=reference.name,
                message="Checked checklist evidence references a test name that pytest does not collect.",
            )
        )

    return ChecklistEvidenceResult(
        checklist_path=_repo_relative(checklist, root=root),
        checked_references=references,
        collected_test_names=collected_names,
        findings=findings,
    )


def collect_findings(
    root: Path = ROOT,
    *,
    checklist_path: Path | None = None,
    test_roots: tuple[Path, ...] = DEFAULT_TEST_ROOTS,
    collected_test_names: set[str] | None = None,
) -> list[ChecklistEvidenceFinding]:
    return analyze_checklist_evidence(
        root,
        checklist_path=checklist_path,
        test_roots=test_roots,
        collected_test_names=collected_test_names,
    ).findings


def write_report(output: Path, result: ChecklistEvidenceResult) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    unique_reference_names = sorted({reference.name for reference in result.checked_references})
    report = {
        "gate": "checklist_evidence",
        "baseline": 0,
        "gate_pass": not result.findings,
        "checklist": result.checklist_path,
        "checkedReferenceCount": len(result.checked_references),
        "uniqueCheckedReferenceCount": len(unique_reference_names),
        "collectedTestNameCount": len(result.collected_test_names),
        "checkedTestNames": unique_reference_names,
        "violations": [asdict(finding) for finding in result.findings],
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(result: ChecklistEvidenceResult, output: Path) -> None:
    print("Checklist evidence gate failed:")
    for finding in result.findings:
        line = f":{finding.line}" if finding.line else ""
        print(f"- {finding.path}{line}: {finding.code}: {finding.reference} - {finding.message}")
    print(f"Report: {_repo_relative(output)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate checked tricky-failure test evidence.")
    parser.add_argument("--checklist", type=Path, default=DEFAULT_CHECKLIST)
    parser.add_argument("--test-root", action="append", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    test_roots = tuple(args.test_root) if args.test_root is not None else DEFAULT_TEST_ROOTS
    result = analyze_checklist_evidence(checklist_path=args.checklist, test_roots=test_roots)
    write_report(args.output, result)
    if result.findings:
        _print_failure(result, args.output)
        return 1
    print(f"Checklist evidence gate passed: {len(result.checked_references)} checked test references are collectable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
