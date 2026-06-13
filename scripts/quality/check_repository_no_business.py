"""Enforces guideline §3.1/§4.2/§7.1/§18.3: repositories do not own business rules.

Repositories are DB read/write adapters. Domain decisions such as validation,
permission, conflict policy, and use-case transaction ownership belong in the
domain/application layers. This gate blocks repository modules from importing
or raising domain errors and from directly committing/rolling back caller-owned
transactions.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATHS = (ROOT / "libs" / "foundry_lite" / "infrastructure" / "repositories",)
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "repository_no_business.json"
DOMAIN_ERROR_MODULE = "foundry_lite.domain.errors"
DOMAIN_ERROR_NAMES = {
    "ConflictDetected",
    "ExternalSystemError",
    "FoundryLiteError",
    "InvariantViolation",
    "NotFound",
    "PermissionDenied",
    "ValidationFailed",
}
ALLOWED_TRANSACTION_CONTROL_RECEIVERS = {"savepoint"}


@dataclass(frozen=True)
class RepositoryBusinessFinding:
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
    if isinstance(node, ast.Call):
        return _call_name(node.func)
    return None


def _import_findings(path: Path, node: ast.ImportFrom) -> list[RepositoryBusinessFinding]:
    if node.module != DOMAIN_ERROR_MODULE:
        return []
    findings: list[RepositoryBusinessFinding] = []
    for alias in node.names:
        if alias.name not in DOMAIN_ERROR_NAMES and alias.name != "*":
            continue
        findings.append(
            RepositoryBusinessFinding(
                code="repository_imports_domain_error",
                path=_repo_relative(path),
                line=node.lineno,
                column=node.col_offset + 1,
                expression=alias.name,
                message="Repository imports a domain error; translate DB errors into port-level errors instead",
            )
        )
    return findings


def _raised_name(node: ast.Raise) -> str | None:
    if node.exc is None:
        return None
    return _call_name(node.exc)


def _raise_finding(path: Path, node: ast.Raise) -> RepositoryBusinessFinding | None:
    raised = _raised_name(node)
    if raised is None:
        return None
    leaf = raised.split(".")[-1]
    if leaf not in DOMAIN_ERROR_NAMES:
        return None
    return RepositoryBusinessFinding(
        code="repository_raises_domain_error",
        path=_repo_relative(path),
        line=node.lineno,
        column=node.col_offset + 1,
        expression=raised,
        message="Repository raises a domain/use-case error instead of leaving business decisions to services",
    )


def _transaction_control_finding(path: Path, node: ast.Call) -> RepositoryBusinessFinding | None:
    call_name = _call_name(node.func)
    if call_name is None:
        return None
    parts = call_name.split(".")
    if len(parts) < 2 or parts[-1] not in {"commit", "rollback"}:
        return None
    receiver = parts[-2]
    if receiver in ALLOWED_TRANSACTION_CONTROL_RECEIVERS:
        return None
    return RepositoryBusinessFinding(
        code="repository_controls_transaction",
        path=_repo_relative(path),
        line=node.lineno,
        column=node.col_offset + 1,
        expression=call_name,
        message="Repository directly commits or rolls back a transaction owned by the application service",
    )


def _findings_for_file(path: Path) -> list[RepositoryBusinessFinding]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    findings: list[RepositoryBusinessFinding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            findings.extend(_import_findings(path, node))
        elif isinstance(node, ast.Raise):
            finding = _raise_finding(path, node)
            if finding is not None:
                findings.append(finding)
        elif isinstance(node, ast.Call):
            finding = _transaction_control_finding(path, node)
            if finding is not None:
                findings.append(finding)
    return findings


def collect_findings(paths: tuple[Path, ...] = DEFAULT_PATHS) -> list[RepositoryBusinessFinding]:
    findings: list[RepositoryBusinessFinding] = []
    for path in _python_files(paths):
        findings.extend(_findings_for_file(path))
    return findings


def write_report(output: Path, findings: list[RepositoryBusinessFinding]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "gate": "repository_no_business",
        "baseline": {"max_violations": 0},
        "count": len(findings),
        "violations": [asdict(finding) for finding in findings],
        "gate_pass": not findings,
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(findings: list[RepositoryBusinessFinding], output: Path) -> None:
    print("Release gate blocked: repositories must not own business decisions or caller transactions.")
    for finding in findings:
        print(
            f"- {finding.path}:{finding.line}:{finding.column} {finding.code} {finding.expression}: {finding.message}"
        )
    print(f"Report: {_repo_relative(output)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate repository business-rule boundaries.")
    parser.add_argument("paths", nargs="*", type=Path, default=list(DEFAULT_PATHS))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    paths = tuple(path if path.is_absolute() else ROOT / path for path in args.paths)
    findings = collect_findings(paths)
    write_report(args.output, findings)
    if findings:
        _print_failure(findings, args.output)
        return 1
    print("Repository no-business gate passed: repositories do not raise domain errors or own commits.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
