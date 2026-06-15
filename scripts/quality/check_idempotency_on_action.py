"""Enforces guideline §6.3/§7.3: action mutations require idempotency.

The Action API is allowed to mutate operational object state only when the
Idempotency-Key contract stays intact from HTTP boundary to action_run storage.
This gate keeps the retry/replay contract from silently becoming optional.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "action_idempotency.json"
REQUIRED_UNIQUE_COLUMNS = {
    "tenant_id",
    "action_type_id",
    "actor_user_id",
    "idempotency_key",
}
REQUEST_FINGERPRINT_FIELD = "request_fingerprint"
IDEMPOTENCY_CONFLICT_AUDIT_EVENT = "action.run.idempotency_conflict"
IDEMPOTENCY_CONFLICT_MESSAGE = "idempotency key conflict"


@dataclass(frozen=True)
class IdempotencyFinding:
    code: str
    path: str
    line: int
    column: int
    message: str


def _repo_relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _parse_file(path: Path, root: Path, findings: list[IdempotencyFinding]) -> ast.Module | None:
    if not path.exists():
        findings.append(
            IdempotencyFinding(
                code="required_file_missing",
                path=_repo_relative(path, root),
                line=1,
                column=1,
                message="required source file for the action idempotency contract is missing",
            )
        )
        return None
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _class_def(tree: ast.Module, name: str) -> ast.ClassDef | None:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


def _function_def(tree: ast.Module, name: str, *, class_name: str | None = None) -> ast.FunctionDef | None:
    if class_name is None:
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        return None
    parent = _class_def(tree, class_name)
    if parent is None:
        return None
    for node in parent.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _param_default(function: ast.FunctionDef, name: str) -> ast.expr | None:
    positional = list(function.args.posonlyargs) + list(function.args.args)
    positional_defaults: dict[str, ast.expr | None] = {arg.arg: None for arg in positional}
    for arg, positional_default in zip(
        positional[-len(function.args.defaults) :],
        function.args.defaults,
        strict=False,
    ):
        positional_defaults[arg.arg] = positional_default
    if name in positional_defaults:
        return positional_defaults[name]
    for arg, keyword_default in zip(function.args.kwonlyargs, function.args.kw_defaults, strict=False):
        if arg.arg == name:
            return keyword_default
    return None


def _has_param(function: ast.FunctionDef, name: str) -> bool:
    names = [arg.arg for arg in function.args.posonlyargs + function.args.args + function.args.kwonlyargs]
    return name in names


def _is_required_keyword_only(function: ast.FunctionDef, name: str) -> bool:
    for arg, default in zip(function.args.kwonlyargs, function.args.kw_defaults, strict=False):
        if arg.arg == name:
            return default is None
    return False


def _header_alias(default: ast.expr | None) -> str | None:
    if not isinstance(default, ast.Call) or _call_name(default.func) != "Header":
        return None
    for keyword in default.keywords:
        if keyword.arg == "alias" and isinstance(keyword.value, ast.Constant):
            return str(keyword.value.value)
    return None


def _call_nodes(function: ast.FunctionDef) -> list[ast.Call]:
    return [node for node in ast.walk(function) if isinstance(node, ast.Call)]


def _class_methods(tree: ast.Module, class_name: str) -> dict[str, ast.FunctionDef]:
    parent = _class_def(tree, class_name)
    if parent is None:
        return {}
    return {node.name: node for node in parent.body if isinstance(node, ast.FunctionDef)}


def _class_has_annotation(tree: ast.Module, class_name: str, name: str) -> bool:
    parent = _class_def(tree, class_name)
    if parent is None:
        return False
    for node in parent.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return True
    return False


def _has_constant(node: ast.AST, value: object) -> bool:
    return any(isinstance(child, ast.Constant) and child.value == value for child in ast.walk(node))


def _has_name(node: ast.AST, name: str) -> bool:
    return any(isinstance(child, ast.Name) and child.id == name for child in ast.walk(node))


def _called_self_method_names(function: ast.FunctionDef, methods: dict[str, ast.FunctionDef]) -> set[str]:
    names: set[str] = set()
    for call in _call_nodes(function):
        call_name = _call_name(call.func)
        if call_name is None or not call_name.startswith("self."):
            continue
        method_name = call_name.rsplit(".", 1)[1]
        if method_name in methods:
            names.add(method_name)
    return names


def _reachable_service_methods(
    tree: ast.Module,
    *,
    class_name: str,
    start: ast.FunctionDef,
) -> list[ast.FunctionDef]:
    methods = _class_methods(tree, class_name)
    reachable: list[ast.FunctionDef] = []
    seen: set[str] = set()
    stack = [start.name]
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        function = methods.get(name)
        if function is None:
            continue
        reachable.append(function)
        stack.extend(sorted(_called_self_method_names(function, methods) - seen, reverse=True))
    return reachable


def _has_call_keyword(function: ast.FunctionDef, call_suffix: str, keyword_name: str) -> bool:
    for call in _call_nodes(function):
        call_name = _call_name(call.func)
        if call_name is None or not call_name.endswith(call_suffix):
            continue
        if any(keyword.arg == keyword_name for keyword in call.keywords):
            return True
    return False


def _first_call_line(function: ast.FunctionDef, call_suffix: str) -> int | None:
    lines = [call.lineno for call in _call_nodes(function) if (_call_name(call.func) or "").endswith(call_suffix)]
    return min(lines) if lines else None


def _raises_validation_failed(nodes: list[ast.stmt]) -> bool:
    for node in ast.walk(ast.Module(body=nodes, type_ignores=[])):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        if _call_name(node.exc.func) == "ValidationFailed" if isinstance(node.exc, ast.Call) else False:
            return True
    return False


def _is_missing_key_test(node: ast.AST) -> bool:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return isinstance(node.operand, ast.Name) and node.operand.id == "idempotency_key"
    if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == "idempotency_key":
        return any(isinstance(comparator, ast.Constant) and comparator.value == "" for comparator in node.comparators)
    return False


def _has_empty_key_validation(function: ast.FunctionDef) -> bool:
    for node in ast.walk(function):
        if isinstance(node, ast.If) and _is_missing_key_test(node.test) and _raises_validation_failed(node.body):
            return True
    return False


def _calls_function(function: ast.FunctionDef, name: str) -> bool:
    return any(_call_name(call.func) == name for call in _call_nodes(function))


def _helper_validates_empty_key(
    function: ast.FunctionDef,
    helper_tree: ast.Module | None,
    helper_name: str,
) -> bool:
    if helper_tree is None or not _calls_function(function, helper_name):
        return False
    helper = _function_def(helper_tree, helper_name)
    return helper is not None and _has_empty_key_validation(helper)


def _reachable_has_call(methods: list[ast.FunctionDef], call_suffix: str) -> bool:
    return any(_first_call_line(method, call_suffix) is not None for method in methods)


def _lookup_after_insert_line(methods: list[ast.FunctionDef]) -> int | None:
    for method in methods:
        lookup_line = _first_call_line(method, "_existing_action_run")
        insert_line = _first_call_line(method, "_insert_action_run")
        if lookup_line is not None and insert_line is not None and lookup_line > insert_line:
            return lookup_line
    return None


def _reachable_has_replay_response(methods: list[ast.FunctionDef]) -> bool:
    return _reachable_has_call(methods, "_action_replay_response") or _reachable_has_call(
        methods, "action_replay_response"
    )


def _reachable_has_request_fingerprint_guard(methods: list[ast.FunctionDef]) -> bool:
    for method in methods:
        for node in ast.walk(method):
            if not isinstance(node, ast.If):
                continue
            if _has_constant(node.test, REQUEST_FINGERPRINT_FIELD) and _has_name(node.test, REQUEST_FINGERPRINT_FIELD):
                return True
    return False


def _add_function_missing(
    findings: list[IdempotencyFinding],
    *,
    code: str,
    path: Path,
    root: Path,
    message: str,
) -> None:
    findings.append(IdempotencyFinding(code=code, path=_repo_relative(path, root), line=1, column=1, message=message))


def _check_api(tree: ast.Module, path: Path, root: Path) -> list[IdempotencyFinding]:
    findings: list[IdempotencyFinding] = []
    function = _function_def(tree, "apply_action")
    if function is None:
        _add_function_missing(
            findings,
            code="api_action_apply_missing",
            path=path,
            root=root,
            message="API must expose the action apply route",
        )
        return findings
    if _header_alias(_param_default(function, "idempotency_key")) != "Idempotency-Key":
        findings.append(
            IdempotencyFinding(
                code="api_idempotency_header_missing",
                path=_repo_relative(path, root),
                line=function.lineno,
                column=function.col_offset + 1,
                message="Action apply endpoint must require the Idempotency-Key header",
            )
        )
    if not _has_call_keyword(function, "foundry.actions.apply", "idempotency_key"):
        findings.append(
            IdempotencyFinding(
                code="api_core_call_missing_idempotency_key",
                path=_repo_relative(path, root),
                line=function.lineno,
                column=function.col_offset + 1,
                message="Action apply endpoint must pass idempotency_key into the application facade",
            )
        )
    return findings


def _check_core(tree: ast.Module, path: Path, root: Path) -> list[IdempotencyFinding]:
    findings: list[IdempotencyFinding] = []
    function = _function_def(tree, "apply", class_name="ActionGateway")
    if function is None:
        _add_function_missing(
            findings,
            code="core_apply_action_missing",
            path=path,
            root=root,
            message="ActionGateway must expose apply",
        )
        return findings
    if not _is_required_keyword_only(function, "idempotency_key"):
        findings.append(
            IdempotencyFinding(
                code="core_idempotency_param_not_required",
                path=_repo_relative(path, root),
                line=function.lineno,
                column=function.col_offset + 1,
                message="ActionGateway.apply must require idempotency_key",
            )
        )
    if not _has_call_keyword(function, "_action.apply_action", "idempotency_key"):
        findings.append(
            IdempotencyFinding(
                code="core_service_call_missing_idempotency_key",
                path=_repo_relative(path, root),
                line=function.lineno,
                column=function.col_offset + 1,
                message="ActionGateway.apply must pass idempotency_key into ActionService",
            )
        )
    return findings


def _service_required_contract_findings(
    function: ast.FunctionDef,
    path: Path,
    root: Path,
    helper_tree: ast.Module | None,
) -> list[IdempotencyFinding]:
    findings: list[IdempotencyFinding] = []
    if not _is_required_keyword_only(function, "idempotency_key"):
        findings.append(
            IdempotencyFinding(
                code="service_idempotency_param_not_required",
                path=_repo_relative(path, root),
                line=function.lineno,
                column=function.col_offset + 1,
                message="ActionService.apply_action must require idempotency_key",
            )
        )
    if not _has_empty_key_validation(function) and not _helper_validates_empty_key(
        function, helper_tree, "action_command"
    ):
        findings.append(
            IdempotencyFinding(
                code="service_idempotency_empty_key_not_rejected",
                path=_repo_relative(path, root),
                line=function.lineno,
                column=function.col_offset + 1,
                message="ActionService.apply_action must reject empty idempotency keys",
            )
        )
    return findings


def _service_replay_findings(
    tree: ast.Module,
    function: ast.FunctionDef,
    path: Path,
    root: Path,
) -> list[IdempotencyFinding]:
    findings: list[IdempotencyFinding] = []
    reachable = _reachable_service_methods(tree, class_name="ActionService", start=function)
    if not _reachable_has_call(reachable, "_existing_action_run"):
        findings.append(
            IdempotencyFinding(
                code="service_existing_run_lookup_missing",
                path=_repo_relative(path, root),
                line=function.lineno,
                column=function.col_offset + 1,
                message="ActionService.apply_action must look up an existing action_run before inserting",
            )
        )
    lookup_after_insert_line = _lookup_after_insert_line(reachable)
    if lookup_after_insert_line is not None:
        findings.append(
            IdempotencyFinding(
                code="service_existing_run_lookup_after_insert",
                path=_repo_relative(path, root),
                line=lookup_after_insert_line,
                column=1,
                message="ActionService.apply_action must check idempotency before inserting a new action_run",
            )
        )
    if _reachable_has_call(reachable, "_insert_action_run") and not _reachable_has_replay_response(reachable):
        findings.append(
            IdempotencyFinding(
                code="service_replay_response_missing",
                path=_repo_relative(path, root),
                line=function.lineno,
                column=function.col_offset + 1,
                message="ActionService.apply_action must return the existing action_run on idempotent replay",
            )
        )
    if not _reachable_has_request_fingerprint_guard(reachable):
        findings.append(
            IdempotencyFinding(
                code="service_request_fingerprint_guard_missing",
                path=_repo_relative(path, root),
                line=function.lineno,
                column=function.col_offset + 1,
                message=(
                    "ActionService must compare the stored request_fingerprint before replaying an idempotency key"
                ),
            )
        )
    return findings


def _service_finding(
    function: ast.FunctionDef,
    path: Path,
    root: Path,
    *,
    code: str,
    message: str,
) -> IdempotencyFinding:
    return IdempotencyFinding(
        code=code,
        path=_repo_relative(path, root),
        line=function.lineno,
        column=function.col_offset + 1,
        message=message,
    )


def _service_lookup_findings(
    tree: ast.Module, function: ast.FunctionDef, path: Path, root: Path
) -> list[IdempotencyFinding]:
    existing = _function_def(tree, "_existing_action_run", class_name="ActionService")
    if existing is not None and _has_call_keyword(
        existing,
        "action_repository.action_run_by_idempotency",
        "idempotency_key",
    ):
        return []
    return [
        _service_finding(
            function,
            path,
            root,
            code="service_repository_idempotency_lookup_missing",
            message="ActionService must query the repository by idempotency_key",
        )
    ]


def _service_insert_findings(
    tree: ast.Module, function: ast.FunctionDef, path: Path, root: Path
) -> list[IdempotencyFinding]:
    findings: list[IdempotencyFinding] = []
    insert_action_run = _function_def(tree, "_insert_action_run", class_name="ActionService")
    required_fields = {
        "idempotency_key": "service_action_run_record_missing_idempotency_key",
        REQUEST_FINGERPRINT_FIELD: "service_action_run_record_missing_request_fingerprint",
    }
    messages = {
        "idempotency_key": "ActionService must persist idempotency_key on ActionRunRecord",
        REQUEST_FINGERPRINT_FIELD: "ActionService must persist request_fingerprint on ActionRunRecord",
    }
    for field_name, code in required_fields.items():
        if insert_action_run is not None and _has_call_keyword(insert_action_run, "ActionRunRecord", field_name):
            continue
        findings.append(_service_finding(function, path, root, code=code, message=messages[field_name]))
    return findings


def _tree_or_helper_has_constant(tree: ast.Module, helper_tree: ast.Module | None, value: object) -> bool:
    return _has_constant(tree, value) or (helper_tree is not None and _has_constant(helper_tree, value))


def _service_conflict_findings(
    tree: ast.Module,
    function: ast.FunctionDef,
    path: Path,
    root: Path,
    helper_tree: ast.Module | None,
) -> list[IdempotencyFinding]:
    required_constants = {
        IDEMPOTENCY_CONFLICT_AUDIT_EVENT: (
            "service_idempotency_conflict_audit_missing",
            "ActionService must audit same-key/different-request idempotency conflicts",
        ),
        IDEMPOTENCY_CONFLICT_MESSAGE: (
            "service_idempotency_conflict_error_missing",
            "ActionService must reject same-key/different-request replays as a conflict",
        ),
    }
    findings: list[IdempotencyFinding] = []
    for value, (code, message) in required_constants.items():
        if not _tree_or_helper_has_constant(tree, helper_tree, value):
            findings.append(_service_finding(function, path, root, code=code, message=message))
    return findings


def _service_helper_findings(
    tree: ast.Module,
    function: ast.FunctionDef,
    path: Path,
    root: Path,
    helper_tree: ast.Module | None,
) -> list[IdempotencyFinding]:
    findings: list[IdempotencyFinding] = []
    findings.extend(_service_lookup_findings(tree, function, path, root))
    findings.extend(_service_insert_findings(tree, function, path, root))
    findings.extend(_service_conflict_findings(tree, function, path, root, helper_tree))
    return findings


def _parse_optional_file(path: Path) -> ast.Module | None:
    if not path.exists():
        return None
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _check_action_service(
    tree: ast.Module,
    path: Path,
    root: Path,
    *,
    helper_tree: ast.Module | None = None,
) -> list[IdempotencyFinding]:
    findings: list[IdempotencyFinding] = []
    function = _function_def(tree, "apply_action", class_name="ActionService")
    if function is None:
        _add_function_missing(
            findings,
            code="service_apply_action_missing",
            path=path,
            root=root,
            message="ActionService must expose apply_action",
        )
        return findings
    findings.extend(_service_required_contract_findings(function, path, root, helper_tree))
    findings.extend(_service_replay_findings(tree, function, path, root))
    findings.extend(_service_helper_findings(tree, function, path, root, helper_tree))
    return findings


def _check_repository_port(tree: ast.Module, path: Path, root: Path) -> list[IdempotencyFinding]:
    findings: list[IdempotencyFinding] = []
    function = _function_def(tree, "action_run_by_idempotency", class_name="ActionRepository")
    if function is None or not _has_param(function, "idempotency_key"):
        findings.append(
            IdempotencyFinding(
                code="action_repository_idempotency_lookup_missing",
                path=_repo_relative(path, root),
                line=1,
                column=1,
                message="ActionRepository must expose action_run_by_idempotency with idempotency_key",
            )
        )
    if not _class_has_annotation(tree, "ActionRunRow", REQUEST_FINGERPRINT_FIELD):
        findings.append(
            IdempotencyFinding(
                code="action_run_row_missing_request_fingerprint",
                path=_repo_relative(path, root),
                line=1,
                column=1,
                message="ActionRunRow must include request_fingerprint for replay conflict checks",
            )
        )
    if not _class_has_annotation(tree, "ActionRunRecord", REQUEST_FINGERPRINT_FIELD):
        findings.append(
            IdempotencyFinding(
                code="action_run_record_missing_request_fingerprint",
                path=_repo_relative(path, root),
                line=1,
                column=1,
                message="ActionRunRecord must include request_fingerprint for storage",
            )
        )
    return findings


def _unique_constraint_columns(node: ast.Call) -> set[str]:
    return {str(arg.value) for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)}


def _unique_constraint_names(node: ast.Call) -> set[str]:
    return {
        str(keyword.value.value)
        for keyword in node.keywords
        if keyword.arg == "name" and isinstance(keyword.value, ast.Constant)
    }


def _is_action_idempotency_constraint(node: ast.Call) -> bool:
    columns = _unique_constraint_columns(node)
    names = _unique_constraint_names(node)
    return REQUIRED_UNIQUE_COLUMNS.issubset(columns) and "uq_action_idempotency" in names


def _has_action_idempotency_constraint(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node.func) != "UniqueConstraint":
            continue
        if _is_action_idempotency_constraint(node):
            return True
    return False


def _has_column(tree: ast.Module, column_name: str) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node.func) != "Column":
            continue
        if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == column_name:
            return True
    return False


def _check_schema(tree: ast.Module, path: Path, root: Path) -> list[IdempotencyFinding]:
    findings: list[IdempotencyFinding] = []
    if not _has_action_idempotency_constraint(tree):
        findings.append(
            IdempotencyFinding(
                code="schema_action_idempotency_unique_constraint_missing",
                path=_repo_relative(path, root),
                line=1,
                column=1,
                message="action_runs must have a tenant/action/actor/idempotency_key unique constraint",
            )
        )
    if not _has_column(tree, REQUEST_FINGERPRINT_FIELD):
        findings.append(
            IdempotencyFinding(
                code="schema_action_request_fingerprint_missing",
                path=_repo_relative(path, root),
                line=1,
                column=1,
                message="action_runs must store request_fingerprint for idempotency conflict detection",
            )
        )
    return findings


def collect_findings(root: Path = ROOT) -> list[IdempotencyFinding]:
    findings: list[IdempotencyFinding] = []
    paths = {
        "api": root / "apps" / "api" / "foundry_lite_api" / "main.py",
        "foundry": root / "libs" / "foundry_lite" / "application" / "facades" / "action_gateway.py",
        "service": root / "libs" / "foundry_lite" / "application" / "services" / "action_service.py",
        "service_helpers": root / "libs" / "foundry_lite" / "application" / "services" / "action_helpers.py",
        "port": root / "libs" / "foundry_lite" / "application" / "ports" / "action_repository.py",
        "schema": root / "libs" / "foundry_lite" / "infrastructure" / "schema.py",
    }
    parsed = {
        name: _parse_optional_file(path) if name == "service_helpers" else _parse_file(path, root, findings)
        for name, path in paths.items()
    }
    if parsed["api"] is not None:
        findings.extend(_check_api(parsed["api"], paths["api"], root))
    if parsed["foundry"] is not None:
        findings.extend(_check_core(parsed["foundry"], paths["foundry"], root))
    if parsed["service"] is not None:
        findings.extend(
            _check_action_service(
                parsed["service"],
                paths["service"],
                root,
                helper_tree=parsed["service_helpers"],
            )
        )
    if parsed["port"] is not None:
        findings.extend(_check_repository_port(parsed["port"], paths["port"], root))
    if parsed["schema"] is not None:
        findings.extend(_check_schema(parsed["schema"], paths["schema"], root))
    return findings


def write_report(output: Path, findings: list[IdempotencyFinding]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "count": len(findings),
        "baseline": 0,
        "gate_pass": not findings,
        "violations": [asdict(finding) for finding in findings],
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(findings: list[IdempotencyFinding]) -> None:
    print("Action idempotency gate failed:")
    for finding in findings:
        print(f"- {finding.path}:{finding.line}:{finding.column} {finding.code}: {finding.message}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Action API idempotency contract.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    findings = collect_findings(args.root)
    write_report(args.output, findings)
    if findings:
        _print_failure(findings)
        return 1
    print("Action idempotency gate passed: Action apply keeps required retry/replay idempotency.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
