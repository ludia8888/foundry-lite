from __future__ import annotations

import ast
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE_ROOT = ROOT / "libs" / "foundry_lite" / "application" / "services"
NON_LEAF_SERVICE_CLASSES = {"CoreService", "CoreServices", "DatasetServices", "ObjectServices"}


@dataclass(frozen=True)
class MethodDefinition:
    class_name: str
    path: str
    line: int


def _method_names(node: ast.ClassDef) -> list[tuple[str, int]]:
    methods: list[tuple[str, int]] = []
    for item in node.body:
        if (
            isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
            and not item.name.startswith("__")
            and not _has_decorator(item, "overload")
        ):
            methods.append((item.name, item.lineno))
    return methods


def _has_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef, decorator_name: str) -> bool:
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Name) and decorator.id == decorator_name:
            return True
        if isinstance(decorator, ast.Attribute) and decorator.attr == decorator_name:
            return True
    return False


def _is_leaf_service_class(node: ast.ClassDef) -> bool:
    return node.name.endswith("Service") and node.name not in NON_LEAF_SERVICE_CLASSES


def collect_methods() -> dict[str, list[MethodDefinition]]:
    by_name: dict[str, list[MethodDefinition]] = defaultdict(list)
    for path in sorted(SERVICE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and _is_leaf_service_class(node):
                for method_name, line in _method_names(node):
                    by_name[method_name].append(
                        MethodDefinition(
                            class_name=node.name,
                            path=str(path.relative_to(ROOT)),
                            line=line,
                        )
                    )
    return by_name


def main() -> int:
    by_name = collect_methods()
    conflicts = {name: definitions for name, definitions in by_name.items() if len(definitions) > 1}
    if conflicts:
        print("Service method name conflicts found. Rename or extract an explicit collaborator:")
        for name, definitions in sorted(conflicts.items()):
            locations = ", ".join(
                f"{definition.class_name} at {definition.path}:{definition.line}" for definition in definitions
            )
            print(f"- {name}: {locations}")
        return 1
    print("Service method conflict guard OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
