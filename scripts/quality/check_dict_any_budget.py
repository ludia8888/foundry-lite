"""Enforces guideline §5.2/§18.3: dict[str, Any] signatures shrink over time.

`dict[str, Any]` is useful at true JSON/adapter boundaries, but it also lets
schema drift hide in the application layer. This gate counts dict[str, Any] in
application function signatures, freezes the current count as a shrinking
baseline, and blocks total or layer-level budget growth.
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
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "dict_any_budget.json"
DEFAULT_TOTAL_BASELINE = 0
DEFAULT_CATEGORY_BASELINE = {
    "application_helpers": 0,
    "core_facade": 0,
    "ports": 0,
    "services": 0,
}


@dataclass(frozen=True)
class DictAnyRecord:
    path: str
    qualname: str
    line: int
    count: int
    category: str


@dataclass(frozen=True)
class DictAnyFinding:
    code: str
    category: str
    count: int
    baseline: int
    message: str


@dataclass(frozen=True)
class DictAnyMetrics:
    total: int
    by_category: dict[str, int]
    records: list[DictAnyRecord]


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


def _annotation_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _annotation_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _is_str_annotation(node: ast.AST) -> bool:
    return _annotation_name(node) == "str"


def _is_any_annotation(node: ast.AST) -> bool:
    return _annotation_name(node) in {"Any", "typing.Any"}


def _is_dict_any_annotation(node: ast.AST) -> bool:
    if not isinstance(node, ast.Subscript):
        return False
    if _annotation_name(node.value) not in {"dict", "Dict", "typing.Dict"}:
        return False
    if not isinstance(node.slice, ast.Tuple) or len(node.slice.elts) != 2:
        return False
    key, value = node.slice.elts
    return _is_str_annotation(key) and _is_any_annotation(value)


def _dict_any_count(annotation: ast.AST | None) -> int:
    if annotation is None:
        return 0
    return sum(1 for node in ast.walk(annotation) if _is_dict_any_annotation(node))


def _signature_dict_any_count(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    count = 0
    for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
        count += _dict_any_count(arg.annotation)
    if node.args.vararg is not None:
        count += _dict_any_count(node.args.vararg.annotation)
    if node.args.kwarg is not None:
        count += _dict_any_count(node.args.kwarg.annotation)
    return count + _dict_any_count(node.returns)


def _category_for_path(path: Path) -> str:
    parts = path.parts
    if "ports" in parts:
        return "ports"
    if "services" in parts:
        return "services"
    if "facades" in parts or path.name == "foundry.py":
        return "core_facade"
    return "application_helpers"


class _SignatureVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.stack: list[str] = []
        self.records: list[DictAnyRecord] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        count = _signature_dict_any_count(node)
        if count:
            self.records.append(
                DictAnyRecord(
                    path=_repo_relative(self.path),
                    qualname=".".join([*self.stack, node.name]),
                    line=node.lineno,
                    count=count,
                    category=_category_for_path(self.path),
                )
            )
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()


def collect_records(paths: tuple[Path, ...] = DEFAULT_PATHS) -> list[DictAnyRecord]:
    records: list[DictAnyRecord] = []
    for path in _python_files(paths):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _SignatureVisitor(path)
        visitor.visit(tree)
        records.extend(visitor.records)
    return records


def collect_metrics(paths: tuple[Path, ...] = DEFAULT_PATHS) -> DictAnyMetrics:
    records = collect_records(paths)
    by_category: dict[str, int] = {}
    for record in records:
        by_category[record.category] = by_category.get(record.category, 0) + record.count
    return DictAnyMetrics(
        total=sum(record.count for record in records),
        by_category=by_category,
        records=records,
    )


def collect_findings(
    metrics: DictAnyMetrics,
    *,
    total_baseline: int = DEFAULT_TOTAL_BASELINE,
    category_baseline: dict[str, int] = DEFAULT_CATEGORY_BASELINE,
) -> list[DictAnyFinding]:
    findings: list[DictAnyFinding] = []
    if metrics.total > total_baseline:
        findings.append(
            DictAnyFinding(
                code="dict_any_total_budget_exceeded",
                category="total",
                count=metrics.total,
                baseline=total_baseline,
                message="dict[str, Any] usage in application signatures exceeded the total baseline",
            )
        )
    for category, count in sorted(metrics.by_category.items()):
        baseline = category_baseline.get(category, 0)
        if count > baseline:
            findings.append(
                DictAnyFinding(
                    code="dict_any_category_budget_exceeded",
                    category=category,
                    count=count,
                    baseline=baseline,
                    message=f"dict[str, Any] usage in {category} signatures exceeded its baseline",
                )
            )
    return findings


def write_report(
    output: Path,
    metrics: DictAnyMetrics,
    findings: list[DictAnyFinding],
    *,
    total_baseline: int = DEFAULT_TOTAL_BASELINE,
    category_baseline: dict[str, int] = DEFAULT_CATEGORY_BASELINE,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "count": len(findings),
        "baseline": {
            "total": total_baseline,
            "by_category": category_baseline,
        },
        "gate_pass": not findings,
        "violations": [asdict(finding) for finding in findings],
        "summary": {
            "total": metrics.total,
            "by_category": metrics.by_category,
            "signature_count": len(metrics.records),
            "remaining_budget": total_baseline - metrics.total,
        },
        "dict_any_signatures": [asdict(record) for record in metrics.records],
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(findings: list[DictAnyFinding], output: Path) -> None:
    print("dict[str, Any] budget gate failed:")
    for finding in findings:
        print(f"- {finding.category}: {finding.count}/{finding.baseline} {finding.code}: {finding.message}")
    print(f"Report: {_repo_relative(output)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check dict[str, Any] signature budget.")
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    metrics = collect_metrics(tuple(args.paths) if args.paths else DEFAULT_PATHS)
    findings = collect_findings(metrics)
    write_report(args.output, metrics, findings)
    if findings:
        _print_failure(findings, args.output)
        return 1
    print("dict[str, Any] budget gate passed: application signature usage stayed within baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
