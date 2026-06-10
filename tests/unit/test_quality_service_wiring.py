from __future__ import annotations

import os as _os

if not _os.path.exists(_os.path.join(_os.path.dirname(__file__), "..", "..", "scripts", "quality")):
    import pytest

    pytest.skip(
        "quality self-tests require the scripts/ tree; skipped under mutmut's mutants/ copy.",
        allow_module_level=True,
    )

import ast
from pathlib import Path

from scripts.quality import check_service_call_graph, check_service_dependencies


def _write(root: Path, relative_path: str, source: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def test_service_dependency_gate_requires_exact_declared_dependencies(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(check_service_dependencies, "ROOT", tmp_path)
    path = _write(
        tmp_path,
        "libs/foundry_lite/application/services/example.py",
        """
class ExampleService:
    required_dependencies = ("engine",)
    required_collaborators = ("runtime_service",)

    def run(self):
        self.engine.begin()
        self.dataset_storage.dataset_uri("raw.orders")
        self.runtime_service._audit()
""",
    )
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    method_names = check_service_dependencies._collect_service_method_names(tree)
    declared_attrs = check_service_dependencies._collect_declared_class_attributes(tree)
    findings, dependency_accesses, _ = check_service_dependencies._check_attribute_accesses(
        path,
        tree,
        dependency_attrs={"engine", "dataset_storage"},
        collaborator_attrs={"runtime_service"},
        method_names=method_names,
        declared_class_attrs=declared_attrs,
    )

    declaration_findings = check_service_dependencies._check_dependency_declarations(
        path,
        tree,
        dependency_attrs={"engine", "dataset_storage"},
        declarations=check_service_dependencies._collect_required_dependencies(tree),
        dependency_accesses=dependency_accesses,
    )

    assert findings == []
    assert [finding.message for finding in declaration_findings] == [
        "uses undeclared dependencies: ['dataset_storage']"
    ]


def test_service_dependency_gate_rejects_unused_declared_dependencies(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(check_service_dependencies, "ROOT", tmp_path)
    path = _write(
        tmp_path,
        "libs/foundry_lite/application/services/example.py",
        """
class ExampleService:
    required_dependencies = ("engine", "dataset_storage")
    required_collaborators = ()

    def run(self):
        self.engine.begin()
""",
    )
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    _, dependency_accesses, _ = check_service_dependencies._check_attribute_accesses(
        path,
        tree,
        dependency_attrs={"engine", "dataset_storage"},
        collaborator_attrs=set(),
        method_names=check_service_dependencies._collect_service_method_names(tree),
        declared_class_attrs=check_service_dependencies._collect_declared_class_attributes(tree),
    )

    declaration_findings = check_service_dependencies._check_dependency_declarations(
        path,
        tree,
        dependency_attrs={"engine", "dataset_storage"},
        declarations=check_service_dependencies._collect_required_dependencies(tree),
        dependency_accesses=dependency_accesses,
    )

    assert [finding.message for finding in declaration_findings] == [
        "declares unused dependencies: ['dataset_storage']"
    ]


def test_service_dependency_gate_requires_exact_declared_collaborators(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(check_service_dependencies, "ROOT", tmp_path)
    path = _write(
        tmp_path,
        "libs/foundry_lite/application/services/example.py",
        """
class ExampleService:
    required_dependencies = ()
    required_collaborators = ("runtime_service",)

    def run(self):
        self.runtime_service._audit()
        self.ontology_service._active_object_type()
""",
    )
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    _, _, collaborator_accesses = check_service_dependencies._check_attribute_accesses(
        path,
        tree,
        dependency_attrs=set(),
        collaborator_attrs={"runtime_service", "ontology_service"},
        method_names=check_service_dependencies._collect_service_method_names(tree),
        declared_class_attrs=check_service_dependencies._collect_declared_class_attributes(tree),
    )

    declaration_findings = check_service_dependencies._check_collaborator_declarations(
        path,
        tree,
        collaborator_attrs={"runtime_service", "ontology_service"},
        declarations=check_service_dependencies._collect_required_collaborators(tree),
        collaborator_accesses=collaborator_accesses,
    )

    assert [finding.message for finding in declaration_findings] == [
        "uses undeclared collaborators: ['ontology_service']"
    ]


def test_service_dependency_gate_rejects_unused_declared_collaborators(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(check_service_dependencies, "ROOT", tmp_path)
    path = _write(
        tmp_path,
        "libs/foundry_lite/application/services/example.py",
        """
class ExampleService:
    required_dependencies = ()
    required_collaborators = ("runtime_service", "ontology_service")

    def run(self):
        self.runtime_service._audit()
""",
    )
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    _, _, collaborator_accesses = check_service_dependencies._check_attribute_accesses(
        path,
        tree,
        dependency_attrs=set(),
        collaborator_attrs={"runtime_service", "ontology_service"},
        method_names=check_service_dependencies._collect_service_method_names(tree),
        declared_class_attrs=check_service_dependencies._collect_declared_class_attributes(tree),
    )

    declaration_findings = check_service_dependencies._check_collaborator_declarations(
        path,
        tree,
        collaborator_attrs={"runtime_service", "ontology_service"},
        declarations=check_service_dependencies._collect_required_collaborators(tree),
        collaborator_accesses=collaborator_accesses,
    )

    assert [finding.message for finding in declaration_findings] == [
        "declares unused collaborators: ['ontology_service']"
    ]


def test_service_call_graph_reads_explicit_collaborator_calls(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(check_service_call_graph, "ROOT", tmp_path)
    monkeypatch.setattr(check_service_call_graph, "SERVICE_ROOT", tmp_path / "services")
    _write(
        tmp_path,
        "services/base.py",
        """
SERVICE_COLLABORATORS: dict[str, str] = {
    "runtime_service": "RuntimeService",
}
""",
    )
    caller = _write(
        tmp_path,
        "services/caller.py",
        """
class CallerService:
    def run(self):
        self.runtime_service._audit()


class RuntimeService:
    def _audit(self):
        pass
""",
    )
    paths = check_service_call_graph._service_files()
    methods = check_service_call_graph._collect_service_methods(paths)
    collaborators = check_service_call_graph._collect_collaborator_service_classes(paths)

    calls = check_service_call_graph._collect_cross_service_calls(paths, methods, collaborators)

    assert [(call.caller_service, call.callee_service, call.method_name, call.path, call.line) for call in calls] == [
        ("CallerService", "RuntimeService", "_audit", str(caller.relative_to(tmp_path)), 4)
    ]
