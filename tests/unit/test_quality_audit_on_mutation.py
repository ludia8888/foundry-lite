from __future__ import annotations

import json
from pathlib import Path

from scripts.quality import check_audit_on_mutation as gate


def _service_file(tmp_path: Path, source: str) -> Path:
    service = tmp_path / "libs" / "foundry_lite" / "application" / "services" / "order_service.py"
    service.parent.mkdir(parents=True)
    service.write_text(source, encoding="utf-8")
    return service


def test_audit_on_mutation_gate_flags_public_mutation_without_audit(tmp_path: Path) -> None:
    _service_file(
        tmp_path,
        """
class OrderService:
    def create_order(self):
        self.order_repository.insert_order(transaction=None)
""".strip(),
    )

    findings = gate.collect_findings(
        services_root=tmp_path / "libs" / "foundry_lite" / "application" / "services",
        project_root=tmp_path,
    )

    assert [finding.method for finding in findings] == ["create_order"]
    assert findings[0].path == "libs/foundry_lite/application/services/order_service.py"


def test_audit_on_mutation_gate_accepts_direct_audit(tmp_path: Path) -> None:
    _service_file(
        tmp_path,
        """
class OrderService:
    def create_order(self):
        self.order_repository.insert_order(transaction=None)
        self.runtime_service._audit(None, None, event_type="order.created")
""".strip(),
    )

    assert (
        gate.collect_findings(
            services_root=tmp_path / "libs" / "foundry_lite" / "application" / "services",
            project_root=tmp_path,
        )
        == []
    )


def test_audit_on_mutation_gate_accepts_delegated_audit_helper(tmp_path: Path) -> None:
    _service_file(
        tmp_path,
        """
class OrderService:
    def apply_order(self):
        self._insert_order()
        self._record_order_audit()

    def _insert_order(self):
        self.order_repository.insert_order(transaction=None)

    def _record_order_audit(self):
        self.runtime_service._audit(None, None, event_type="order.applied")
""".strip(),
    )

    assert (
        gate.collect_findings(
            services_root=tmp_path / "libs" / "foundry_lite" / "application" / "services",
            project_root=tmp_path,
        )
        == []
    )


def test_audit_on_mutation_gate_writes_json_report(tmp_path: Path) -> None:
    _service_file(
        tmp_path,
        """
class OrderService:
    def create_order(self):
        self.order_repository.insert_order(transaction=None)
""".strip(),
    )
    findings = gate.collect_findings(
        services_root=tmp_path / "libs" / "foundry_lite" / "application" / "services",
        project_root=tmp_path,
    )
    output = tmp_path / "artifacts" / "quality" / "audit_on_mutation.json"

    gate.write_report(output, findings)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["count"] == 1
    assert report["baseline"] == 0
    assert report["gate_pass"] is False
    assert report["violations"][0]["method"] == "create_order"
