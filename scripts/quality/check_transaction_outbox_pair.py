"""Enforces guideline §7.1/§10.2: transaction writes need audit/outbox proof.

This gate scans application services for transaction-scoped repository writes.
If a service transaction block writes domain state using the transaction handle,
the same transaction call tree must also persist audit/outbox evidence using
that handle. This blocks state changes that can commit without an operational
trail in the same unit of work.
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
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "transaction_outbox_pair.json"
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
AUDIT_OUTBOX_CALLS = {"_audit", "_outbox", "insert_audit_event", "insert_outbox_event"}
LIFECYCLE_WRITE_ALLOWLIST = {
    "create_index_run",
    "create_open_transaction",
    "insert_materialization",
    "insert_materialization_run",
    "insert_sync_run",
    "insert_transform_run",
    "mark_index_run_failed",
}


@dataclass(frozen=True)
class MethodInfo:
    path: Path
    class_name: str
    name: str
    node: ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True)
class TransactionFinding:
    path: str
    line: int
    class_name: str
    method: str
    transaction_name: str
    write_calls: tuple[str, ...]
    message: str


@dataclass(frozen=True)
class TransactionEvidence:
    writes: frozenset[str]
    has_audit_or_outbox: bool


MethodCallKey = tuple[str, str, str]
EvidenceCacheKey = tuple[MethodCallKey, frozenset[MethodCallKey]]


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


def _leaf(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def _is_public_mutation(name: str) -> bool:
    return not name.startswith("_") and any(name.startswith(prefix) for prefix in MUTATION_METHOD_PREFIXES)


def _is_repository_write(name: str) -> bool:
    leaf = _leaf(name)
    if leaf in LIFECYCLE_WRITE_ALLOWLIST:
        return False
    return "repository" in name and any(leaf.startswith(prefix) for prefix in REPOSITORY_WRITE_PREFIXES)


def _is_audit_or_outbox(name: str) -> bool:
    return _leaf(name) in AUDIT_OUTBOX_CALLS


def _name_matches(node: ast.AST, expected: str) -> bool:
    return isinstance(node, ast.Name) and node.id == expected


def _call_uses_transaction(call: ast.Call, transaction_name: str) -> bool:
    if call.args and _name_matches(call.args[0], transaction_name):
        return True
    return any(
        keyword.arg == "transaction" and _name_matches(keyword.value, transaction_name) for keyword in call.keywords
    )


def _service_method_candidates(
    call: ast.Call,
    *,
    class_name: str,
    methods: dict[tuple[str, str], MethodInfo],
    methods_by_name: dict[str, list[MethodInfo]],
) -> list[MethodInfo]:
    call_name = _call_name(call.func)
    if not call_name.startswith("self."):
        return []
    method_name = _leaf(call_name)
    if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
        candidate = methods.get((class_name, method_name))
        return [candidate] if candidate is not None else []
    return methods_by_name.get(method_name, [])


def _method_transaction_name(caller_transaction_name: str, call: ast.Call, callee: MethodInfo) -> str | None:
    args = callee.node.args.args
    for index, arg in enumerate(call.args):
        if _name_matches(arg, caller_transaction_name) and index + 1 < len(args):
            return args[index + 1].arg
    for keyword in call.keywords:
        if keyword.arg is None or not _name_matches(keyword.value, caller_transaction_name):
            continue
        if any(arg.arg == keyword.arg for arg in args):
            return keyword.arg
    return None


def _service_files(services_root: Path) -> list[Path]:
    return sorted(path for path in services_root.rglob("*.py") if "__pycache__" not in path.parts)


def collect_methods(services_root: Path = DEFAULT_SERVICES_ROOT) -> dict[tuple[str, str], MethodInfo]:
    methods: dict[tuple[str, str], MethodInfo] = {}
    for path in _service_files(services_root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for class_node in (node for node in tree.body if isinstance(node, ast.ClassDef)):
            for node in class_node.body:
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    methods[(class_node.name, node.name)] = MethodInfo(path, class_node.name, node.name, node)
    return methods


def _methods_by_name(methods: dict[tuple[str, str], MethodInfo]) -> dict[str, list[MethodInfo]]:
    by_name: dict[str, list[MethodInfo]] = {}
    for method in methods.values():
        by_name.setdefault(method.name, []).append(method)
    return by_name


def _engine_begin_transaction_name(item: ast.AST) -> str | None:
    if not isinstance(item, ast.withitem):
        return None
    if _call_name(item.context_expr) != "self.engine.begin":
        return None
    if isinstance(item.optional_vars, ast.Name):
        return item.optional_vars.id
    return None


def _transaction_blocks(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[tuple[int, str, list[ast.stmt]]]:
    blocks: list[tuple[int, str, list[ast.stmt]]] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.With):
            continue
        for item in child.items:
            transaction_name = _engine_begin_transaction_name(item)
            if transaction_name:
                blocks.append((child.lineno, transaction_name, child.body))
    return blocks


def _calls_in_statements(statements: list[ast.stmt]) -> list[ast.Call]:
    return [node for statement in statements for node in ast.walk(statement) if isinstance(node, ast.Call)]


def _analyze_statements(
    *,
    class_name: str,
    statements: list[ast.stmt],
    transaction_name: str,
    methods: dict[tuple[str, str], MethodInfo],
    methods_by_name: dict[str, list[MethodInfo]],
    seen: frozenset[MethodCallKey],
    evidence_cache: dict[EvidenceCacheKey, TransactionEvidence],
) -> TransactionEvidence:
    writes: set[str] = set()
    has_audit_or_outbox = False
    for call in _calls_in_statements(statements):
        call_name = _call_name(call.func)
        if _call_uses_transaction(call, transaction_name):
            if _is_repository_write(call_name):
                writes.add(call_name)
            if _is_audit_or_outbox(call_name):
                has_audit_or_outbox = True
        nested = _nested_transaction_evidence(
            call,
            class_name=class_name,
            transaction_name=transaction_name,
            methods=methods,
            methods_by_name=methods_by_name,
            seen=seen,
            evidence_cache=evidence_cache,
        )
        writes.update(nested.writes)
        has_audit_or_outbox = has_audit_or_outbox or nested.has_audit_or_outbox
    return TransactionEvidence(frozenset(writes), has_audit_or_outbox)


def _nested_transaction_evidence(
    call: ast.Call,
    *,
    class_name: str,
    transaction_name: str,
    methods: dict[tuple[str, str], MethodInfo],
    methods_by_name: dict[str, list[MethodInfo]],
    seen: frozenset[MethodCallKey],
    evidence_cache: dict[EvidenceCacheKey, TransactionEvidence],
) -> TransactionEvidence:
    writes: set[str] = set()
    has_audit_or_outbox = False
    for callee in _service_method_candidates(
        call,
        class_name=class_name,
        methods=methods,
        methods_by_name=methods_by_name,
    ):
        callee_transaction_name = _method_transaction_name(transaction_name, call, callee)
        if callee_transaction_name is None:
            continue
        key = (callee.class_name, callee.name, callee_transaction_name)
        if key in seen:
            continue
        cache_key = (key, seen)
        if cache_key not in evidence_cache:
            evidence_cache[cache_key] = _analyze_statements(
                class_name=callee.class_name,
                statements=callee.node.body,
                transaction_name=callee_transaction_name,
                methods=methods,
                methods_by_name=methods_by_name,
                seen=seen | {key},
                evidence_cache=evidence_cache,
            )
        evidence = evidence_cache[cache_key]
        writes.update(evidence.writes)
        has_audit_or_outbox = has_audit_or_outbox or evidence.has_audit_or_outbox
    return TransactionEvidence(frozenset(writes), has_audit_or_outbox)


def collect_findings(
    *,
    services_root: Path = DEFAULT_SERVICES_ROOT,
    project_root: Path = ROOT,
) -> list[TransactionFinding]:
    methods = collect_methods(services_root)
    methods_by_name = _methods_by_name(methods)
    evidence_cache: dict[EvidenceCacheKey, TransactionEvidence] = {}
    findings: list[TransactionFinding] = []
    for method in methods.values():
        if not _is_public_mutation(method.name):
            continue
        for line, transaction_name, statements in _transaction_blocks(method.node):
            evidence = _analyze_statements(
                class_name=method.class_name,
                statements=statements,
                transaction_name=transaction_name,
                methods=methods,
                methods_by_name=methods_by_name,
                seen=frozenset({(method.class_name, method.name, transaction_name)}),
                evidence_cache=evidence_cache,
            )
            if evidence.writes and not evidence.has_audit_or_outbox:
                findings.append(
                    TransactionFinding(
                        path=_repo_relative(method.path, project_root),
                        line=line,
                        class_name=method.class_name,
                        method=method.name,
                        transaction_name=transaction_name,
                        write_calls=tuple(sorted(evidence.writes)),
                        message="transaction-scoped repository writes need audit/outbox proof in the same call tree",
                    )
                )
    return findings


def write_report(output: Path, findings: list[TransactionFinding]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "gate": "transaction_outbox_pair",
        "count": len(findings),
        "baseline": 0,
        "gate_pass": not findings,
        "violations": [asdict(finding) for finding in findings],
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(findings: list[TransactionFinding], output: Path) -> None:
    print("Release gate blocked: transaction-scoped writes must carry audit/outbox proof.")
    for finding in findings:
        writes = ", ".join(finding.write_calls)
        print(f"- {finding.path}:{finding.line} {finding.class_name}.{finding.method}: {writes}")
    print(f"Report: {_repo_relative(output)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate service transaction write/audit-outbox pairing.")
    parser.add_argument("--services-root", type=Path, default=DEFAULT_SERVICES_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    findings = collect_findings(services_root=args.services_root, project_root=ROOT)
    write_report(args.output, findings)
    if findings:
        _print_failure(findings, args.output)
        return 1
    print("Transaction outbox/audit pair gate passed: transaction-scoped writes keep audit/outbox proof.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
