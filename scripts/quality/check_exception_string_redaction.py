"""Block raw exception strings outside the shared redaction boundary.

Exception messages from databases, SDKs, connectors, model providers, and file
systems can contain credentials or request content.  Application/API/worker
code must pass conventional exception values through ``scrub_error_text`` (or
the standard ``runtime_error_payload`` builder) before persisting or exposing
their string form.
"""

from __future__ import annotations

import argparse
import ast
import json
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATHS = (ROOT / "apps", ROOT / "libs" / "foundry_lite" / "application")
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "exception_string_redaction.json"
CONVENTIONAL_EXCEPTION_NAMES = frozenset({"exc", "error", "exception", "failure"})
EXCEPTION_TEXT_ATTRIBUTES = frozenset({"message", "detail", "args"})
SAFE_BOUNDARIES = frozenset({"scrub_error_text", "runtime_error_payload"})


@dataclass(frozen=True)
class ExceptionStringFinding:
    code: str
    path: str
    line: int
    column: int
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
    return sorted(files)


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _caught_exception_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and isinstance(node.name, str):
            names.add(node.name)
    return names


def _is_exception_string_call(node: ast.AST, exception_names: set[str]) -> bool:
    return (
        isinstance(node, ast.Call)
        and _call_name(node.func) in {"str", "repr"}
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id in exception_names
    )


def _is_exception_formatted_value(node: ast.AST, caught_exception_names: set[str]) -> bool:
    return (
        isinstance(node, ast.FormattedValue)
        and isinstance(node.value, ast.Name)
        and node.value.id in caught_exception_names
    )


def _is_exception_text_attribute(node: ast.AST, exception_names: set[str]) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr in EXCEPTION_TEXT_ATTRIBUTES
        and isinstance(node.value, ast.Name)
        and node.value.id in exception_names
    )


def _contains_exception_name(node: ast.AST, exception_names: set[str]) -> bool:
    return any(isinstance(child, ast.Name) and child.id in exception_names for child in ast.walk(node))


def _is_exception_format_call(node: ast.AST, exception_names: set[str]) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
        and any(_contains_exception_name(argument, exception_names) for argument in (*node.args, *node.keywords))
    )


def _is_exception_percent_format(node: ast.AST, exception_names: set[str]) -> bool:
    return (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Mod)
        and _contains_exception_name(node.right, exception_names)
    )


def _is_inside_safe_boundary(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.Call) and _call_name(current.func) in SAFE_BOUNDARIES:
            return True
        if isinstance(current, (ast.Assign, ast.AnnAssign, ast.Return, ast.Raise, ast.Expr)):
            return False
        current = parents.get(current)
    return False


def _is_control_only_exception_attribute(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """Allow inspecting an exception value without serializing the value itself."""

    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.Compare):
            return True
        if (
            isinstance(current, ast.Call)
            and _call_name(current.func) == "len"
            and isinstance(node, ast.Attribute)
            and node.attr == "args"
        ):
            return True
        if isinstance(current, (ast.Assign, ast.AnnAssign, ast.Return, ast.Raise, ast.Expr)):
            return False
        current = parents.get(current)
    return False


def _parent_map(tree: ast.Module) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _raw_exception_kind(
    node: ast.AST,
    *,
    caught_names: set[str],
    string_exception_names: set[str],
) -> tuple[bool, bool]:
    is_attribute = _is_exception_text_attribute(node, string_exception_names)
    is_raw = any(
        (
            _is_exception_string_call(node, string_exception_names),
            _is_exception_formatted_value(node, caught_names),
            is_attribute,
            _is_exception_format_call(node, string_exception_names),
            _is_exception_percent_format(node, string_exception_names),
        )
    )
    return is_raw, is_attribute


def _finding_for_node(
    node: ast.AST,
    *,
    path: Path,
    parents: dict[ast.AST, ast.AST],
    caught_names: set[str],
    string_exception_names: set[str],
) -> ExceptionStringFinding | None:
    is_raw, is_attribute = _raw_exception_kind(
        node,
        caught_names=caught_names,
        string_exception_names=string_exception_names,
    )
    if not is_raw or _is_inside_safe_boundary(node, parents):
        return None
    if is_attribute and _is_control_only_exception_attribute(node, parents):
        return None
    return ExceptionStringFinding(
        code="raw_exception_string",
        path=_repo_relative(path),
        line=getattr(node, "lineno", 0),
        column=getattr(node, "col_offset", 0) + 1,
        message="exception strings must pass through scrub_error_text or runtime_error_payload",
    )


def collect_findings(paths: tuple[Path, ...] = DEFAULT_PATHS) -> list[ExceptionStringFinding]:
    findings: list[ExceptionStringFinding] = []
    for path in _python_files(paths):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = _parent_map(tree)
        caught_names = _caught_exception_names(tree)
        string_exception_names = set(CONVENTIONAL_EXCEPTION_NAMES) | caught_names
        for node in ast.walk(tree):
            finding = _finding_for_node(
                node,
                path=path,
                parents=parents,
                caught_names=caught_names,
                string_exception_names=string_exception_names,
            )
            if finding is not None:
                findings.append(finding)
    return findings


def write_report(output: Path, findings: list[ExceptionStringFinding]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "count": len(findings),
                "baseline": 0,
                "gate_pass": not findings,
                "violations": [asdict(finding) for finding in findings],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reject raw exception strings outside redaction boundaries.")
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    findings = collect_findings(tuple(args.paths) if args.paths else DEFAULT_PATHS)
    write_report(args.output, findings)
    if findings:
        print("Exception string redaction gate failed:")
        for finding in findings:
            print(f"- {finding.path}:{finding.line}:{finding.column} {finding.message}")
        return 1
    print("Exception string redaction gate passed: raw exception strings are not exposed or persisted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
