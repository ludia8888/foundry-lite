"""Reject broad exception handlers that erase failures as normal control flow."""

from __future__ import annotations

import argparse
import ast
import json
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATHS = (ROOT / "apps", ROOT / "libs" / "foundry_lite" / "application")
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "silent_broad_exceptions.json"
FAILURE_EVIDENCE_CALL_NAMES = frozenset(
    {
        "_detector_failure_incident",
        "_error_payload",
        "_mark_failed",
        "_record_failure",
        "_record_reconciliation_deferred",
        "_unexpected_error",
        "build_failure_incident",
        "failed",
        "reconnecting",
        "record_runtime_cleanup_failure",
        "runtime_error_payload",
    }
)


@dataclass(frozen=True)
class SilentBroadExceptionFinding:
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


def _is_broad_handler(node: ast.ExceptHandler) -> bool:
    if node.type is None:
        return True
    return isinstance(node.type, ast.Name) and node.type.id in {"Exception", "BaseException"}


def _is_empty_collection(node: ast.AST) -> bool:
    return isinstance(node, (ast.List, ast.Tuple, ast.Set, ast.Dict)) and not (
        node.elts if isinstance(node, (ast.List, ast.Tuple, ast.Set)) else node.keys
    )


def _is_silent_statement(node: ast.AST) -> bool:
    if isinstance(node, (ast.Pass, ast.Continue, ast.Break)):
        return True
    if not isinstance(node, ast.Return):
        return False
    return node.value is None or isinstance(node.value, ast.Constant) or _is_empty_collection(node.value)


def _handler_statements(node: ast.ExceptHandler) -> list[ast.stmt]:
    statements: list[ast.stmt] = []
    pending: list[ast.AST] = list(node.body)
    while pending:
        item = pending.pop()
        if isinstance(item, (ast.ExceptHandler, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(item, ast.stmt):
            statements.append(item)
        pending.extend(ast.iter_child_nodes(item))
    return statements


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id.casefold()
    if isinstance(node.func, ast.Attribute):
        return node.func.attr.casefold()
    return ""


def _contains_name(node: ast.AST, name: str) -> bool:
    return any(isinstance(child, ast.Name) and child.id == name for child in ast.walk(node))


def _handler_has_failure_evidence(handler: ast.ExceptHandler) -> bool:
    if not isinstance(handler.name, str):
        return False
    for node in ast.walk(handler):
        if not isinstance(node, ast.Call):
            continue
        call_name = _call_name(node)
        if call_name in FAILURE_EVIDENCE_CALL_NAMES and _contains_name(node, handler.name):
            return True
        if call_name == "append" and any(_contains_name(argument, handler.name) for argument in node.args):
            return True
    return False


def collect_findings(paths: tuple[Path, ...] = DEFAULT_PATHS) -> list[SilentBroadExceptionFinding]:
    findings: list[SilentBroadExceptionFinding] = []
    for path in _python_files(paths):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for handler in (node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)):
            if not _is_broad_handler(handler):
                continue
            has_failure_evidence = _handler_has_failure_evidence(handler)
            for statement in _handler_statements(handler):
                if not _is_silent_statement(statement) or has_failure_evidence:
                    continue
                findings.append(
                    SilentBroadExceptionFinding(
                        code="silent_broad_exception",
                        path=_repo_relative(path),
                        line=statement.lineno,
                        column=statement.col_offset + 1,
                        message=(
                            "broad exception handlers must raise or emit an explicit failure payload; "
                            "they cannot return a normal sentinel or skip silently"
                        ),
                    )
                )
    return findings


def write_report(output: Path, findings: list[SilentBroadExceptionFinding]) -> None:
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
    parser = argparse.ArgumentParser(description="Reject silent broad exception handlers.")
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    findings = collect_findings(tuple(args.paths) if args.paths else DEFAULT_PATHS)
    write_report(args.output, findings)
    if findings:
        print("Silent broad exception gate failed:")
        for finding in findings:
            print(f"- {finding.path}:{finding.line}:{finding.column} {finding.message}")
        return 1
    print("Silent broad exception gate passed: broad handlers preserve explicit failure semantics.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
