from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml

from scripts.quality.check_kubernetes_packaging import REQUIRED_PATHS, ROOT, _is_digest_image, collect_findings, main


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


def test_digest_image_parser_rejects_oversized_adversarial_repository() -> None:
    value = "!/" * 100_000 + "image@sha256:" + "a" * 64

    assert _is_digest_image(value) is False


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


def test_kubernetes_packaging_gate_rejects_mutable_macmini_tool_manifest(tmp_path: Path) -> None:
    _copy_gate_tree(tmp_path)
    manifest = tmp_path / "deploy/macmini-tools-arm64.json"
    text = manifest.read_text(encoding="utf-8")
    manifest.write_text(text.replace("5bb0e5fe008a773c", "latest", 1), encoding="utf-8")

    findings = collect_findings(tmp_path)

    assert any(item.code == "macmini_tool_manifest_invalid" and item.detail == "uv" for item in findings)


def test_kubernetes_packaging_gate_rejects_escaping_tool_archive_member(tmp_path: Path) -> None:
    _copy_gate_tree(tmp_path)
    manifest = tmp_path / "deploy/macmini-tools-arm64.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["tools"][0]["archiveMember"] = "../uv"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    findings = collect_findings(tmp_path)

    assert any(item.code == "macmini_tool_manifest_invalid" and item.detail == "archive member" for item in findings)


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


def test_macmini_nodeports_expose_web_api_gateway_and_keycloak_separately() -> None:
    values = yaml.safe_load((ROOT / "deploy/helm/foundry-lite/values.macmini-qa.yaml").read_text(encoding="utf-8"))
    web_service = values["web"]["service"]
    keycloak_service = values["qaDependencies"]["keycloak"]["service"]
    web_config = (ROOT / "deploy/helm/foundry-lite/templates/web-config.yaml").read_text(encoding="utf-8")

    assert web_service == {"type": "NodePort", "nodePort": 30443}
    assert keycloak_service == {"type": "NodePort", "nodePort": 30444}
    assert r"location ~ ^/(api|mcp|readyz|\.well-known)" in web_config
    assert 'proxy_pass http://{{ include "foundry-lite.fullname" . }}-api:10000;' in web_config


def test_oauth_bootstrap_deadline_includes_clean_host_image_pull() -> None:
    values = yaml.safe_load((ROOT / "deploy/helm/foundry-lite/values.yaml").read_text(encoding="utf-8"))
    jobs = (ROOT / "deploy/helm/foundry-lite/templates/jobs.yaml").read_text(encoding="utf-8")

    assert values["secrets"]["oauthBootstrapActiveDeadlineSeconds"] >= 900
    assert "activeDeadlineSeconds: {{ .Values.secrets.oauthBootstrapActiveDeadlineSeconds }}" in jobs
    assert "activeDeadlineSeconds: 180" not in jobs


def test_qa_dependencies_keep_read_only_roots_with_explicit_writable_runtime_mounts() -> None:
    templates = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "deploy/helm/foundry-lite/templates/qa-datastores.yaml",
            "deploy/helm/foundry-lite/templates/qa-runtime-services.yaml",
            "deploy/helm/foundry-lite/templates/qa-observability-identity.yaml",
        )
    )

    for copy_target in (
        "cp -R /etc/redpanda/. /writable-config/",
        "cp -R /etc/temporal/config/. /writable-config/",
        "cp -R /usr/share/elasticsearch/config/. /writable-config/",
        "cp -R /etc/clamav/. /writable-config/",
        "cp -R /opt/keycloak/lib/quarkus/. /writable-quarkus/",
    ):
        assert copy_target in templates
    for writable_mount in (
        "mountPath: /etc/redpanda",
        "mountPath: /etc/temporal/config",
        "mountPath: /usr/share/elasticsearch/config",
        "mountPath: /run/clamav",
        "mountPath: /opt/keycloak/lib/quarkus",
    ):
        assert writable_mount in templates
    assert "command: [clamd]" in templates


def _copy_gate_tree(target: Path) -> None:
    for relative in REQUIRED_PATHS:
        source = ROOT / relative
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
