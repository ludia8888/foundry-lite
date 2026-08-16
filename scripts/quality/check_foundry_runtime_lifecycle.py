"""Enforce guideline §2.3/§4.3: composition roots must close or transfer FoundryLite ownership."""

from __future__ import annotations

import argparse
import ast
import json
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATHS = (
    ROOT / "apps" / "api",
    ROOT / "apps" / "cli",
    ROOT / "apps" / "worker",
    ROOT / "scripts",
)
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "foundry_runtime_lifecycle.json"


@dataclass(frozen=True)
class FoundryRuntimeLifecycleFinding:
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


def _is_foundry_constructor(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "FoundryLite"


def _scope_nodes(scope: ast.AST) -> list[ast.AST]:
    nodes: list[ast.AST] = []
    pending = list(ast.iter_child_nodes(scope))
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        nodes.append(node)
        pending.extend(ast.iter_child_nodes(node))
    return nodes


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _scope(node: ast.AST, parents: dict[ast.AST, ast.AST], tree: ast.Module) -> ast.AST:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            return current
    return tree


def _assigned_name(value: ast.AST, parents: dict[ast.AST, ast.AST]) -> str | None:
    parent = parents.get(value)
    if isinstance(parent, ast.Assign):
        return _plain_assignment_name(parent, value)
    if isinstance(parent, ast.AnnAssign):
        return _named_assignment_target(parent.target) if parent.value is value else None
    if isinstance(parent, ast.NamedExpr):
        return _named_assignment_target(parent.target) if parent.value is value else None
    return None


def _plain_assignment_name(parent: ast.Assign, value: ast.AST) -> str | None:
    if parent.value is not value or len(parent.targets) != 1:
        return None
    return _named_assignment_target(parent.targets[0])


def _named_assignment_target(target: ast.AST) -> str | None:
    return target.id if isinstance(target, ast.Name) else None


def _value_transfers_name(value: ast.AST, name: str) -> bool:
    if isinstance(value, ast.Name):
        return value.id == name
    if isinstance(value, (ast.Tuple, ast.List, ast.Set)):
        return any(_value_transfers_name(item, name) for item in value.elts)
    if isinstance(value, ast.Dict):
        return any(_value_transfers_name(item, name) for item in value.values)
    if not isinstance(value, ast.Call):
        return False
    return any(keyword.arg == "foundry" and _value_transfers_name(keyword.value, name) for keyword in value.keywords)


def _is_direct_return(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    parent = parents.get(node)
    return isinstance(parent, ast.Return) and parent.value is node


def _finally_closes_name(node: ast.Try, name: str) -> bool:
    for statement in node.finalbody:
        for child in ast.walk(statement):
            if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
                continue
            receiver = child.func.value
            if child.func.attr == "close" and isinstance(receiver, ast.Name) and receiver.id == name:
                return True
    return False


def _next_statement(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.stmt | None:
    statement: ast.AST = node
    while statement in parents and not isinstance(statement, ast.stmt):
        statement = parents[statement]
    if not isinstance(statement, ast.stmt) or statement not in parents:
        return None
    container = parents[statement]
    for _field, value in ast.iter_fields(container):
        if not isinstance(value, list) or statement not in value:
            continue
        index = value.index(statement)
        candidate = value[index + 1] if index + 1 < len(value) else None
        return candidate if isinstance(candidate, ast.stmt) else None
    return None


def _is_immediately_guarded(node: ast.AST, name: str, parents: dict[ast.AST, ast.AST]) -> bool:
    next_statement = _next_statement(node, parents)
    return isinstance(next_statement, ast.Try) and _finally_closes_name(next_statement, name)


def _return_transfers_name(statement: ast.stmt | None, name: str) -> bool:
    return (
        isinstance(statement, ast.Return)
        and statement.value is not None
        and _value_transfers_name(statement.value, name)
    )


def _handler_closes_and_raises(handler: ast.ExceptHandler, name: str) -> bool:
    has_close = False
    has_raise = False
    for node in _scope_nodes(handler):
        if isinstance(node, ast.Raise):
            has_raise = True
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        receiver = node.func.value
        if node.func.attr == "close" and isinstance(receiver, ast.Name) and receiver.id == name:
            has_close = True
    return has_close and has_raise


def _statement_transfers_name(statement: ast.stmt, name: str) -> bool:
    value: ast.AST | None = None
    if isinstance(statement, ast.Return):
        value = statement.value
    elif isinstance(statement, ast.Assign):
        value = statement.value
    elif isinstance(statement, ast.AnnAssign):
        value = statement.value
    elif isinstance(statement, ast.Expr):
        value = statement.value
    return value is not None and _value_transfers_name(value, name)


def _try_protects_transfer(node: ast.Try, name: str, parents: dict[ast.AST, ast.AST]) -> bool:
    if not node.handlers or not all(_handler_closes_and_raises(handler, name) for handler in node.handlers):
        return False
    if any(_statement_transfers_name(item, name) for item in node.body):
        return True
    return _return_transfers_name(_next_statement(node, parents), name)


def _has_safe_lifecycle(call: ast.Call, parents: dict[ast.AST, ast.AST], scope: ast.AST) -> bool:
    if _is_direct_return(call, parents):
        return True
    name = _assigned_name(call, parents)
    if name is None:
        return False
    next_statement = _next_statement(call, parents)
    if _return_transfers_name(next_statement, name):
        return True
    if not isinstance(next_statement, ast.Try):
        return False
    return _finally_closes_name(next_statement, name) or _try_protects_transfer(next_statement, name, parents)


def collect_findings(paths: tuple[Path, ...] = DEFAULT_PATHS) -> list[FoundryRuntimeLifecycleFinding]:
    findings: list[FoundryRuntimeLifecycleFinding] = []
    for path in _python_files(paths):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = _parents(tree)
        for node in ast.walk(tree):
            if not _is_foundry_constructor(node):
                continue
            call = node
            assert isinstance(call, ast.Call)
            if _has_safe_lifecycle(call, parents, _scope(call, parents, tree)):
                continue
            findings.append(
                FoundryRuntimeLifecycleFinding(
                    code="foundry_runtime_not_closed_or_transferred",
                    path=_repo_relative(path),
                    line=call.lineno,
                    column=call.col_offset + 1,
                    message=(
                        "FoundryLite must be closed in a finally block or returned as an explicit ownership transfer"
                    ),
                )
            )
    return findings


def write_report(output: Path, findings: list[FoundryRuntimeLifecycleFinding]) -> None:
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
    parser = argparse.ArgumentParser(description="Reject leaked FoundryLite composition-root runtimes.")
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    findings = collect_findings(tuple(args.paths) if args.paths else DEFAULT_PATHS)
    write_report(args.output, findings)
    if findings:
        print("Foundry runtime lifecycle gate failed:")
        for finding in findings:
            print(f"- {finding.path}:{finding.line}:{finding.column} {finding.message}")
        return 1
    print("Foundry runtime lifecycle gate passed: all runtimes are closed or explicitly transferred.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
