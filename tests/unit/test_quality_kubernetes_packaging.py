from __future__ import annotations

import json
import shutil
from pathlib import Path

from scripts.quality.check_kubernetes_packaging import REQUIRED_PATHS, ROOT, collect_findings, main


def test_kubernetes_packaging_gate_accepts_current_protected_chart() -> None:
    assert collect_findings(ROOT) == []


def test_kubernetes_packaging_gate_rejects_mutable_dependency_image(tmp_path: Path) -> None:
    _copy_gate_tree(tmp_path)
    values = tmp_path / "deploy/helm/foundry-lite/values.yaml"
    text = values.read_text(encoding="utf-8")
    values.write_text(
        text.replace("docker.io/library/postgres@sha256:", "docker.io/library/postgres:"),
        encoding="utf-8",
    )

    findings = collect_findings(tmp_path)

    assert any(item.code == "mutable_dependency_image" and item.detail == "postgresql" for item in findings)


def test_kubernetes_packaging_gate_rejects_api_secret_permission(tmp_path: Path) -> None:
    _copy_gate_tree(tmp_path)
    rbac = tmp_path / "deploy/helm/foundry-lite/templates/serviceaccounts-rbac.yaml"
    text = rbac.read_text(encoding="utf-8")
    marker = 'resources: ["foundrydeployments"]'
    rbac.write_text(text.replace(marker, 'resources: ["secrets"]', 1), encoding="utf-8")

    findings = collect_findings(tmp_path)

    assert any(item.code == "release_secret_rbac_forbidden" for item in findings)


def test_kubernetes_packaging_gate_rejects_sandbox_network_allow(tmp_path: Path) -> None:
    _copy_gate_tree(tmp_path)
    policy = tmp_path / "deploy/helm/foundry-lite/templates/networkpolicies.yaml"
    text = policy.read_text(encoding="utf-8")
    policy.write_text(text.replace("operator: DoesNotExist", "operator: Exists"), encoding="utf-8")

    findings = collect_findings(tmp_path)

    assert any(item.code == "execution_network_deny_contract_missing" for item in findings)


def test_kubernetes_packaging_gate_writes_machine_report(tmp_path: Path) -> None:
    output = tmp_path / "report.json"

    assert main(["--root", str(ROOT), "--output", str(output)]) == 0
    assert '"gate_pass": true' in output.read_text(encoding="utf-8")


def test_keycloak_realm_references_only_defined_scopes_and_enforces_public_oauth() -> None:
    template = ROOT / "deploy/helm/foundry-lite/templates/qa-dependencies-config.yaml"
    text = template.read_text(encoding="utf-8").split("  foundry-lite-realm.json: |\n", 1)[1]
    realm_text = "\n".join(line[4:] for line in text.splitlines() if line != "{{- end }}")
    realm = json.loads(realm_text)
    scopes = {item["name"] for item in realm["clientScopes"]}
    referenced = set(realm["defaultDefaultClientScopes"] + realm["defaultOptionalClientScopes"])
    executors = realm["clientProfiles"]["profiles"][0]["executors"]
    executor_names = {item["executor"] for item in executors}

    assert referenced <= scopes
    assert {"profile", "email", "foundry-lite-runtime", "osdk", "mcp-audience"} <= scopes
    audience_scope = next(item for item in realm["clientScopes"] if item["name"] == "mcp-audience")
    assert audience_scope["protocolMappers"][0]["protocolMapper"] == "oidc-parameterized-scope-mapper"
    assert audience_scope["protocolMappers"][0]["config"]["claim.name"] == "aud"
    assert {
        "secure-redirect-uris-enforcer",
        "pkce-enforcer",
        "consent-required",
        "full-scope-disabled",
        "reject-implicit-grant",
        "reject-ropc-grant",
    } <= executor_names


def _copy_gate_tree(target: Path) -> None:
    for relative in REQUIRED_PATHS:
        source = ROOT / relative
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
