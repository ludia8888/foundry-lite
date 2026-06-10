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
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "service_dependencies.json"

ALLOWED_NON_DEPENDENCY_ATTRS = {
    # Built-in protocol/dunder access:
    "__class__",
    "__dict__",
    "__doc__",
    "__module__",
    # Helper methods defined within services themselves. Pattern
    # `self._helper_method(...)` is allowed because pyright/mypy enforce
    # method resolution against the service class.
}
NON_LEAF_SERVICE_CLASSES = {"CoreService", "CoreServices", "DatasetServices", "ObjectServices"}


@dataclass(frozen=True)
class DependencyAccess:
    path: str
    line: int
    class_name: str
    attribute: str


@dataclass(frozen=True)
class DependencyDeclarationFinding:
    path: str
    class_name: str
    message: str


@dataclass(frozen=True)
class ServiceDependencyAnalysis:
    findings: list[DependencyAccess]
    declaration_findings: list[DependencyDeclarationFinding]
    dependency_declarations: dict[str, list[str]]
    collaborator_declarations: dict[str, list[str]]


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


def _is_leaf_service_class(node: ast.ClassDef) -> bool:
    return node.name.endswith("Service") and node.name not in NON_LEAF_SERVICE_CLASSES


def _class_for_node(tree: ast.AST, target: ast.AST) -> str | None:
    """Return the enclosing class name for a node, or None."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for child in ast.walk(node):
                if child is target:
                    return node.name
    return None


def _collect_service_method_names(tree: ast.AST) -> dict[str, set[str]]:
    """Collect method names defined directly within each service class.

    ``self.method_name(...)`` should be local to the current service. Calls to
    another service must go through an explicit collaborator attribute such as
    ``self.runtime_service._audit(...)``.
    """
    method_names: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            methods: set[str] = set()
            for item in node.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    methods.add(item.name)
            method_names[node.name] = methods
    return method_names


def _collect_declared_class_attributes(tree: ast.AST) -> dict[str, set[str]]:
    """Collect typed class attributes declared directly on each service class.

    Constructor-injected service groups declare collaborators as dataclass
    fields. Accessing those via ``self.<field>`` is an explicit dependency,
    not hidden dynamic coupling.
    """

    declared: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        attributes: set[str] = set()
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                attributes.add(item.target.id)
        declared[node.name] = attributes
    return declared


def _required_string_names(node: ast.AST) -> set[str] | None:
    if isinstance(node, ast.Tuple | ast.List | ast.Set):
        values: set[str] = set()
        for item in node.elts:
            if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
                return None
            values.add(item.value)
        return values
    return None


def _collect_required_names(tree: ast.AST, declaration_name: str) -> dict[str, set[str]]:
    declarations: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or not _is_leaf_service_class(node):
            continue
        for item in node.body:
            if not isinstance(item, ast.Assign):
                continue
            if not any(isinstance(target, ast.Name) and target.id == declaration_name for target in item.targets):
                continue
            required_names = _required_string_names(item.value)
            if required_names is not None:
                declarations[node.name] = required_names
    return declarations


def _collect_required_dependencies(tree: ast.AST) -> dict[str, set[str]]:
    return _collect_required_names(tree, "required_dependencies")


def _collect_required_collaborators(tree: ast.AST) -> dict[str, set[str]]:
    return _collect_required_names(tree, "required_collaborators")


def _service_collaborators_value(node: ast.AST) -> ast.expr | None:
    if isinstance(node, ast.Assign):
        if any(isinstance(t, ast.Name) and t.id == "SERVICE_COLLABORATORS" for t in node.targets):
            return node.value
    elif isinstance(node, ast.AnnAssign):
        if isinstance(node.target, ast.Name) and node.target.id == "SERVICE_COLLABORATORS":
            return node.value
    return None


def _collect_collaborator_attributes(paths: list[Path]) -> set[str]:
    """Read the SERVICE_COLLABORATORS registry in base.py as the source of truth.

    Collaborator attributes used to be declared as 16 ``Any`` annotations on the
    ``CoreService`` base class. They were retired so each concrete service only
    annotates the collaborators it actually receives. The registry constant
    ``SERVICE_COLLABORATORS`` in ``application/services/base.py`` now owns the
    canonical name list; ``tests/unit/test_quality_service_wiring.py`` keeps it
    in sync with this gate.
    """

    collaborators: set[str] = set()
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            value = _service_collaborators_value(node)
            if not isinstance(value, ast.Dict):
                continue
            for key in value.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    collaborators.add(key.value)
    return collaborators


def _service_files() -> list[Path]:
    return sorted(SERVICE_ROOT.rglob("*.py"))


def _is_allowed_non_dependency_attribute(
    attr: str,
    class_name: str,
    *,
    method_names: dict[str, set[str]],
    declared_class_attrs: dict[str, set[str]],
) -> bool:
    return (
        attr in method_names.get(class_name, set())
        or attr in declared_class_attrs.get(class_name, set())
        or attr in ALLOWED_NON_DEPENDENCY_ATTRS
        or attr.startswith("_")
    )


def _check_attribute_accesses(
    path: Path,
    tree: ast.AST,
    *,
    dependency_attrs: set[str],
    collaborator_attrs: set[str],
    method_names: dict[str, set[str]],
    declared_class_attrs: dict[str, set[str]],
) -> tuple[list[DependencyAccess], dict[str, set[str]], dict[str, set[str]]]:
    findings: list[DependencyAccess] = []
    dependency_accesses: dict[str, set[str]] = {}
    collaborator_accesses: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if not _is_self_attribute(node):
            continue
        attr = node.attr
        class_name = _class_for_node(tree, node) or ""
        if attr in dependency_attrs:
            dependency_accesses.setdefault(class_name, set()).add(attr)
            continue
        if attr in collaborator_attrs:
            collaborator_accesses.setdefault(class_name, set()).add(attr)
            continue
        if _is_allowed_non_dependency_attribute(
            attr,
            class_name,
            method_names=method_names,
            declared_class_attrs=declared_class_attrs,
        ):
            continue
        findings.append(
            DependencyAccess(
                path=str(path.relative_to(ROOT)),
                line=node.lineno,
                class_name=class_name,
                attribute=attr,
            )
        )
    return findings, dependency_accesses, collaborator_accesses


def _check_required_declarations(
    path: Path,
    tree: ast.AST,
    *,
    allowed_attrs: set[str],
    declaration_name: str,
    label: str,
    declarations: dict[str, set[str]],
    accesses: dict[str, set[str]],
) -> list[DependencyDeclarationFinding]:
    findings: list[DependencyDeclarationFinding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or not _is_leaf_service_class(node):
            continue
        declared = declarations.get(node.name)
        if declared is None:
            findings.append(
                DependencyDeclarationFinding(
                    path=str(path.relative_to(ROOT)),
                    class_name=node.name,
                    message=f"missing {declaration_name} declaration",
                )
            )
            continue
        unknown = sorted(declared - allowed_attrs)
        used = accesses.get(node.name, set())
        undeclared = sorted(used - declared)
        unused = sorted(declared - used)
        if unknown:
            findings.append(
                DependencyDeclarationFinding(
                    path=str(path.relative_to(ROOT)),
                    class_name=node.name,
                    message=f"declares unknown {label}: {unknown}",
                )
            )
        if undeclared:
            findings.append(
                DependencyDeclarationFinding(
                    path=str(path.relative_to(ROOT)),
                    class_name=node.name,
                    message=f"uses undeclared {label}: {undeclared}",
                )
            )
        if unused:
            findings.append(
                DependencyDeclarationFinding(
                    path=str(path.relative_to(ROOT)),
                    class_name=node.name,
                    message=f"declares unused {label}: {unused}",
                )
            )
    return findings


def _check_dependency_declarations(
    path: Path,
    tree: ast.AST,
    *,
    dependency_attrs: set[str],
    declarations: dict[str, set[str]],
    dependency_accesses: dict[str, set[str]],
) -> list[DependencyDeclarationFinding]:
    return _check_required_declarations(
        path,
        tree,
        allowed_attrs=dependency_attrs,
        declaration_name="required_dependencies",
        label="dependencies",
        declarations=declarations,
        accesses=dependency_accesses,
    )


def _check_collaborator_declarations(
    path: Path,
    tree: ast.AST,
    *,
    collaborator_attrs: set[str],
    declarations: dict[str, set[str]],
    collaborator_accesses: dict[str, set[str]],
) -> list[DependencyDeclarationFinding]:
    return _check_required_declarations(
        path,
        tree,
        allowed_attrs=collaborator_attrs,
        declaration_name="required_collaborators",
        label="collaborators",
        declarations=declarations,
        accesses=collaborator_accesses,
    )


def _analyze_service_file(
    path: Path,
    *,
    dependency_attrs: set[str],
    collaborator_attrs: set[str],
) -> ServiceDependencyAnalysis:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    findings, dependency_accesses, collaborator_accesses = _check_attribute_accesses(
        path,
        tree,
        dependency_attrs=dependency_attrs,
        collaborator_attrs=collaborator_attrs,
        method_names=_collect_service_method_names(tree),
        declared_class_attrs=_collect_declared_class_attributes(tree),
    )
    dependency_declarations = _collect_required_dependencies(tree)
    collaborator_declarations = _collect_required_collaborators(tree)
    declaration_findings = _check_dependency_declarations(
        path,
        tree,
        dependency_attrs=dependency_attrs,
        declarations=dependency_declarations,
        dependency_accesses=dependency_accesses,
    )
    declaration_findings.extend(
        _check_collaborator_declarations(
            path,
            tree,
            collaborator_attrs=collaborator_attrs,
            declarations=collaborator_declarations,
            collaborator_accesses=collaborator_accesses,
        )
    )
    return ServiceDependencyAnalysis(
        findings=findings,
        declaration_findings=declaration_findings,
        dependency_declarations={class_name: sorted(names) for class_name, names in dependency_declarations.items()},
        collaborator_declarations={
            class_name: sorted(names) for class_name, names in collaborator_declarations.items()
        },
    )


def _finding_rows(findings: list[DependencyAccess]) -> list[dict[str, object]]:
    return [
        {
            "path": finding.path,
            "line": finding.line,
            "class": finding.class_name,
            "attribute": finding.attribute,
        }
        for finding in findings
    ]


def _declaration_finding_rows(findings: list[DependencyDeclarationFinding]) -> list[dict[str, str]]:
    return [
        {
            "path": finding.path,
            "class": finding.class_name,
            "message": finding.message,
        }
        for finding in findings
    ]


def _write_report(
    output: Path,
    *,
    dependency_attrs: set[str],
    collaborator_attrs: set[str],
    dependency_declarations: dict[str, list[str]],
    collaborator_declarations: dict[str, list[str]],
    findings: list[DependencyAccess],
    declaration_findings: list[DependencyDeclarationFinding],
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "core_dependency_attributes": sorted(dependency_attrs),
        "collaborator_attributes": sorted(collaborator_attrs),
        "service_required_dependencies": dependency_declarations,
        "service_required_collaborators": collaborator_declarations,
        "findings": _finding_rows(findings),
        "declaration_findings": _declaration_finding_rows(declaration_findings),
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure_report(
    output: Path,
    *,
    findings: list[DependencyAccess],
    declaration_findings: list[DependencyDeclarationFinding],
) -> None:
    print(
        "Service uses self.<attr> that is neither a CoreDependencies field, "
        "an explicit collaborator, local method, declared constructor field, nor a private helper:"
    )
    for access_finding in findings:
        print(f"- {access_finding.path}:{access_finding.line} {access_finding.class_name}.{access_finding.attribute}")
    for declaration_finding in declaration_findings:
        print(f"- {declaration_finding.path} {declaration_finding.class_name}: {declaration_finding.message}")
    print(f"Report: {output.relative_to(ROOT)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    dependency_attrs = _core_dependency_attributes()
    service_files = _service_files()
    collaborator_attrs = _collect_collaborator_attributes(service_files)

    all_findings: list[DependencyAccess] = []
    declaration_findings: list[DependencyDeclarationFinding] = []
    dependency_declarations_report: dict[str, list[str]] = {}
    collaborator_declarations_report: dict[str, list[str]] = {}
    for path in service_files:
        if "__pycache__" in path.parts:
            continue
        analysis = _analyze_service_file(
            path,
            dependency_attrs=dependency_attrs,
            collaborator_attrs=collaborator_attrs,
        )
        all_findings.extend(analysis.findings)
        declaration_findings.extend(analysis.declaration_findings)
        dependency_declarations_report.update(analysis.dependency_declarations)
        collaborator_declarations_report.update(analysis.collaborator_declarations)

    _write_report(
        args.output,
        dependency_attrs=dependency_attrs,
        collaborator_attrs=collaborator_attrs,
        dependency_declarations=dependency_declarations_report,
        collaborator_declarations=collaborator_declarations_report,
        findings=all_findings,
        declaration_findings=declaration_findings,
    )

    if all_findings or declaration_findings:
        _print_failure_report(args.output, findings=all_findings, declaration_findings=declaration_findings)
        return 1

    print(
        "Service dependency access OK: "
        f"{len(dependency_declarations_report)} services declare only the CoreDependencies fields they use; "
        f"{len(collaborator_declarations_report)} services declare only the collaborators they use."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
