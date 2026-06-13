"""Enforces guideline §10.2: tenant-scoped writes must carry tenant_id."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATHS = (ROOT / "libs" / "foundry_lite" / "infrastructure" / "repositories",)
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "tenant_write_guard.json"
MUTATION_OPERATIONS = {"insert", "update", "delete"}


@dataclass(frozen=True)
class TenantWriteFinding:
    path: str
    line: int
    operation: str
    table: str
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


def _func_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _table_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _is_tenant_column(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and _func_name(node.func) == "Column"
        and bool(node.args)
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "tenant_id"
    )


def tenant_scoped_tables(
    schema_path: Path = ROOT / "libs" / "foundry_lite" / "infrastructure" / "schema.py",
) -> set[str]:
    tree = ast.parse(schema_path.read_text(encoding="utf-8"), filename=str(schema_path))
    tables: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        if _func_name(node.value.func) != "Table" or not any(_is_tenant_column(arg) for arg in node.value.args):
            continue
        tables.update(target.id for target in node.targets if isinstance(target, ast.Name))
    return tables


def _call_chain(node: ast.AST) -> list[ast.Call]:
    chain: list[ast.Call] = []
    current = node
    while isinstance(current, ast.Call):
        chain.append(current)
        if not isinstance(current.func, ast.Attribute):
            break
        current = current.func.value
    return chain


def _base_mutation(chain: list[ast.Call]) -> tuple[str, str | None, int] | None:
    for call in reversed(chain):
        operation = _func_name(call.func)
        if operation in MUTATION_OPERATIONS and call.args:
            return operation, _table_name(call.args[0]), call.lineno
    return None


def _mentions_tenant_id(node: ast.AST) -> bool:
    return any(
        (isinstance(child, ast.Attribute) and child.attr == "tenant_id")
        or (isinstance(child, ast.keyword) and child.arg == "tenant_id")
        or (isinstance(child, ast.Constant) and child.value == "tenant_id")
        for child in ast.walk(node)
    )


def _where_has_tenant_guard(chain: list[ast.Call]) -> bool:
    return any(
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "where"
        and any(_mentions_tenant_id(arg) for arg in call.args)
        for call in chain
    )


def _values_has_tenant_id(chain: list[ast.Call]) -> bool:
    return any(
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "values"
        and (
            any(keyword.arg == "tenant_id" for keyword in call.keywords)
            or any(_mentions_tenant_id(arg) for arg in call.args)
        )
        for call in chain
    )


def _execute_payload(node: ast.Call) -> ast.AST | None:
    if isinstance(node.func, ast.Attribute) and node.func.attr == "execute" and node.args:
        return node.args[0]
    return None


def _finding_for_payload(
    path: Path,
    payload: ast.AST,
    tenant_tables: set[str],
) -> TenantWriteFinding | None:
    chain = _call_chain(payload)
    mutation = _base_mutation(chain)
    if mutation is None:
        return None
    operation, table, line = mutation
    if operation == "insert" and (table not in tenant_tables or _values_has_tenant_id(chain)):
        return None
    if operation in {"update", "delete"} and _where_has_tenant_guard(chain):
        return None
    return TenantWriteFinding(
        path=_repo_relative(path),
        line=line,
        operation=operation,
        table=table or "<dynamic>",
        message="tenant-scoped writes must include tenant_id in values or where clauses",
    )


def collect_findings(
    paths: tuple[Path, ...] = DEFAULT_PATHS,
    *,
    tenant_tables: set[str] | None = None,
) -> list[TenantWriteFinding]:
    scoped_tables = tenant_tables if tenant_tables is not None else tenant_scoped_tables()
    findings: list[TenantWriteFinding] = []
    for path in _python_files(paths):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            payload = _execute_payload(node)
            finding = _finding_for_payload(path, payload, scoped_tables) if payload is not None else None
            if finding is not None:
                findings.append(finding)
    return findings


def write_report(output: Path, findings: list[TenantWriteFinding], tenant_tables: set[str]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "count": len(findings),
        "gate_pass": not findings,
        "tenant_table_count": len(tenant_tables),
        "violations": [asdict(finding) for finding in findings],
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(findings: list[TenantWriteFinding], output: Path) -> None:
    print("Tenant write guard failed:")
    for finding in findings:
        print(f"- {finding.path}:{finding.line} {finding.operation}({finding.table}): {finding.message}")
    print(f"Report: {_repo_relative(output)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check tenant_id on tenant-scoped SQLAlchemy writes.")
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    scoped_tables = tenant_scoped_tables()
    findings = collect_findings(tuple(args.paths) if args.paths else DEFAULT_PATHS, tenant_tables=scoped_tables)
    write_report(args.output, findings, scoped_tables)
    if findings:
        _print_failure(findings, args.output)
        return 1
    print("Tenant write guard passed: tenant-scoped writes carry tenant_id.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
