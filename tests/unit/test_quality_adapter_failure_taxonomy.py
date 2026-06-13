from __future__ import annotations

from pathlib import Path

from foundry_lite.application.ports import AdapterFailureContract, AdapterFailureMode

from scripts.quality import check_adapter_failure_taxonomy as gate


def test_adapter_failure_taxonomy_gate_passes_current_adapter_profiles() -> None:
    findings = gate.collect_findings(gate.load_contracts())

    assert findings == []


def test_adapter_failure_taxonomy_gate_flags_missing_required_profile() -> None:
    incomplete = tuple(
        contract for contract in gate.load_contracts() if contract.adapter_profile != "rest-pull-connector"
    )

    findings = gate.collect_findings(incomplete)

    assert findings
    assert findings[0].code == "missing_adapter_failure_contract"
    assert findings[0].adapter_profile == "rest-pull-connector"


def test_adapter_failure_taxonomy_gate_flags_empty_operator_message() -> None:
    contract = AdapterFailureContract(
        adapter_profile="rest-pull-connector",
        modes=(AdapterFailureMode("snapshot", "timeout", True, "", timeout_seconds=5),),
    )

    findings = gate.collect_findings((contract,))

    assert any(finding.code == "missing_operator_message" for finding in findings)


def test_adapter_failure_taxonomy_gate_writes_report(tmp_path: Path) -> None:
    output = tmp_path / "adapter_failure_taxonomy.json"
    contracts = gate.load_contracts()

    gate.write_report(output, contracts, [])

    assert output.read_text(encoding="utf-8")
