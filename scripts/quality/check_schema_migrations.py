"""Enforces guideline §18.1/S55: Alembic migration history stays operationally safe.

S55 moves schema management from "metadata snapshot exists" toward an
operator-facing migration discipline. This gate keeps the migration chain
single-headed and blocks destructive downgrades so production rollback work uses
forward fixes or a documented restore runbook instead of silent table drops. It
also enforces the S55 expand-contract rule: expand migrations may add compatible
shape, but they cannot remove/rename/change shape while old app versions may
still be running.
"""

from __future__ import annotations

import argparse
import ast
import json
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_VERSIONS_DIR = ROOT / "migrations" / "versions"
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "schema_migrations.json"
VALID_MIGRATION_PHASES = frozenset({"baseline", "expand", "contract"})
PHASE_COMPATIBILITY_WINDOWS = {
    "baseline": "bootstrap",
    "expand": "old_and_new_app",
    "contract": "new_app_only",
}
EXPAND_ALLOWED_OPS = frozenset({"op.add_column", "op.create_index", "op.create_table", "op.execute"})
DESTRUCTIVE_SQL_PREFIXES = ("ALTER ", "DELETE ", "DROP ", "RENAME ", "TRUNCATE ")


@dataclass(frozen=True)
class MigrationFinding:
    code: str
    message: str
    path: str | None = None
    expected: str | None = None
    actual: str | None = None


@dataclass(frozen=True)
class MigrationRevision:
    path: Path
    revision: str | None
    down_revisions: tuple[str, ...]
    migration_phase: str | None
    release_compatibility: str | None
    has_upgrade: bool
    has_downgrade: bool
    has_docstring: bool
    has_downgrade_raise: bool
    upgrade_op_calls: tuple[OperationCall, ...]
    downgrade_op_calls: tuple[str, ...]


@dataclass(frozen=True)
class OperationCall:
    name: str
    line: int
    sql_statement: str | None = None
    has_not_null_column_without_default: bool = False
    table_name: str | None = None
    has_tenant_column: bool = False


def _repo_relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _migration_files(versions_dir: Path) -> list[Path]:
    return sorted(path for path in versions_dir.glob("*.py") if path.is_file() and path.name != "__init__.py")


def _assignment_value(module: ast.Module, name: str) -> ast.AST | None:
    for statement in module.body:
        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            if statement.target.id == name:
                return statement.value
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return statement.value
    return None


def _string_literal(value: ast.AST | None) -> str | None:
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    return None


def _down_revisions(value: ast.AST | None) -> tuple[str, ...]:
    if value is None or (isinstance(value, ast.Constant) and value.value is None):
        return ()
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return (value.value,)
    if isinstance(value, ast.List | ast.Tuple):
        revisions: list[str] = []
        for item in value.elts:
            if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
                return ()
            revisions.append(item.value)
        return tuple(revisions)
    return ()


def _function(module: ast.Module, name: str) -> ast.FunctionDef | None:
    for statement in module.body:
        if isinstance(statement, ast.FunctionDef) and statement.name == name:
            return statement
    return None


def _call_name(call: ast.Call) -> str:
    names: list[str] = []
    current: ast.AST = call.func
    while isinstance(current, ast.Attribute):
        names.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        names.append(current.id)
    return ".".join(reversed(names))


def _operation_calls(function: ast.FunctionDef | None) -> tuple[OperationCall, ...]:
    if function is None:
        return ()
    calls: list[OperationCall] = []
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name == "op" or name.startswith("op."):
                calls.append(
                    OperationCall(
                        name=name,
                        line=node.lineno,
                        sql_statement=_execute_sql_statement(name, node),
                        has_not_null_column_without_default=_has_not_null_add_column_without_default(name, node),
                        table_name=_create_table_name(name, node),
                        has_tenant_column=_has_tenant_column(name, node),
                    )
                )
    return tuple(sorted(calls, key=lambda item: (item.line, item.name)))


def _downgrade_op_calls(function: ast.FunctionDef | None) -> tuple[str, ...]:
    return tuple(sorted({item.name for item in _operation_calls(function)}))


def _execute_sql_statement(name: str, call: ast.Call) -> str | None:
    if name != "op.execute" or not call.args:
        return None
    statement = call.args[0]
    if isinstance(statement, ast.Constant) and isinstance(statement.value, str):
        return statement.value
    return None


def _has_not_null_add_column_without_default(name: str, call: ast.Call) -> bool:
    column_call = _add_column_call(name, call)
    if column_call is None:
        return False
    if not _keyword_is_false(column_call, "nullable"):
        return False
    return not _has_keyword_value(column_call, {"default", "server_default"})


def _create_table_name(name: str, call: ast.Call) -> str | None:
    if name != "op.create_table" or not call.args:
        return None
    table_arg = call.args[0]
    if isinstance(table_arg, ast.Constant) and isinstance(table_arg.value, str):
        return table_arg.value
    return None


def _has_tenant_column(name: str, call: ast.Call) -> bool:
    if name != "op.create_table":
        return False
    return any(_column_name(arg) == "tenant_id" for arg in call.args[1:])


def _column_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    if not _call_name(node).endswith("Column") or not node.args:
        return None
    column_arg = node.args[0]
    if isinstance(column_arg, ast.Constant) and isinstance(column_arg.value, str):
        return column_arg.value
    return None


def _add_column_call(name: str, call: ast.Call) -> ast.Call | None:
    if name != "op.add_column":
        return None
    column_arg = call.args[1] if len(call.args) >= 2 else _keyword_value(call, "column")
    if isinstance(column_arg, ast.Call) and _call_name(column_arg).endswith("Column"):
        return column_arg
    return None


def _keyword_value(call: ast.Call, keyword_name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == keyword_name:
            return keyword.value
    return None


def _keyword_is_false(call: ast.Call, keyword_name: str) -> bool:
    value = _keyword_value(call, keyword_name)
    return isinstance(value, ast.Constant) and value.value is False


def _has_keyword_value(call: ast.Call, keyword_names: set[str]) -> bool:
    for keyword in call.keywords:
        if keyword.arg in keyword_names and not _is_none(keyword.value):
            return True
    return False


def _is_none(value: ast.AST) -> bool:
    return isinstance(value, ast.Constant) and value.value is None


def _has_raise(function: ast.FunctionDef | None) -> bool:
    return bool(function and any(isinstance(node, ast.Raise) for node in ast.walk(function)))


def _load_revision(path: Path) -> tuple[MigrationRevision | None, list[MigrationFinding]]:
    try:
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return None, [
            MigrationFinding(
                code="migration_syntax_error",
                message=f"Migration file is not valid Python: {exc.msg}",
                path=_repo_relative(path),
            )
        ]
    revision = _string_literal(_assignment_value(module, "revision"))
    down_value = _assignment_value(module, "down_revision")
    migration_phase = _string_literal(_assignment_value(module, "migration_phase"))
    release_compatibility = _string_literal(_assignment_value(module, "release_compatibility"))
    upgrade = _function(module, "upgrade")
    downgrade = _function(module, "downgrade")
    return (
        MigrationRevision(
            path=path,
            revision=revision,
            down_revisions=_down_revisions(down_value),
            migration_phase=migration_phase,
            release_compatibility=release_compatibility,
            has_upgrade=upgrade is not None,
            has_downgrade=downgrade is not None,
            has_docstring=bool(ast.get_docstring(module)),
            has_downgrade_raise=_has_raise(downgrade),
            upgrade_op_calls=_operation_calls(upgrade),
            downgrade_op_calls=_downgrade_op_calls(downgrade),
        ),
        [],
    )


def _revision_findings(revision: MigrationRevision) -> list[MigrationFinding]:
    path = _repo_relative(revision.path)
    findings: list[MigrationFinding] = []
    if revision.revision is None:
        findings.append(MigrationFinding("migration_missing_revision", "Migration must declare revision", path=path))
    elif revision.revision != revision.path.stem.split("_", maxsplit=1)[0]:
        findings.append(
            MigrationFinding(
                "migration_revision_filename_mismatch",
                "Migration revision id must match the filename prefix",
                path=path,
                expected=revision.path.stem.split("_", maxsplit=1)[0],
                actual=revision.revision,
            )
        )
    if not revision.has_docstring:
        findings.append(
            MigrationFinding("migration_missing_docstring", "Migration must explain the schema change", path=path)
        )
    if not revision.has_upgrade:
        findings.append(MigrationFinding("migration_missing_upgrade", "Migration must define upgrade()", path=path))
    if not revision.has_downgrade:
        findings.append(MigrationFinding("migration_missing_downgrade", "Migration must define downgrade()", path=path))
    if len(revision.down_revisions) > 1:
        findings.append(
            MigrationFinding(
                "migration_multiple_down_revisions",
                "S55 keeps migration history linear; merge migrations need explicit review",
                path=path,
                actual=",".join(revision.down_revisions),
            )
        )
    findings.extend(_phase_findings(revision, path))
    if not revision.has_downgrade_raise:
        findings.append(
            MigrationFinding(
                "migration_downgrade_not_forward_fix_only",
                "downgrade() must raise so production rollback uses forward-fix/restore runbooks",
                path=path,
                expected="raise NotImplementedError",
            )
        )
    if revision.downgrade_op_calls:
        findings.append(
            MigrationFinding(
                "migration_downgrade_uses_schema_ops",
                "downgrade() must not run Alembic schema operations",
                path=path,
                actual=",".join(revision.downgrade_op_calls),
            )
        )
    return findings


def _phase_findings(revision: MigrationRevision, path: str) -> list[MigrationFinding]:
    phase = revision.migration_phase
    if phase is None:
        return [
            MigrationFinding(
                "migration_phase_missing",
                "Migration must declare migration_phase: baseline, expand, or contract",
                path=path,
            )
        ]
    if phase not in VALID_MIGRATION_PHASES:
        return [
            MigrationFinding(
                "migration_phase_invalid",
                "migration_phase must be baseline, expand, or contract",
                path=path,
                expected="baseline|expand|contract",
                actual=phase,
            )
        ]
    findings = _compatibility_window_findings(revision, path, phase)
    if phase == "baseline":
        return [*findings, *_baseline_phase_findings(revision, path)]
    if phase == "expand":
        return [*findings, *_expand_phase_findings(revision, path)]
    return [
        *findings,
        MigrationFinding(
            "migration_contract_phase_requires_old_writer_gate",
            "contract migrations remain blocked until old writer rejection and release-window proof exist",
            path=path,
        ),
    ]


def _compatibility_window_findings(
    revision: MigrationRevision,
    path: str,
    phase: str,
) -> list[MigrationFinding]:
    expected = PHASE_COMPATIBILITY_WINDOWS[phase]
    actual = revision.release_compatibility
    if actual is None:
        return [
            MigrationFinding(
                "migration_release_compatibility_missing",
                "Migration must declare the release compatibility window for rolling deploy review",
                path=path,
                expected=expected,
            )
        ]
    if actual == expected:
        return []
    return [
        MigrationFinding(
            "migration_release_compatibility_mismatch",
            "release_compatibility must match the migration phase",
            path=path,
            expected=expected,
            actual=actual,
        )
    ]


def _baseline_phase_findings(revision: MigrationRevision, path: str) -> list[MigrationFinding]:
    if not revision.down_revisions:
        return []
    return [
        MigrationFinding(
            "migration_baseline_has_parent",
            "Only the root migration may use migration_phase='baseline'",
            path=path,
            actual=",".join(revision.down_revisions),
        )
    ]


def _expand_phase_findings(revision: MigrationRevision, path: str) -> list[MigrationFinding]:
    findings: list[MigrationFinding] = []
    for operation in revision.upgrade_op_calls:
        if operation.name not in EXPAND_ALLOWED_OPS:
            findings.append(_expand_operation_finding(operation, path))
        if operation.has_not_null_column_without_default:
            findings.append(_expand_not_null_finding(operation, path))
        findings.extend(_expand_sql_findings(operation, path))
    findings.extend(_ai_tenant_rls_findings(revision, path))
    return findings


def _ai_tenant_rls_findings(revision: MigrationRevision, path: str) -> list[MigrationFinding]:
    statements = tuple(operation.sql_statement or "" for operation in revision.upgrade_op_calls)
    findings: list[MigrationFinding] = []
    for operation in revision.upgrade_op_calls:
        table_name = operation.table_name
        if table_name is None or not _requires_ai_tenant_rls(operation, table_name):
            continue
        missing = _missing_ai_tenant_rls(table_name, statements)
        if missing:
            findings.append(
                MigrationFinding(
                    "migration_ai_tenant_rls_missing",
                    "AI tables with tenant_id must enable/force Postgres RLS and create a tenant policy",
                    path=path,
                    expected="ENABLE RLS, FORCE RLS, tenant policy",
                    actual=f"{table_name}: missing {','.join(missing)}",
                )
            )
    return findings


def _requires_ai_tenant_rls(operation: OperationCall, table_name: str) -> bool:
    return operation.name == "op.create_table" and operation.has_tenant_column and table_name.startswith("ai_")


def _missing_ai_tenant_rls(table_name: str, statements: tuple[str, ...]) -> list[str]:
    normalized = tuple(" ".join(statement.split()).upper() for statement in statements)
    table = table_name.upper()
    missing: list[str] = []
    if not _contains_sql(normalized, f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"):
        missing.append("enable")
    if not _contains_sql(normalized, f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"):
        missing.append("force")
    if not _has_tenant_policy(table, normalized):
        missing.append("policy")
    return missing


def _contains_sql(statements: tuple[str, ...], needle: str) -> bool:
    return any(needle in statement for statement in statements)


def _has_tenant_policy(table_name: str, statements: tuple[str, ...]) -> bool:
    policy_name = f"CREATE POLICY {table_name}_TENANT_ISOLATION ON {table_name}"
    setting = "CURRENT_SETTING('FOUNDRY_LITE.TENANT_ID', TRUE)"
    return any(policy_name in statement and setting in statement for statement in statements)


def _expand_operation_finding(operation: OperationCall, path: str) -> MigrationFinding:
    return MigrationFinding(
        "migration_expand_operation_not_allowed",
        "expand migrations may only add compatible tables, columns, indexes, or safe backfill SQL",
        path=path,
        expected=",".join(sorted(EXPAND_ALLOWED_OPS)),
        actual=f"{operation.name}:line {operation.line}",
    )


def _expand_not_null_finding(operation: OperationCall, path: str) -> MigrationFinding:
    return MigrationFinding(
        "migration_expand_not_null_column_without_default",
        "expand migrations must give new NOT NULL columns a default for old writers",
        path=path,
        expected="server_default or default",
        actual=f"line {operation.line}",
    )


def _expand_sql_findings(operation: OperationCall, path: str) -> list[MigrationFinding]:
    if operation.name != "op.execute":
        return []
    if operation.sql_statement is None:
        return [
            MigrationFinding(
                "migration_expand_execute_not_literal",
                "expand op.execute calls must use literal SQL so the gate can screen destructive statements",
                path=path,
                actual=f"line {operation.line}",
            )
        ]
    statement = operation.sql_statement.lstrip().upper()
    if _is_safe_rls_statement(statement):
        return []
    if not statement.startswith(DESTRUCTIVE_SQL_PREFIXES):
        return []
    return [
        MigrationFinding(
            "migration_expand_execute_destructive_sql",
            "expand migrations must not run destructive SQL while old app versions may still run",
            path=path,
            actual=f"line {operation.line}",
        )
    ]


def _is_safe_rls_statement(statement: str) -> bool:
    normalized = " ".join(statement.split())
    return normalized.startswith("ALTER TABLE ") and normalized.endswith(
        (" ENABLE ROW LEVEL SECURITY", " FORCE ROW LEVEL SECURITY")
    )


def _graph_findings(revisions: list[MigrationRevision]) -> list[MigrationFinding]:
    by_revision, duplicate_findings = _revision_index_findings(revisions)
    references = _referenced_down_revisions(revisions)
    return [
        *duplicate_findings,
        *_missing_reference_findings(references, by_revision),
        *_root_head_findings(revisions, references),
        *_cycle_findings(by_revision),
    ]


def _revision_index_findings(
    revisions: list[MigrationRevision],
) -> tuple[dict[str, MigrationRevision], list[MigrationFinding]]:
    by_revision: dict[str, MigrationRevision] = {}
    duplicate_revisions: set[str] = set()
    for item in revisions:
        if item.revision is None:
            continue
        if item.revision in by_revision:
            duplicate_revisions.add(item.revision)
        by_revision[item.revision] = item
    findings = [
        MigrationFinding("migration_duplicate_revision", "Revision id must be unique", actual=duplicate)
        for duplicate in sorted(duplicate_revisions)
    ]
    return by_revision, findings


def _referenced_down_revisions(revisions: list[MigrationRevision]) -> set[str]:
    return {down for item in revisions for down in item.down_revisions}


def _missing_reference_findings(
    references: set[str],
    by_revision: dict[str, MigrationRevision],
) -> list[MigrationFinding]:
    return [
        MigrationFinding(
            "migration_down_revision_missing",
            "down_revision must point to an existing migration",
            actual=reference,
        )
        for reference in sorted(references)
        if reference not in by_revision
    ]


def _root_head_findings(revisions: list[MigrationRevision], references: set[str]) -> list[MigrationFinding]:
    findings: list[MigrationFinding] = []
    roots = [item.revision for item in revisions if item.revision is not None and not item.down_revisions]
    heads = [item.revision for item in revisions if item.revision is not None and item.revision not in references]
    if len(roots) != 1:
        findings.append(
            MigrationFinding(
                "migration_root_count_invalid",
                "Migration history must have exactly one root",
                expected="1",
                actual=str(len(roots)),
            )
        )
    if len(heads) != 1:
        findings.append(
            MigrationFinding(
                "migration_head_count_invalid",
                "Migration history must have exactly one head",
                expected="1",
                actual=str(len(heads)),
            )
        )
    return findings


def _cycle_findings(by_revision: dict[str, MigrationRevision]) -> list[MigrationFinding]:
    findings: list[MigrationFinding] = []
    visited: set[str] = set()
    visiting: set[str] = set()

    def visit(revision_id: str) -> None:
        if revision_id in visiting:
            findings.append(
                MigrationFinding("migration_cycle", "Migration history must not contain cycles", actual=revision_id)
            )
            return
        if revision_id in visited:
            return
        visiting.add(revision_id)
        item = by_revision.get(revision_id)
        if item is not None:
            for down_revision in item.down_revisions:
                if down_revision in by_revision:
                    visit(down_revision)
        visiting.remove(revision_id)
        visited.add(revision_id)

    for revision_id in sorted(by_revision):
        visit(revision_id)
    return findings


def collect_findings(*, versions_dir: Path = DEFAULT_VERSIONS_DIR) -> list[MigrationFinding]:
    if not versions_dir.exists():
        return [
            MigrationFinding(
                "migration_versions_dir_missing",
                "Alembic versions directory does not exist",
                path=_repo_relative(versions_dir),
            )
        ]
    files = _migration_files(versions_dir)
    if not files:
        return [
            MigrationFinding(
                "migration_versions_missing",
                "Alembic versions directory has no migration files",
                path=_repo_relative(versions_dir),
            )
        ]
    findings: list[MigrationFinding] = []
    revisions: list[MigrationRevision] = []
    for path in files:
        revision, load_findings = _load_revision(path)
        findings.extend(load_findings)
        if revision is not None:
            revisions.append(revision)
            findings.extend(_revision_findings(revision))
    findings.extend(_graph_findings(revisions))
    return findings


def write_report(output: Path, findings: list[MigrationFinding], *, versions_dir: Path = DEFAULT_VERSIONS_DIR) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    files = _migration_files(versions_dir) if versions_dir.exists() else []
    report = {
        "baseline": 0,
        "count": len(findings),
        "gate": "schema_migrations",
        "gate_pass": not findings,
        "migration_count": len(files),
        "versions_dir": _repo_relative(versions_dir),
        "violations": [asdict(finding) for finding in findings],
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(findings: list[MigrationFinding]) -> None:
    print("Schema migration gate failed: Alembic history is not operationally safe.")
    for finding in findings:
        location = f" {finding.path}" if finding.path else ""
        print(f"- {finding.code}{location}: {finding.message}")
        if finding.expected is not None or finding.actual is not None:
            print(f"  expected={finding.expected!r} actual={finding.actual!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Alembic migration history safety.")
    parser.add_argument("--versions-dir", type=Path, default=DEFAULT_VERSIONS_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    versions_dir = args.versions_dir if args.versions_dir.is_absolute() else ROOT / args.versions_dir
    findings = collect_findings(versions_dir=versions_dir)
    write_report(args.output, findings, versions_dir=versions_dir)
    if findings:
        _print_failure(findings)
        return 1
    print(
        "Schema migration gate passed: Alembic history is single-headed, "
        "forward-fix safe, and compatibility-window guarded."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
