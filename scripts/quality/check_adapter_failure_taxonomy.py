"""Verify every concrete adapter exposes a stable failure taxonomy."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
LIBS = ROOT / "libs"
if str(LIBS) not in sys.path:
    sys.path.insert(0, str(LIBS))

from foundry_lite.application.ports import AdapterFailureContract  # noqa: E402
from foundry_lite.infrastructure.adapters import (  # noqa: E402
    DuckDBComputeAdapter,
    FakeComputeAdapter,
    FakeConnectorAdapter,
    FakeDatasetStorageAdapter,
    FakeSearchAdapter,
    FakeStreamAdapter,
    FakeWorkflowAdapter,
    LocalConnectorAdapter,
    LocalDatasetStorageAdapter,
    LocalSearchAdapter,
    LocalStreamAdapter,
    LocalWorkflowAdapter,
    RestPullConnectorAdapter,
)
from foundry_lite.infrastructure.auth import DemoAuthProvider, HeaderTrustAuthProvider  # noqa: E402

DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "adapter_failure_taxonomy.json"
REQUIRED_PROFILES = frozenset(
    {
        "duckdb",
        "fake-compute",
        "local",
        "fake-storage",
        "local-workflow",
        "fake-workflow",
        "local-stream",
        "fake-stream",
        "local-search",
        "fake-search",
        "local-connector",
        "fake-connector",
        "rest-pull-connector",
        "header-trust-auth",
        "demo-auth",
    }
)


@dataclass(frozen=True)
class AdapterTaxonomyFinding:
    code: str
    adapter_profile: str
    message: str


def load_contracts() -> tuple[AdapterFailureContract, ...]:
    with TemporaryDirectory() as tmp:
        storage_root = Path(tmp)
        adapters = (
            DuckDBComputeAdapter(),
            FakeComputeAdapter(),
            LocalDatasetStorageAdapter(storage_root / "local"),
            FakeDatasetStorageAdapter(storage_root / "fake"),
            LocalWorkflowAdapter(),
            FakeWorkflowAdapter(),
            LocalStreamAdapter(),
            FakeStreamAdapter(),
            LocalSearchAdapter(),
            FakeSearchAdapter(),
            LocalConnectorAdapter(),
            FakeConnectorAdapter(),
            RestPullConnectorAdapter(),
            HeaderTrustAuthProvider(),
            DemoAuthProvider(),
        )
        return tuple(adapter.failure_contract() for adapter in adapters)


def collect_findings(contracts: tuple[AdapterFailureContract, ...]) -> list[AdapterTaxonomyFinding]:
    findings: list[AdapterTaxonomyFinding] = []
    findings.extend(_missing_profile_findings(contracts))
    for contract in contracts:
        findings.extend(_contract_findings(contract))
    return findings


def _missing_profile_findings(contracts: tuple[AdapterFailureContract, ...]) -> list[AdapterTaxonomyFinding]:
    profiles = {contract.adapter_profile for contract in contracts}
    missing = sorted(REQUIRED_PROFILES - profiles)
    duplicate = sorted(profile for profile in profiles if sum(c.adapter_profile == profile for c in contracts) > 1)
    findings = [
        AdapterTaxonomyFinding("missing_adapter_failure_contract", profile, "adapter profile is not covered")
        for profile in missing
    ]
    findings.extend(
        AdapterTaxonomyFinding("duplicate_adapter_failure_contract", profile, "adapter profile is duplicated")
        for profile in duplicate
    )
    return findings


def _contract_findings(contract: AdapterFailureContract) -> list[AdapterTaxonomyFinding]:
    findings: list[AdapterTaxonomyFinding] = []
    if not contract.modes:
        findings.append(_finding(contract, "missing_failure_modes", "adapter failure contract has no modes"))
    for mode in contract.modes:
        if not mode.operation.strip():
            findings.append(_finding(contract, "missing_operation", "failure mode operation is empty"))
        if not mode.operator_message.strip():
            findings.append(_finding(contract, "missing_operator_message", "operator message is empty"))
        if mode.timeout_seconds is not None and mode.timeout_seconds < 1:
            findings.append(_finding(contract, "invalid_timeout", "timeout_seconds must be positive"))
        if mode.kind == "timeout" and not mode.is_retryable:
            findings.append(_finding(contract, "timeout_not_retryable", "timeout failures must be retryable"))
    return findings


def _finding(contract: AdapterFailureContract, code: str, message: str) -> AdapterTaxonomyFinding:
    return AdapterTaxonomyFinding(code, contract.adapter_profile, message)


def write_report(
    output: Path,
    contracts: tuple[AdapterFailureContract, ...],
    findings: list[AdapterTaxonomyFinding],
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "gate": "adapter_failure_taxonomy",
        "adapterProfiles": sorted(contract.adapter_profile for contract in contracts),
        "requiredProfiles": sorted(REQUIRED_PROFILES),
        "count": len(findings),
        "violations": [asdict(finding) for finding in findings],
        "contracts": [asdict(contract) for contract in contracts],
        "gate_pass": not findings,
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate concrete adapter failure taxonomy contracts.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    contracts = load_contracts()
    findings = collect_findings(contracts)
    write_report(args.output, contracts, findings)
    if findings:
        print("Adapter failure taxonomy gate failed:")
        for finding in findings:
            print(f"- {finding.adapter_profile}: {finding.code}: {finding.message}")
        print(f"Report: {args.output.relative_to(ROOT)}")
        return 1
    print(f"Adapter failure taxonomy gate passed: {len(contracts)} adapter profiles expose failure contracts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
