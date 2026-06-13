"""Enforces guideline §2.3: read/query entrypoints must not mutate state.

Query methods are safe only when callers can trust them to observe state without
changing it. This gate scans application services and blocks public read-like
methods (`get_*`, `list_*`, `query_*`, `preview_*`, `inspect_*`, `lineage_*`,
`find_*`) that can reach repository writes, audit/outbox/lineage writes, file
writes, write-oriented adapters, or public mutation methods.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SERVICES_ROOT = ROOT / "libs" / "foundry_lite" / "application" / "services"
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "query_side_effects.json"

QUERY_METHOD_PREFIXES = ("find_", "get_", "inspect_", "lineage_", "list_", "preview_", "query_")
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
SIDE_EFFECT_CALL_LEAVES = {
    "_audit",
    "_lineage",
    "_outbox",
    "commit_staged_file",
    "csv_to_parquet",
    "insert_audit_event",
    "insert_lineage_edge",
    "insert_outbox_event",
    "mkdir",
    "rename",
    "replace",
    "rmtree",
    "staging_file",
    "unlink",
    "write_bytes",
    "write_text",
    "rows_to_parquet",
    "execute_transform",
}
WRITE_OPEN_MODES = {"a", "a+", "w", "w+", "x", "x+"}


@dataclass(frozen=True)
class MethodInfo:
    path: Path
    line: int
    class_name: str
    name: str
    calls: frozenset[str]
    side_effect_calls: frozenset[str]


@dataclass(frozen=True)
class QuerySideEffectFinding:
    path: str
    line: int
    class_name: str
    method: str
    side_effects: tuple[str, ...]
    message: str


def _repo_relative(path: Path, project_root: Path = ROOT) -> str:
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


def _leaf(call_name: str) -> str:
    return call_name.rsplit(".", 1)[-1]


def _is_query_entrypoint(method_name: str) -> bool:
    return not method_name.startswith("_") and any(method_name.startswith(prefix) for prefix in QUERY_METHOD_PREFIXES)


def _is_public_mutation_method(method_name: str) -> bool:
    return not method_name.startswith("_") and any(
        method_name.startswith(prefix) for prefix in MUTATION_METHOD_PREFIXES
    )


def _is_repository_write(call_name: str) -> bool:
    leaf = _leaf(call_name)
    return "repository" in call_name and any(leaf.startswith(prefix) for prefix in REPOSITORY_WRITE_PREFIXES)


def _constant_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _open_call_is_write(call: ast.Call, call_name: str) -> bool:
    if _leaf(call_name) != "open":
        return False
    mode = _constant_string(call.args[1]) if len(call.args) > 1 else None
    for keyword in call.keywords:
        if keyword.arg == "mode":
            mode = _constant_string(keyword.value)
    return mode in WRITE_OPEN_MODES


def _side_effect_call_name(call: ast.Call) -> str | None:
    call_name = _call_name(call.func)
    if not call_name:
        return None
    if _is_repository_write(call_name) or _leaf(call_name) in SIDE_EFFECT_CALL_LEAVES:
        return call_name
    if _open_call_is_write(call, call_name):
        return f"{call_name}[write-mode]"
    return None


def _service_files(services_root: Path) -> list[Path]:
    return sorted(path for path in services_root.rglob("*.py") if "__pycache__" not in path.parts)


def _method_info(path: Path, class_name: str, node: ast.FunctionDef | ast.AsyncFunctionDef) -> MethodInfo:
    calls: set[str] = set()
    side_effect_calls: set[str] = set()
    for call in (child for child in ast.walk(node) if isinstance(child, ast.Call)):
        call_name = _call_name(call.func)
        if call_name:
            calls.add(_leaf(call_name))
        side_effect = _side_effect_call_name(call)
        if side_effect is not None:
            side_effect_calls.add(side_effect)
    return MethodInfo(
        path=path,
        line=node.lineno,
        class_name=class_name,
        name=node.name,
        calls=frozenset(calls),
        side_effect_calls=frozenset(side_effect_calls),
    )


def collect_methods(services_root: Path = DEFAULT_SERVICES_ROOT) -> list[MethodInfo]:
    methods: list[MethodInfo] = []
    for path in _service_files(services_root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for class_node in (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)):
            for node in class_node.body:
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    methods.append(_method_info(path, class_node.name, node))
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


def _side_effects_for_query(method: MethodInfo, reachable: list[MethodInfo]) -> tuple[str, ...]:
    side_effects: set[str] = set()
    for candidate in reachable:
        side_effects.update(candidate.side_effect_calls)
        if candidate is not method and _is_public_mutation_method(candidate.name):
            side_effects.add(f"{candidate.class_name}.{candidate.name}")
    return tuple(sorted(side_effects))


def collect_findings(
    *,
    services_root: Path = DEFAULT_SERVICES_ROOT,
    project_root: Path = ROOT,
) -> list[QuerySideEffectFinding]:
    methods = collect_methods(services_root)
    by_name = _methods_by_name(methods)
    findings: list[QuerySideEffectFinding] = []
    for method in methods:
        if not _is_query_entrypoint(method.name):
            continue
        side_effects = _side_effects_for_query(method, _reachable_methods(method, by_name))
        if side_effects:
            findings.append(
                QuerySideEffectFinding(
                    path=_repo_relative(method.path, project_root),
                    line=method.line,
                    class_name=method.class_name,
                    method=method.name,
                    side_effects=side_effects,
                    message="public query/read method can reach state-changing behavior",
                )
            )
    return findings


def write_report(output: Path, findings: list[QuerySideEffectFinding]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "baseline": 0,
        "count": len(findings),
        "gate": "query_side_effects",
        "gate_pass": not findings,
        "violations": [asdict(finding) for finding in findings],
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(findings: list[QuerySideEffectFinding], output: Path) -> None:
    print("Release gate blocked: read/query methods must not change system state.")
    for finding in findings:
        side_effects = ", ".join(finding.side_effects)
        print(f"- {finding.path}:{finding.line} {finding.class_name}.{finding.method}: {side_effects}")
    print(f"Report: {_repo_relative(output)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate read/query application services have no side effects.")
    parser.add_argument("--services-root", type=Path, default=DEFAULT_SERVICES_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    findings = collect_findings(services_root=args.services_root, project_root=ROOT)
    write_report(args.output, findings)
    if findings:
        _print_failure(findings, args.output)
        return 1
    print("Query side-effect gate passed: read/query service entrypoints do not mutate state.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
