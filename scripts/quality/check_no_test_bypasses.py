from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
BYPASS_CALLS = {
    "pytest.skip",
    "pytest.xfail",
    "pytest.mark.skip",
    "pytest.mark.skipif",
    "pytest.mark.xfail",
}
POSTGRES_LOCAL_OPT_OUT_FILES = {
    Path("tests/contracts/conftest.py"),
}
POSTGRES_LOCAL_OPT_OUT_MARKERS = (
    "FOUNDRY_LITE_SKIP_POSTGRES_CONTRACTS",
    "postgres contract tests disabled",
)


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _is_allowed_local_postgres_opt_out(path: Path, name: str, source: str) -> bool:
    if path.relative_to(ROOT) not in POSTGRES_LOCAL_OPT_OUT_FILES:
        return False
    if name not in {"pytest.skip", "pytest.mark.skipif"}:
        return False
    return any(marker in source for marker in POSTGRES_LOCAL_OPT_OUT_MARKERS)


def _call_violation(path: Path, source: str, node: ast.Call) -> str | None:
    name = _call_name(node.func)
    if name in BYPASS_CALLS:
        source_segment = ast.get_source_segment(source, node) or ""
        if _is_allowed_local_postgres_opt_out(path, name, source_segment):
            return None
        return f"{path.relative_to(ROOT)}:{node.lineno} uses {name}"
    return None


def _decorator_violations(path: Path, source: str, node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    violations: list[str] = []
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Call):
            name = _call_name(decorator.func)
        else:
            name = _call_name(decorator)
        source_segment = ast.get_source_segment(source, decorator) or ""
        if name in BYPASS_CALLS and not _is_allowed_local_postgres_opt_out(path, name, source_segment):
            violations.append(f"{path.relative_to(ROOT)}:{decorator.lineno} uses @{name}")
    return violations


def _decorator_call_ids(tree: ast.AST) -> set[int]:
    calls: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            calls.update(id(decorator) for decorator in node.decorator_list if isinstance(decorator, ast.Call))
    return calls


def _violations_for_file(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    decorator_calls = _decorator_call_ids(tree)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and id(node) not in decorator_calls:
            violation = _call_violation(path, source, node)
            if violation:
                violations.append(violation)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            violations.extend(_decorator_violations(path, source, node))
    return violations


def _print_violations(violations: list[str]) -> None:
    print("Release gate blocked: skipped/flaky/xfail tests are not allowed.")
    for violation in violations:
        print(f"- {violation}")


def main() -> int:
    violations: list[str] = []
    for path in sorted(TESTS.rglob("*.py")):
        violations.extend(_violations_for_file(path))

    if violations:
        _print_violations(violations)
        return 1
    print("No skip/xfail test bypasses found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
