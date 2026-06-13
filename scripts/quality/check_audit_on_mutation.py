"""Enforces guideline §1.2/§7/§10.2/§18.3: public mutations need audit proof.

This gate scans application service entrypoints. If a public mutation method
reaches a repository write but cannot reach an audit/outbox call in the same
service call tree, release evidence is blocked.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SERVICES_ROOT = ROOT / "libs" / "foundry_lite" / "application" / "services"
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "audit_on_mutation.json"
MUTATION_METHOD_PREFIXES = (
    "apply_",
    "abort_",
    "cleanup_",
    "commit_",
    "create_",
    "ensure_",
    "index_",
    "materialize",
    "register_",
    "retry_",
    "run_",
    "upload_",
)
REPOSITORY_WRITE_PREFIXES = (
    "abort_",
    "activate_",
    "archive_",
    "commit_",
    "create_",
    "delete_",
    "insert_",
    "mark_",
    "update_",
)
AUDIT_CALL_NAMES = {"_audit", "_outbox", "insert_audit_event", "insert_outbox_event"}


@dataclass(frozen=True)
class MethodInfo:
    path: Path
    line: int
    class_name: str
    name: str
    calls: frozenset[str]
    has_repository_write: bool
    has_audit: bool


@dataclass(frozen=True)
class AuditFinding:
    path: str
    line: int
    class_name: str
    method: str
    message: str


def _repo_relative(path: Path, project_root: Path) -> str:
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return str(path)


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        return _call_name(node.func)
    return ""


def _call_leaf(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def _is_repository_write(name: str) -> bool:
    leaf = _call_leaf(name)
    return "repository" in name and any(leaf.startswith(prefix) for prefix in REPOSITORY_WRITE_PREFIXES)


def _is_audit_call(name: str) -> bool:
    return _call_leaf(name) in AUDIT_CALL_NAMES


def _is_public_mutation(name: str) -> bool:
    return not name.startswith("_") and any(name.startswith(prefix) for prefix in MUTATION_METHOD_PREFIXES)


def _method_info(path: Path, class_name: str, node: ast.FunctionDef | ast.AsyncFunctionDef) -> MethodInfo:
    call_names = frozenset(_call_name(call.func) for call in ast.walk(node) if isinstance(call, ast.Call))
    return MethodInfo(
        path=path,
        line=node.lineno,
        class_name=class_name,
        name=node.name,
        calls=frozenset(_call_leaf(name) for name in call_names if name),
        has_repository_write=any(_is_repository_write(name) for name in call_names),
        has_audit=any(_is_audit_call(name) for name in call_names),
    )


def _service_files(services_root: Path) -> list[Path]:
    return sorted(path for path in services_root.rglob("*.py") if "__pycache__" not in path.parts)


def collect_methods(services_root: Path = DEFAULT_SERVICES_ROOT) -> list[MethodInfo]:
    methods: list[MethodInfo] = []
    for path in _service_files(services_root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for class_node in (node for node in tree.body if isinstance(node, ast.ClassDef)):
            for method_node in (
                item for item in class_node.body if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
            ):
                methods.append(_method_info(path, class_node.name, method_node))
    return methods


def _methods_by_name(methods: list[MethodInfo]) -> dict[str, list[MethodInfo]]:
    by_name: dict[str, list[MethodInfo]] = {}
    for method in methods:
        by_name.setdefault(method.name, []).append(method)
    return by_name


def _reachable_methods(method: MethodInfo, by_name: dict[str, list[MethodInfo]]) -> list[MethodInfo]:
    seen = {(method.class_name, method.name)}
    stack = [method]
    reachable: list[MethodInfo] = []
    while stack:
        current = stack.pop()
        reachable.append(current)
        for call in current.calls:
            for candidate in by_name.get(call, []):
                key = (candidate.class_name, candidate.name)
                if key not in seen:
                    seen.add(key)
                    stack.append(candidate)
    return reachable


def collect_findings(
    *,
    services_root: Path = DEFAULT_SERVICES_ROOT,
    project_root: Path = ROOT,
) -> list[AuditFinding]:
    methods = collect_methods(services_root)
    by_name = _methods_by_name(methods)
    findings: list[AuditFinding] = []
    for method in methods:
        if not _is_public_mutation(method.name):
            continue
        reachable = _reachable_methods(method, by_name)
        reaches_write = any(candidate.has_repository_write for candidate in reachable)
        reaches_audit = any(candidate.has_audit for candidate in reachable)
        if reaches_write and not reaches_audit:
            findings.append(
                AuditFinding(
                    path=_repo_relative(method.path, project_root),
                    line=method.line,
                    class_name=method.class_name,
                    method=method.name,
                    message="public mutation reaches a repository write without a reachable audit/outbox call",
                )
            )
    return findings


def write_report(output: Path, findings: list[AuditFinding]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "count": len(findings),
        "baseline": 0,
        "gate_pass": not findings,
        "violations": [finding.__dict__ for finding in findings],
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(findings: list[AuditFinding], output: Path) -> None:
    print("Release gate blocked: public service mutations must reach audit/outbox evidence.")
    for finding in findings:
        print(f"- {finding.path}:{finding.line} {finding.class_name}.{finding.method}: {finding.message}")
    print(f"Report: {_repo_relative(output, ROOT)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate that public service mutations have audit/outbox proof.")
    parser.add_argument("--services-root", type=Path, default=DEFAULT_SERVICES_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    findings = collect_findings(services_root=args.services_root, project_root=ROOT)
    write_report(args.output, findings)
    if findings:
        _print_failure(findings, args.output)
        return 1
    print("Audit-on-mutation gate passed: all public service mutations reach audit/outbox proof.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
