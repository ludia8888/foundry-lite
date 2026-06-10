from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE_ROOT = ROOT / "libs" / "foundry_lite" / "application" / "services"
DEPENDENCIES_PATH = ROOT / "libs" / "foundry_lite" / "application" / "dependencies.py"
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "service_mixin_dependencies.json"

ALLOWED_NON_DEPENDENCY_ATTRS = {
    # Built-in protocol/dunder access:
    "__class__",
    "__dict__",
    "__doc__",
    "__module__",
    # Helper methods defined within service mixins themselves. Pattern
    # `self._helper_method(...)` is allowed because pyright/mypy enforce
    # method resolution against the mixin tree.
}


@dataclass(frozen=True)
class DependencyAccess:
    path: str
    line: int
    class_name: str
    attribute: str


def _core_dependency_attributes() -> set[str]:
    """Parse CoreDependencies dataclass and return the declared attribute names."""
    tree = ast.parse(DEPENDENCIES_PATH.read_text(encoding="utf-8"))
    attributes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "CoreDependencies":
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    attributes.add(item.target.id)
    return attributes


def _is_self_attribute(node: ast.Attribute) -> bool:
    return isinstance(node.value, ast.Name) and node.value.id == "self"


def _class_for_node(tree: ast.AST, target: ast.AST) -> str | None:
    """Return the enclosing class name for a node, or None."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for child in ast.walk(node):
                if child is target:
                    return node.name
    return None


def _collect_mixin_method_names(tree: ast.AST) -> set[str]:
    """Collect method names defined within service mixin classes.

    `self.method_name(...)` calls inside a service mixin should reference
    methods defined in that same mixin or another service mixin in the
    facade's MRO. We allow any name we see defined as a function inside any
    class in the services tree.
    """
    method_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    method_names.add(item.name)
    return method_names


def _collect_all_method_names(paths: list[Path]) -> set[str]:
    """Union of method names defined across the service mixin tree."""
    method_names: set[str] = set()
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        method_names |= _collect_mixin_method_names(tree)
    return method_names


def _service_files() -> list[Path]:
    return sorted(SERVICE_ROOT.rglob("*.py"))


def _check_attribute_accesses(
    path: Path,
    tree: ast.AST,
    *,
    dependency_attrs: set[str],
    method_names: set[str],
) -> list[DependencyAccess]:
    findings: list[DependencyAccess] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if not _is_self_attribute(node):
            continue
        attr = node.attr
        if (
            attr in dependency_attrs
            or attr in method_names
            or attr in ALLOWED_NON_DEPENDENCY_ATTRS
            or attr.startswith("_")
        ):
            continue
        class_name = _class_for_node(tree, node) or ""
        findings.append(
            DependencyAccess(
                path=str(path.relative_to(ROOT)),
                line=node.lineno,
                class_name=class_name,
                attribute=attr,
            )
        )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    dependency_attrs = _core_dependency_attributes()
    service_files = _service_files()
    method_names = _collect_all_method_names(service_files)

    all_findings: list[DependencyAccess] = []
    for path in service_files:
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        all_findings.extend(
            _check_attribute_accesses(
                path,
                tree,
                dependency_attrs=dependency_attrs,
                method_names=method_names,
            )
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "core_dependency_attributes": sorted(dependency_attrs),
        "findings": [
            {
                "path": finding.path,
                "line": finding.line,
                "class": finding.class_name,
                "attribute": finding.attribute,
            }
            for finding in all_findings
        ],
    }
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    if all_findings:
        print(
            "Service mixin uses self.<attr> that is neither a CoreDependencies field, "
            "a service-mixin method, nor a private helper:"
        )
        for finding in all_findings:
            print(f"- {finding.path}:{finding.line} {finding.class_name}.{finding.attribute}")
        print(f"Report: {args.output.relative_to(ROOT)}")
        return 1

    print(
        "Service mixin dependency access OK: "
        f"all public self.<attr> in services match CoreDependencies "
        f"({len(dependency_attrs)} fields) or another service mixin method "
        f"({len(method_names)} methods)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
