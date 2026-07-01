from __future__ import annotations

from pathlib import Path

from scripts.quality import check_contract_test_per_port as gate


def _write_port(port_dir: Path, name: str) -> Path:
    port_dir.mkdir(parents=True, exist_ok=True)
    port = port_dir / name
    port.write_text("from typing import Protocol\n\nclass ExamplePort(Protocol):\n    pass\n", encoding="utf-8")
    return port


def _write_contract(contract_dir: Path, name: str) -> Path:
    contract_dir.mkdir(parents=True, exist_ok=True)
    contract = contract_dir / name
    contract.write_text("def test_contract():\n    assert True\n", encoding="utf-8")
    return contract


def test_contract_test_per_port_flags_missing_contract(tmp_path: Path) -> None:
    port_dir = tmp_path / "ports"
    contract_dir = tmp_path / "contracts"
    port = _write_port(port_dir, "search_adapter.py")

    findings = gate.collect_findings(port_dir=port_dir, contract_dir=contract_dir)

    assert findings == [
        gate.ContractPortFinding(
            code="port_missing_contract_test",
            port_path=str(port),
            expected_contracts=["test_search_adapter_contract.py"],
            message="Application port has no matching tests/contracts contract suite",
        )
    ]


def test_contract_test_per_port_accepts_matching_contract(tmp_path: Path) -> None:
    port_dir = tmp_path / "ports"
    contract_dir = tmp_path / "contracts"
    _write_port(port_dir, "workflow_adapter.py")
    _write_contract(contract_dir, "test_workflow_adapter_contract.py")

    assert gate.collect_findings(port_dir=port_dir, contract_dir=contract_dir) == []


def test_contract_test_per_port_accepts_dataset_storage_alias(tmp_path: Path) -> None:
    port_dir = tmp_path / "ports"
    contract_dir = tmp_path / "contracts"
    _write_port(port_dir, "dataset_storage.py")
    _write_contract(contract_dir, "test_dataset_storage_adapter_contract.py")

    assert gate.collect_findings(port_dir=port_dir, contract_dir=contract_dir) == []


def test_contract_test_per_port_accepts_runtime_repository_type_split(tmp_path: Path) -> None:
    port_dir = tmp_path / "ports"
    contract_dir = tmp_path / "contracts"
    _write_port(port_dir, "runtime_repository_types.py")
    _write_contract(contract_dir, "test_runtime_repository_contract.py")

    assert gate.collect_findings(port_dir=port_dir, contract_dir=contract_dir) == []


def test_contract_test_per_port_writes_json_report(tmp_path: Path) -> None:
    port_dir = tmp_path / "ports"
    contract_dir = tmp_path / "contracts"
    output = tmp_path / "quality" / "contract_test_per_port.json"
    _write_port(port_dir, "connector_adapter.py")
    findings = gate.collect_findings(port_dir=port_dir, contract_dir=contract_dir)

    gate.write_report(output, findings, port_dir=port_dir, contract_dir=contract_dir)

    report = output.read_text(encoding="utf-8")
    assert '"gate_pass": false' in report
    assert '"port_missing_contract_test"' in report
