from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _call_violation(path: Path, node: ast.Call) -> str | None:
    name = _call_name(node.func)
    if name in {"pytest.skip", "pytest.xfail"}:
        return f"{path.relative_to(ROOT)}:{node.lineno} uses {name}"
    return None


def _decorator_violations(path: Path, node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    violations: list[str] = []
    for decorator in node.decorator_list:
        name = _call_name(decorator.func if isinstance(decorator, ast.Call) else decorator)
        if name in {"pytest.mark.skip", "pytest.mark.skipif", "pytest.mark.xfail"}:
            violations.append(f"{path.relative_to(ROOT)}:{node.lineno} uses @{name}")
    return violations


def _violations_for_file(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            violation = _call_violation(path, node)
            if violation:
                violations.append(violation)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            violations.extend(_decorator_violations(path, node))
    return violations


def _print_violations(violations: list[str]) -> None:
    print("Release gate blocked: skipped/flaky/xfail tests are not allowed.")
    for violation in violations:
        print(f"- {violation}")


def main() -> int:
    violations: list[str] = []
    for path in sorted(TESTS.rglob("test_*.py")):
        violations.extend(_violations_for_file(path))

    if violations:
        _print_violations(violations)
        return 1
    print("No skip/xfail test bypasses found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
