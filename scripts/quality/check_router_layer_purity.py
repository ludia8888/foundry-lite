"""Enforces guideline §3.1/§4.1/§18.3: API routers must not own DB work.

API handlers are request/response adapters. They may call the application
facade or application services, but they must not reach into repository fields
or open/execute SQLAlchemy transactions directly.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATHS = (ROOT / "apps" / "api",)
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "router_layer_purity.json"
TRANSACTION_EXECUTE_RECEIVERS = {"conn", "connection", "session", "transaction"}


@dataclass(frozen=True)
class RouterPurityFinding:
    code: str
    path: str
    line: int
    column: int
    expression: str
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
    return sorted(files)


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _is_core_repository_call(call_name: str) -> bool:
    parts = call_name.split(".")
    if len(parts) < 3 or parts[0] != "foundry":
        return False
    return any(part.endswith("_repository") for part in parts[1:-1])


def _is_engine_begin_call(call_name: str) -> bool:
    parts = call_name.split(".")
    return len(parts) >= 2 and parts[-2:] == ["engine", "begin"]


def _is_transaction_execute_call(call_name: str) -> bool:
    parts = call_name.split(".")
    return len(parts) >= 2 and parts[-1] == "execute" and parts[-2] in TRANSACTION_EXECUTE_RECEIVERS


def _finding_for_call(path: Path, node: ast.Call, call_name: str) -> RouterPurityFinding | None:
    if _is_core_repository_call(call_name):
        return RouterPurityFinding(
            code="api_core_repository_call",
            path=_repo_relative(path),
            line=node.lineno,
            column=node.col_offset + 1,
            expression=call_name,
            message="API router calls a foundry repository field directly instead of an application service",
        )
    if _is_engine_begin_call(call_name):
        return RouterPurityFinding(
            code="api_engine_begin",
            path=_repo_relative(path),
            line=node.lineno,
            column=node.col_offset + 1,
            expression=call_name,
            message="API router opens a DB transaction directly",
        )
    if _is_transaction_execute_call(call_name):
        return RouterPurityFinding(
            code="api_transaction_execute",
            path=_repo_relative(path),
            line=node.lineno,
            column=node.col_offset + 1,
            expression=call_name,
            message="API router executes a DB statement directly",
        )
    return None


def collect_findings(paths: tuple[Path, ...] = DEFAULT_PATHS) -> list[RouterPurityFinding]:
    findings: list[RouterPurityFinding] = []
    for path in _python_files(paths):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            call_name = _call_name(node.func)
            if call_name is None:
                continue
            finding = _finding_for_call(path, node, call_name)
            if finding is not None:
                findings.append(finding)
    return findings


def write_report(output: Path, findings: list[RouterPurityFinding]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "gate": "router_layer_purity",
        "baseline": {"max_violations": 0},
        "count": len(findings),
        "violations": [asdict(finding) for finding in findings],
        "gate_pass": not findings,
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(findings: list[RouterPurityFinding], output: Path) -> None:
    print("Release gate blocked: API routers must not call repositories or DB transactions directly.")
    for finding in findings:
        print(
            f"- {finding.path}:{finding.line}:{finding.column} {finding.code} {finding.expression}: {finding.message}"
        )
    print(f"Report: {_repo_relative(output)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate API router layer purity.")
    parser.add_argument("paths", nargs="*", type=Path, default=list(DEFAULT_PATHS))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    paths = tuple(path if path.is_absolute() else ROOT / path for path in args.paths)
    findings = collect_findings(paths)
    write_report(args.output, findings)
    if findings:
        _print_failure(findings, args.output)
        return 1
    print("Router layer purity gate passed: API routers do not call repositories or DB transactions directly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
