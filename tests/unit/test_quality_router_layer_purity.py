from __future__ import annotations

from pathlib import Path

from scripts.quality import check_router_layer_purity as gate


def _write_api_file(tmp_path: Path, source: str) -> Path:
    api_file = tmp_path / "apps" / "api" / "foundry_lite_api" / "main.py"
    api_file.parent.mkdir(parents=True)
    api_file.write_text(source, encoding="utf-8")
    return api_file


def test_router_layer_purity_flags_core_repository_access(tmp_path: Path) -> None:
    api_file = _write_api_file(
        tmp_path,
        """
def handler(foundry):
    return foundry.dataset_repository.find_active_dataset(tenant_id="t", namespace="raw", name="orders")
""",
    )

    findings = gate.collect_findings((api_file,))

    assert [finding.code for finding in findings] == ["api_core_repository_call"]
    assert findings[0].expression == "foundry.dataset_repository.find_active_dataset"


def test_router_layer_purity_flags_direct_transactions(tmp_path: Path) -> None:
    api_file = _write_api_file(
        tmp_path,
        """
def handler(foundry, statement):
    with foundry.engine.begin() as conn:
        return conn.execute(statement)
""",
    )

    findings = gate.collect_findings((api_file,))

    assert [finding.code for finding in findings] == ["api_engine_begin", "api_transaction_execute"]


def test_router_layer_purity_allows_application_facade_calls(tmp_path: Path) -> None:
    api_file = _write_api_file(
        tmp_path,
        """
def handler(foundry, request):
    return foundry.objects.get("Order", "O-1001", ctx=request.state.ctx)
""",
    )

    assert gate.collect_findings((api_file,)) == []


def test_router_layer_purity_writes_json_report(tmp_path: Path) -> None:
    api_file = _write_api_file(
        tmp_path,
        """
def handler(foundry):
    return foundry.runtime_repository.list_runs(tenant_id="tenant-demo")
""",
    )
    output = tmp_path / "quality" / "router_layer_purity.json"
    findings = gate.collect_findings((api_file,))

    gate.write_report(output, findings)

    report = output.read_text(encoding="utf-8")
    assert '"gate_pass": false' in report
    assert '"api_core_repository_call"' in report
