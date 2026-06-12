"""Block broad Any usage at application and app entrypoint boundaries.

Guideline #28 allows dynamic values only at deliberate adapter/runtime edges.
The application layer, API, CLI, and worker entrypoints should instead accept
`object` and validate shapes before using values. This gate freezes that
boundary at zero broad Any usages.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATHS = (
    ROOT / "libs" / "foundry_lite" / "application",
    ROOT / "apps" / "api",
    ROOT / "apps" / "cli",
    ROOT / "apps" / "worker",
)
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "application_any_budget.json"
DEFAULT_MAX_COUNT = 0


@dataclass(frozen=True)
class AnyUsageRecord:
    path: str
    line: int
    column: int
    kind: str


@dataclass(frozen=True)
class AnyBudgetFinding:
    code: str
    count: int
    baseline: int
    message: str


def _repo_relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _python_files(paths: tuple[Path, ...]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if not path.exists():
            continue
        if path.is_file() and path.suffix == ".py":
            files.append(path)
        elif path.is_dir():
            files.extend(sorted(child for child in path.rglob("*.py") if child.is_file()))
    return sorted(path for path in files if "__pycache__" not in path.parts)


def _attribute_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _attribute_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


class _AnyUsageVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.records: list[AnyUsageRecord] = []

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module in {"typing", "typing_extensions"}:
            for alias in node.names:
                if alias.name == "Any":
                    self._record(node, "typing_any_import")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id == "Any":
            self._record(node, "any_name")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if _attribute_name(node) in {"typing.Any", "typing_extensions.Any"}:
            self._record(node, "typing_any_attribute")
        self.generic_visit(node)

    def _record(self, node: ast.AST, kind: str) -> None:
        self.records.append(
            AnyUsageRecord(
                path=_repo_relative(self.path),
                line=getattr(node, "lineno", 1),
                column=getattr(node, "col_offset", 0),
                kind=kind,
            )
        )


def collect_records(paths: tuple[Path, ...] = DEFAULT_PATHS) -> list[AnyUsageRecord]:
    records: list[AnyUsageRecord] = []
    for path in _python_files(paths):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _AnyUsageVisitor(path)
        visitor.visit(tree)
        records.extend(visitor.records)
    return records


def collect_findings(
    records: list[AnyUsageRecord],
    *,
    max_count: int = DEFAULT_MAX_COUNT,
) -> list[AnyBudgetFinding]:
    if len(records) <= max_count:
        return []
    return [
        AnyBudgetFinding(
            code="application_any_budget_exceeded",
            count=len(records),
            baseline=max_count,
            message="application/app boundary broad Any usage exceeded the zero baseline",
        )
    ]


def write_report(
    output: Path,
    records: list[AnyUsageRecord],
    findings: list[AnyBudgetFinding],
    *,
    max_count: int = DEFAULT_MAX_COUNT,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "count": len(findings),
        "baseline": {"max_count": max_count},
        "gate_pass": not findings,
        "summary": {
            "total": len(records),
            "remaining_budget": max_count - len(records),
        },
        "violations": [asdict(finding) for finding in findings],
        "any_usages": [asdict(record) for record in records],
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(findings: list[AnyBudgetFinding], records: list[AnyUsageRecord], output: Path) -> None:
    print("Application Any budget gate failed:")
    for finding in findings:
        print(f"- {finding.count}/{finding.baseline} {finding.code}: {finding.message}")
    for record in records[:20]:
        print(f"  {record.path}:{record.line}:{record.column} {record.kind}")
    print(f"Report: {_repo_relative(output)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check application/app broad Any usage budget.")
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-count", type=int, default=DEFAULT_MAX_COUNT)
    args = parser.parse_args(argv)

    records = collect_records(tuple(args.paths) if args.paths else DEFAULT_PATHS)
    findings = collect_findings(records, max_count=args.max_count)
    write_report(args.output, records, findings, max_count=args.max_count)
    if findings:
        _print_failure(findings, records, args.output)
        return 1
    print(f"Application Any budget passed ({len(records)}/{args.max_count}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
