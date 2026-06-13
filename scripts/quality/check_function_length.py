"""Enforces guideline §1.2/§2.2: application functions stay small.

Long functions mix validation, state change, and response shaping until bugs are
hard to localize. This gate blocks application functions above the 40-line hard
limit and keeps the zero-baseline function-length budget from regressing.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATHS = (ROOT / "libs" / "foundry_lite" / "application",)
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "function_length.json"
DEFAULT_MAX_LINES = 40
DEFAULT_WARNING_LINES = 40
DEFAULT_BASELINE: dict[str, int] = {}


@dataclass(frozen=True)
class FunctionRecord:
    path: str
    qualname: str
    line: int
    length: int


@dataclass(frozen=True)
class FunctionLengthFinding:
    code: str
    path: str
    line: int
    qualname: str
    length: int
    limit: int
    baseline_length: int | None
    message: str


def _repo_relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _python_files(paths: tuple[Path, ...]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_file() and path.suffix == ".py":
            files.append(path)
        elif path.is_dir():
            files.extend(sorted(child for child in path.rglob("*.py") if child.is_file()))
    return sorted(path for path in files if "__pycache__" not in path.parts)


def function_key(record: FunctionRecord) -> str:
    return f"{record.path}:{record.qualname}"


class _FunctionVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.stack: list[str] = []
        self.records: list[FunctionRecord] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualname = ".".join([*self.stack, node.name])
        end_lineno = node.end_lineno or node.lineno
        self.records.append(
            FunctionRecord(
                path=_repo_relative(self.path),
                qualname=qualname,
                line=node.lineno,
                length=end_lineno - node.lineno + 1,
            )
        )
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()


def collect_functions(paths: tuple[Path, ...] = DEFAULT_PATHS) -> list[FunctionRecord]:
    records: list[FunctionRecord] = []
    for path in _python_files(paths):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _FunctionVisitor(path)
        visitor.visit(tree)
        records.extend(visitor.records)
    return records


def _finding_for_record(
    record: FunctionRecord,
    *,
    baseline: dict[str, int],
    max_lines: int,
) -> FunctionLengthFinding | None:
    if record.length <= max_lines:
        return None
    key = function_key(record)
    baseline_length = baseline.get(key)
    if baseline_length is None:
        return FunctionLengthFinding(
            code="function_over_limit",
            path=record.path,
            line=record.line,
            qualname=record.qualname,
            length=record.length,
            limit=max_lines,
            baseline_length=None,
            message="Function exceeds the line limit and is not in the shrinking baseline",
        )
    if record.length > baseline_length:
        return FunctionLengthFinding(
            code="baseline_function_grew",
            path=record.path,
            line=record.line,
            qualname=record.qualname,
            length=record.length,
            limit=max_lines,
            baseline_length=baseline_length,
            message="Existing over-limit function grew beyond its recorded baseline",
        )
    return None


def collect_findings(
    paths: tuple[Path, ...] = DEFAULT_PATHS,
    *,
    baseline: dict[str, int] = DEFAULT_BASELINE,
    max_lines: int = DEFAULT_MAX_LINES,
) -> list[FunctionLengthFinding]:
    findings: list[FunctionLengthFinding] = []
    for record in collect_functions(paths):
        finding = _finding_for_record(record, baseline=baseline, max_lines=max_lines)
        if finding is not None:
            findings.append(finding)
    return findings


def write_report(
    output: Path,
    records: list[FunctionRecord],
    findings: list[FunctionLengthFinding],
    *,
    baseline: dict[str, int],
    max_lines: int,
    warning_lines: int,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    over_limit = [record for record in records if record.length > max_lines]
    warnings = [record for record in records if record.length > warning_lines]
    present_baseline = {function_key(record) for record in records if function_key(record) in baseline}
    report = {
        "count": len(findings),
        "baseline": {
            "allowed_over_limit": len(baseline),
            "max_lines": max_lines,
            "warning_lines": warning_lines,
        },
        "gate_pass": not findings,
        "violations": [asdict(finding) for finding in findings],
        "summary": {
            "functions_scanned": len(records),
            "warning_count": len(warnings),
            "over_limit_count": len(over_limit),
            "baseline_present": len(present_baseline),
            "baseline_removed": len(set(baseline) - present_baseline),
            "longest_function_lines": max((record.length for record in records), default=0),
        },
        "over_limit_functions": [asdict(record) for record in over_limit],
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(findings: list[FunctionLengthFinding], output: Path) -> None:
    print("Function length gate failed:")
    for finding in findings:
        baseline = f", baseline={finding.baseline_length}" if finding.baseline_length is not None else ""
        print(
            f"- {finding.path}:{finding.line} {finding.qualname} "
            f"{finding.length}/{finding.limit}{baseline}: {finding.message}"
        )
    print(f"Report: {_repo_relative(output)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check application function length hard limit.")
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-lines", type=int, default=DEFAULT_MAX_LINES)
    parser.add_argument("--warning-lines", type=int, default=DEFAULT_WARNING_LINES)
    args = parser.parse_args(argv)

    paths = tuple(args.paths) if args.paths else DEFAULT_PATHS
    records = collect_functions(paths)
    findings = collect_findings(paths, baseline=DEFAULT_BASELINE, max_lines=args.max_lines)
    write_report(
        args.output,
        records,
        findings,
        baseline=DEFAULT_BASELINE,
        max_lines=args.max_lines,
        warning_lines=args.warning_lines,
    )
    if findings:
        _print_failure(findings, args.output)
        return 1
    print(f"Function length gate passed: no application function exceeds {args.max_lines} lines.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
