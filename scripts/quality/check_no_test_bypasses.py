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


def main() -> int:
    violations: list[str] = []
    for path in sorted(TESTS.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = _call_name(node.func)
                if name in {"pytest.skip", "pytest.xfail"}:
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno} uses {name}")
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                for decorator in node.decorator_list:
                    name = _call_name(decorator.func if isinstance(decorator, ast.Call) else decorator)
                    if name in {"pytest.mark.skip", "pytest.mark.skipif", "pytest.mark.xfail"}:
                        violations.append(f"{path.relative_to(ROOT)}:{node.lineno} uses @{name}")

    if violations:
        print("Release gate blocked: skipped/flaky/xfail tests are not allowed.")
        for violation in violations:
            print(f"- {violation}")
        return 1
    print("No skip/xfail test bypasses found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
