"""Enforce guideline §4.3 with a global registry and provider-neutral release contracts.

The gate blocks provider-specific release semantics from leaking into the application
layer and requires an infrastructure registry plus an alternate-provider regression.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "infrastructure_swapability.json"

DEPLOYMENT_PORT = Path("libs/foundry_lite/application/ports/infrastructure_deployment_adapter.py")
SOURCE_PORT = Path("libs/foundry_lite/application/ports/source_control_release.py")
COMPOSITION = Path("libs/foundry_lite/infrastructure/release_dependencies.py")
REGRESSION = Path("tests/unit/test_release_dependencies.py")
MATRIX = Path("docs/infrastructure-swapability-matrix.json")
REQUIRED_FAMILY_IDS = frozenset(
    {
        "metadata-database",
        "dataset-storage",
        "compute",
        "event-stream",
        "workflow",
        "search",
        "media-storage",
        "authentication",
        "secret-provider",
        "release-source-control",
        "release-deployment",
    }
)
PROVIDER_NEUTRAL_MODULES = (
    Path("libs/foundry_lite/application/ports/governed_release_live_attestation_repository.py"),
    Path("libs/foundry_lite/application/services/aip/external_release_delivery_service.py"),
    Path("libs/foundry_lite/application/services/aip/external_release_infrastructure_evidence.py"),
    Path("libs/foundry_lite/application/services/aip/governed_release_live_artifact_sources.py"),
    Path("libs/foundry_lite/application/services/aip/governed_release_live_artifacts.py"),
    Path("libs/foundry_lite/application/services/aip/governed_release_live_collection_contract.py"),
    Path("libs/foundry_lite/application/services/aip/governed_release_live_collection_db_loader.py"),
    Path("libs/foundry_lite/application/services/aip/governed_release_live_evidence.py"),
    Path("libs/foundry_lite/application/services/aip/governed_release_live_provider_collector.py"),
    Path("libs/foundry_lite/application/services/aip/governed_release_live_provider_receipts.py"),
    Path("libs/foundry_lite/application/services/aip/governed_release_live_target_policy.py"),
)

REQUIRED_TERMS = {
    DEPLOYMENT_PORT: (
        "provider_name",
        "is_live_provider",
        "InfrastructureDeploymentReleaseMode",
        "InfrastructureDeploymentTriggerMode",
        "InfrastructureDeploymentSourceBinding",
        "release_mode",
        "trigger_mode",
        "source_binding",
        "workload_kind",
    ),
    SOURCE_PORT: ("provider_name", "is_live_provider"),
    COMPOSITION: (
        "FOUNDRY_LITE_GOVERNED_RELEASE_SOURCE_PROVIDER",
        "source_control_adapter_factories",
        "SourceControlAdapterFactory",
        "adapter.provider_name != provider",
        "FOUNDRY_LITE_GOVERNED_RELEASE_DEPLOYMENT_PROVIDER",
        "deployment_adapter_factories",
        "DeploymentAdapterFactory",
        "adapter.provider_name != provider",
    ),
    REGRESSION: (
        "test_registered_source_provider_swaps_without_application_changes",
        "test_registered_source_provider_identity_mismatch_fails_closed",
        'provider_name = "gitlab"',
        "test_registered_deployment_provider_swaps_without_application_changes",
        "test_registered_deployment_provider_identity_mismatch_fails_closed",
        'provider_name = "kubernetes"',
    ),
}

FORBIDDEN_TERMS = (
    'Literal["github", "render"]',
    '== "github-release"',
    '== "render-infrastructure-deployment"',
    '!= ("github-release", "render-infrastructure-deployment")',
    '"provider": "github"',
    '"provider": "render"',
    "profile_name.split",
    "is_auto_deploy_enabled",
    "source_repository_owner",
    "source_repository_name",
    "source_branch",
    "service_type",
)


@dataclass(frozen=True)
class SwapabilityFinding:
    code: str
    path: str
    term: str
    message: str


def collect_findings(root: Path = ROOT) -> list[SwapabilityFinding]:
    findings: list[SwapabilityFinding] = []
    for relative, terms in REQUIRED_TERMS.items():
        path = root / relative
        if not path.exists():
            findings.append(_finding("missing_file", relative, "", "required swapability file is missing"))
            continue
        text = path.read_text(encoding="utf-8")
        findings.extend(
            _finding("missing_contract", relative, term, "required swapability contract is missing")
            for term in terms
            if term not in text
        )
    for relative in PROVIDER_NEUTRAL_MODULES:
        path = root / relative
        if not path.exists():
            findings.append(_finding("missing_file", relative, "", "provider-neutral module is missing"))
            continue
        text = path.read_text(encoding="utf-8")
        findings.extend(
            _finding(
                "provider_lock_in",
                relative,
                term,
                "provider-specific semantics leaked into a provider-neutral application module",
            )
            for term in FORBIDDEN_TERMS
            if term in text
        )
    findings.extend(_matrix_findings(root))
    return findings


def _matrix_findings(root: Path) -> list[SwapabilityFinding]:
    payload, load_findings = _load_matrix(root)
    if payload is None:
        return load_findings
    families = payload.get("families")
    if not isinstance(families, list):
        return [_finding("invalid_matrix", MATRIX, "families", "swapability matrix families must be a list")]
    return [
        *(finding for item in families for finding in _family_findings(root, item)),
        *_matrix_registry_findings(payload, families),
    ]


def _load_matrix(root: Path) -> tuple[dict[object, object] | None, list[SwapabilityFinding]]:
    path = root / MATRIX
    if not path.exists():
        finding = _finding("missing_file", MATRIX, "", "global infrastructure swapability matrix is missing")
        return None, [finding]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        finding = _finding("invalid_matrix", MATRIX, "", "swapability matrix is not valid JSON")
        return None, [finding]
    if not isinstance(payload, dict):
        finding = _finding("invalid_matrix", MATRIX, "", "swapability matrix root must be an object")
        return None, [finding]
    return payload, []


def _matrix_registry_findings(
    payload: dict[object, object],
    families: list[object],
) -> list[SwapabilityFinding]:
    findings: list[SwapabilityFinding] = []
    requirements = payload.get("statefulCutoverRequirements")
    expected_requirements = {"migration", "reconciliation", "write_fencing", "rollback", "rpo_rto"}
    if not isinstance(requirements, list) or set(requirements) != expected_requirements:
        findings.append(
            _finding("invalid_matrix", MATRIX, "statefulCutoverRequirements", "cutover requirements drifted")
        )
    family_id_values = [item.get("id") for item in families if isinstance(item, dict)]
    family_ids = {value for value in family_id_values if isinstance(value, str)}
    if len(family_id_values) != len(family_ids):
        findings.append(_finding("duplicate_family", MATRIX, "id", "family ids must be unique strings"))
    for missing in sorted(REQUIRED_FAMILY_IDS - family_ids):
        findings.append(_finding("missing_family", MATRIX, missing, "required infrastructure family is missing"))
    return findings


def _family_findings(root: Path, item: object) -> list[SwapabilityFinding]:
    if not isinstance(item, dict):
        return [_finding("invalid_family", MATRIX, repr(item), "family entry must be an object")]
    family_id = item.get("id")
    if not isinstance(family_id, str) or not family_id.strip():
        return [_finding("invalid_family", MATRIX, "id", "family id is required")]
    return [
        *_family_path_findings(root, family_id, item),
        *_family_implementation_findings(root, family_id, item),
        *_family_state_findings(family_id, item),
    ]


def _family_path_findings(root: Path, family_id: str, item: dict[object, object]) -> list[SwapabilityFinding]:
    findings: list[SwapabilityFinding] = []
    for field in ("boundary", "compositionRoot"):
        value = item.get(field)
        if not isinstance(value, str) or not (root / value).is_file():
            findings.append(_finding("invalid_family_path", MATRIX, family_id, f"{field} must name an existing file"))
    composition = item.get("compositionRoot")
    selector = item.get("selector")
    if isinstance(composition, str) and (root / composition).is_file() and isinstance(selector, str):
        if selector not in (root / composition).read_text(encoding="utf-8"):
            findings.append(_finding("missing_selector", MATRIX, family_id, "selector is absent from composition root"))
    return findings


def _family_implementation_findings(
    root: Path,
    family_id: str,
    item: dict[object, object],
) -> list[SwapabilityFinding]:
    implementations = item.get("implementations")
    contracts = item.get("contractTests")
    findings: list[SwapabilityFinding] = []
    if not isinstance(implementations, list) or len(implementations) < 2:
        findings.append(_finding("insufficient_implementations", MATRIX, family_id, "two implementations are required"))
    else:
        findings.extend(_implementation_path_findings(root, family_id, implementations))
    if not isinstance(contracts, list) or not contracts:
        findings.append(_finding("missing_contract_test", MATRIX, family_id, "contract tests are required"))
    else:
        findings.extend(_listed_path_findings(root, family_id, contracts, "missing_contract_test"))
    return findings


def _implementation_path_findings(root: Path, family_id: str, values: list[object]) -> list[SwapabilityFinding]:
    findings: list[SwapabilityFinding] = []
    for value in values:
        path = value.get("path") if isinstance(value, dict) else None
        name = value.get("name") if isinstance(value, dict) else None
        if not isinstance(name, str) or not isinstance(path, str) or not (root / path).is_file():
            findings.append(
                _finding("invalid_implementation", MATRIX, family_id, "implementation name/path is invalid")
            )
    return findings


def _listed_path_findings(
    root: Path,
    family_id: str,
    values: list[object],
    code: str,
) -> list[SwapabilityFinding]:
    return [
        _finding(code, MATRIX, family_id, "listed proof path does not exist")
        for value in values
        if not isinstance(value, str) or not (root / value).is_file()
    ]


def _family_state_findings(family_id: str, item: dict[object, object]) -> list[SwapabilityFinding]:
    is_stateful = item.get("isStateful")
    cutover = item.get("cutoverStatus")
    level = item.get("swapLevel")
    valid_level = level in {"contract", "ratcheted", "cutover-proven"}
    valid_cutover = cutover in {"not-applicable", "not-proven", "proven"}
    checks = (
        (not isinstance(is_stateful, bool), "isStateful must be boolean"),
        (not valid_level, "swapLevel is invalid"),
        (not valid_cutover, "cutoverStatus is invalid"),
        (is_stateful is True and cutover == "not-applicable", "stateful family cannot skip cutover evidence"),
        (is_stateful is False and cutover != "not-applicable", "stateless family must mark cutover not-applicable"),
    )
    return [_finding("invalid_swap_level", MATRIX, family_id, message) for failed, message in checks if failed]


def _finding(code: str, path: Path, term: str, message: str) -> SwapabilityFinding:
    return SwapabilityFinding(code, str(path), term, message)


def write_report(output: Path, findings: list[SwapabilityFinding]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "gate": "infrastructure_swapability",
        "baseline": {"max_provider_lock_in": 0},
        "count": len(findings),
        "violations": [asdict(item) for item in findings],
        "gate_pass": not findings,
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate provider-neutral infrastructure swap contracts.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    output = args.output if args.output.is_absolute() else root / args.output
    findings = collect_findings(root)
    write_report(output, findings)
    if findings:
        print("Infrastructure swapability gate blocked provider lock-in.")
        for item in findings:
            print(f"- {item.path}: {item.message}: {item.term}")
        return 1
    print("Infrastructure swapability gate passed: global registry and provider-neutral release contracts are intact.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
