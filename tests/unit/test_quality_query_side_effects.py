from __future__ import annotations

import json
from pathlib import Path

from scripts.quality import check_query_side_effects as gate


def _write_service(root: Path, text: str) -> Path:
    path = root / "services" / "example_service.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_query_side_effect_gate_passes_read_only_repository_call(tmp_path: Path) -> None:
    _write_service(
        tmp_path,
        """
class ExampleService:
    def get_order(self):
        return self.order_repository.order_by_id()
""",
    )

    assert gate.collect_findings(services_root=tmp_path / "services", project_root=tmp_path) == []


def test_query_side_effect_gate_flags_repository_write(tmp_path: Path) -> None:
    _write_service(
        tmp_path,
        """
class ExampleService:
    def get_order(self):
        self.order_repository.insert_order()
""",
    )

    findings = gate.collect_findings(services_root=tmp_path / "services", project_root=tmp_path)

    assert [finding.method for finding in findings] == ["get_order"]
    assert findings[0].side_effects == ("self.order_repository.insert_order",)


def test_query_side_effect_gate_flags_hidden_helper_audit_write(tmp_path: Path) -> None:
    _write_service(
        tmp_path,
        """
class ExampleService:
    def get_order(self):
        return self._load_order()

    def _load_order(self):
        self.runtime_service._audit()
""",
    )

    findings = gate.collect_findings(services_root=tmp_path / "services", project_root=tmp_path)

    assert [finding.method for finding in findings] == ["get_order"]
    assert findings[0].side_effects == ("self.runtime_service._audit",)


def test_query_side_effect_gate_flags_file_write(tmp_path: Path) -> None:
    _write_service(
        tmp_path,
        """
class ExampleService:
    def preview_order(self, path):
        path.write_text("changed")
""",
    )

    findings = gate.collect_findings(services_root=tmp_path / "services", project_root=tmp_path)

    assert [finding.method for finding in findings] == ["preview_order"]
    assert findings[0].side_effects == ("path.write_text",)


def test_query_side_effect_gate_flags_mutation_collaborator(tmp_path: Path) -> None:
    _write_service(
        tmp_path,
        """
class ExampleService:
    def query_orders(self):
        return self.action_service.apply_action()

class ActionService:
    def apply_action(self):
        return {"status": "ok"}
""",
    )

    findings = gate.collect_findings(services_root=tmp_path / "services", project_root=tmp_path)

    assert [finding.method for finding in findings] == ["query_orders"]
    assert findings[0].side_effects == ("ActionService.apply_action",)


def test_query_side_effect_gate_flags_write_mode_open(tmp_path: Path) -> None:
    _write_service(
        tmp_path,
        """
class ExampleService:
    def inspect_order(self):
        with open("out.txt", "w") as handle:
            handle.write("changed")
""",
    )

    findings = gate.collect_findings(services_root=tmp_path / "services", project_root=tmp_path)

    assert [finding.method for finding in findings] == ["inspect_order"]
    assert findings[0].side_effects == ("open[write-mode]",)


def test_query_side_effect_gate_writes_json_report(tmp_path: Path) -> None:
    output = tmp_path / "artifacts" / "quality" / "query_side_effects.json"
    findings = [
        gate.QuerySideEffectFinding(
            path="services/example_service.py",
            line=2,
            class_name="ExampleService",
            method="get_order",
            side_effects=("self.order_repository.insert_order",),
            message="public query/read method can reach state-changing behavior",
        )
    ]

    gate.write_report(output, findings)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["count"] == 1
    assert report["baseline"] == 0
    assert report["gate_pass"] is False
    assert report["violations"][0]["method"] == "get_order"
