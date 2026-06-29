"""Enforces guideline §2.1: boolean values read like clear questions/state."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATHS = (ROOT / "libs", ROOT / "apps", ROOT / "scripts")
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "boolean_naming.json"
ALLOWED_PREFIXES = (
    "allow_",
    "can_",
    "confirm_",
    "enable_",
    "has_",
    "include_",
    "is_",
    "require_",
    "requires_",
    "should_",
    "simulate_",
    "skip_",
    "use_",
)
ALLOWED_CAMEL_PREFIXES = (
    "allow",
    "can",
    "confirm",
    "enable",
    "has",
    "include",
    "is",
    "require",
    "requires",
    "should",
    "simulate",
    "skip",
    "use",
)
ALLOWED_EXACT_NAMES = {
    "allowed",
    "deleted",
    "disabled",
    "editable",
    "enabled",
    "fresh",
    "gate_pass",
    "indexed",
    "initialized",
    "nullable",
    "searchable",
}
ALLOWED_SUFFIXES = (
    "_allowed",
    "_deleted",
    "_disabled",
    "_enabled",
    "_ready",
    "_visible",
)


@dataclass(frozen=True)
class BooleanNamingFinding:
    path: str
    line: int
    name: str
    owner: str
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
            files.extend(child for child in path.rglob("*.py") if child.is_file())
    return sorted(path for path in files if "__pycache__" not in path.parts)


def _annotation_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _annotation_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _is_none_annotation(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _is_bool_name(node: ast.AST) -> bool:
    return _annotation_name(node) in {"bool", "builtins.bool"}


def _is_optional_bool_union(node: ast.AST) -> bool:
    if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.BitOr):
        return False
    sides = (node.left, node.right)
    return any(_is_bool_name(side) for side in sides) and all(
        _is_bool_name(side) or _is_none_annotation(side) for side in sides
    )


def _is_optional_bool_subscript(node: ast.AST) -> bool:
    if not isinstance(node, ast.Subscript):
        return False
    return _annotation_name(node.value) in {"Optional", "typing.Optional"} and _is_bool_name(node.slice)


def _is_boolean_annotation(node: ast.AST | None) -> bool:
    if node is None:
        return False
    return _is_bool_name(node) or _is_optional_bool_union(node) or _is_optional_bool_subscript(node)


def _is_allowed_boolean_name(name: str) -> bool:
    return (
        name in ALLOWED_EXACT_NAMES
        or name.startswith(ALLOWED_PREFIXES)
        or name.endswith(ALLOWED_SUFFIXES)
        or _has_allowed_camel_prefix(name)
    )


def _has_allowed_camel_prefix(name: str) -> bool:
    return any(_starts_with_camel_prefix(name, prefix) for prefix in ALLOWED_CAMEL_PREFIXES)


def _starts_with_camel_prefix(name: str, prefix: str) -> bool:
    return len(name) > len(prefix) and name.startswith(prefix) and name[len(prefix)].isupper()


class _BooleanNamingVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.stack: list[str] = []
        self.findings: list[BooleanNamingFinding] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name) and _is_boolean_annotation(node.annotation):
            self._add_finding(node.target.id, node.lineno)
        self.generic_visit(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.stack.append(node.name)
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            if arg.arg not in {"self", "cls"} and _is_boolean_annotation(arg.annotation):
                self._add_finding(arg.arg, arg.lineno)
        if node.args.vararg is not None and _is_boolean_annotation(node.args.vararg.annotation):
            self._add_finding(node.args.vararg.arg, node.args.vararg.lineno)
        if node.args.kwarg is not None and _is_boolean_annotation(node.args.kwarg.annotation):
            self._add_finding(node.args.kwarg.arg, node.args.kwarg.lineno)
        self.generic_visit(node)
        self.stack.pop()

    def _add_finding(self, name: str, line: int) -> None:
        if _is_allowed_boolean_name(name):
            return
        self.findings.append(
            BooleanNamingFinding(
                path=_repo_relative(self.path),
                line=line,
                name=name,
                owner=".".join(self.stack) if self.stack else "<module>",
                message=(
                    "boolean arguments and fields must read like a question or explicit state "
                    "(is_/has_/can_/should_/include_/allow_/enabled/deleted/etc.)"
                ),
            )
        )


def collect_findings(paths: tuple[Path, ...] = DEFAULT_PATHS) -> list[BooleanNamingFinding]:
    findings: list[BooleanNamingFinding] = []
    for path in _python_files(paths):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _BooleanNamingVisitor(path)
        visitor.visit(tree)
        findings.extend(visitor.findings)
    return findings


def write_report(output: Path, findings: list[BooleanNamingFinding]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "count": len(findings),
        "baseline": 0,
        "gate_pass": not findings,
        "violations": [asdict(finding) for finding in findings],
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(findings: list[BooleanNamingFinding], output: Path) -> None:
    print("Boolean naming gate failed:")
    for finding in findings:
        print(f"- {finding.path}:{finding.line} {finding.owner}.{finding.name}: {finding.message}")
    print(f"Report: {_repo_relative(output)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check boolean argument and field names.")
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    findings = collect_findings(tuple(args.paths) if args.paths else DEFAULT_PATHS)
    write_report(args.output, findings)
    if findings:
        _print_failure(findings, args.output)
        return 1
    print("Boolean naming gate passed: boolean arguments and fields read clearly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
