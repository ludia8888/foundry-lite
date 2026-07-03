"""Enforces guideline §4.3/§11.4: every application port needs a contract test.

Ports are the swap points for infrastructure. If a new port is added without a
contract test, fake/local/PostgreSQL implementations can drift silently. This
gate maps `libs/foundry_lite/application/ports/*.py` to
`tests/contracts/test_*_contract.py` and requires zero missing contracts.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PORT_DIR = ROOT / "libs" / "foundry_lite" / "application" / "ports"
DEFAULT_CONTRACT_DIR = ROOT / "tests" / "contracts"
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "contract_test_per_port.json"
PORT_CONTRACT_ALIASES = {
    "dataset_storage": ("dataset_storage_adapter",),
    "ontology_definitions": ("ontology_repository",),
    "runtime_repository_types": ("runtime_repository",),
}


@dataclass(frozen=True)
class ContractPortFinding:
    code: str
    port_path: str
    expected_contracts: list[str]
    message: str


def _repo_relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _port_files(port_dir: Path) -> list[Path]:
    return sorted(path for path in port_dir.glob("*.py") if path.name != "__init__.py")


def _contract_files(contract_dir: Path) -> set[str]:
    return {path.name for path in contract_dir.glob("test_*_contract.py") if path.is_file()}


def _expected_contract_names(port_path: Path) -> list[str]:
    stem = port_path.stem
    stems = (stem, *PORT_CONTRACT_ALIASES.get(stem, ()))
    return [f"test_{contract_stem}_contract.py" for contract_stem in stems]


def _missing_contract_finding(port_path: Path, existing_contracts: set[str]) -> ContractPortFinding | None:
    expected_names = _expected_contract_names(port_path)
    if any(name in existing_contracts for name in expected_names):
        return None
    return ContractPortFinding(
        code="port_missing_contract_test",
        port_path=_repo_relative(port_path),
        expected_contracts=expected_names,
        message="Application port has no matching tests/contracts contract suite",
    )


def collect_findings(
    *,
    port_dir: Path = DEFAULT_PORT_DIR,
    contract_dir: Path = DEFAULT_CONTRACT_DIR,
) -> list[ContractPortFinding]:
    existing_contracts = _contract_files(contract_dir)
    findings: list[ContractPortFinding] = []
    for port_path in _port_files(port_dir):
        finding = _missing_contract_finding(port_path, existing_contracts)
        if finding is not None:
            findings.append(finding)
    return findings


def write_report(
    output: Path,
    findings: list[ContractPortFinding],
    *,
    port_dir: Path = DEFAULT_PORT_DIR,
    contract_dir: Path = DEFAULT_CONTRACT_DIR,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    port_count = len(_port_files(port_dir))
    contract_count = len(_contract_files(contract_dir))
    report = {
        "gate": "contract_test_per_port",
        "baseline": {"max_missing_contracts": 0},
        "count": len(findings),
        "port_count": port_count,
        "contract_count": contract_count,
        "violations": [asdict(finding) for finding in findings],
        "gate_pass": not findings,
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(findings: list[ContractPortFinding], output: Path) -> None:
    print("Release gate blocked: every application port must have a contract test.")
    for finding in findings:
        expected = ", ".join(finding.expected_contracts)
        print(f"- {finding.port_path}: expected one of {expected}")
    print(f"Report: {_repo_relative(output)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate 1:1 contract tests for application ports.")
    parser.add_argument("--port-dir", type=Path, default=DEFAULT_PORT_DIR)
    parser.add_argument("--contract-dir", type=Path, default=DEFAULT_CONTRACT_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    port_dir = args.port_dir if args.port_dir.is_absolute() else ROOT / args.port_dir
    contract_dir = args.contract_dir if args.contract_dir.is_absolute() else ROOT / args.contract_dir
    findings = collect_findings(port_dir=port_dir, contract_dir=contract_dir)
    write_report(args.output, findings, port_dir=port_dir, contract_dir=contract_dir)
    if findings:
        _print_failure(findings, args.output)
        return 1
    print("Contract-test-per-port gate passed: every application port has a contract suite.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
