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


def test_oauth_signing_secret_bootstrap_runs_only_on_first_install() -> None:
    jobs = (ROOT / "deploy/helm/foundry-lite/templates/jobs.yaml").read_text(encoding="utf-8")
    rbac = (ROOT / "deploy/helm/foundry-lite/templates/serviceaccounts-rbac.yaml").read_text(encoding="utf-8")
    oauth_job = jobs.split("{{- if and .Values.qaDependencies.enabled .Values.migrations.enabled }}", 1)[0]
    oauth_rbac = rbac.split("{{- if .Values.secrets.bootstrapOauthSigningSecret }}", 1)[1].split("{{- end }}", 1)[0]

    assert oauth_job.count("helm.sh/hook: pre-install") == 1
    assert "pre-upgrade" not in oauth_job
    assert oauth_rbac.count("helm.sh/hook: pre-install") == 3
    assert "pre-upgrade" not in oauth_rbac


def test_api_and_workers_can_read_oauth_key_as_the_image_nonroot_principal() -> None:
    for template_name in ("api.yaml", "workers.yaml"):
        template = (ROOT / "deploy/helm/foundry-lite/templates" / template_name).read_text(encoding="utf-8")

        assert "runAsUser: 10001" in template
        assert "runAsGroup: 10001" in template
        assert "fsGroup: 10001" in template
        assert "fsGroupChangePolicy: OnRootMismatch" in template
        assert "defaultMode: 0440" in template


def test_otlp_http_trace_endpoint_includes_the_collector_path() -> None:
    base = yaml.safe_load((ROOT / "deploy/helm/foundry-lite/values.yaml").read_text(encoding="utf-8"))
    macmini = yaml.safe_load((ROOT / "deploy/helm/foundry-lite/values.macmini-qa.yaml").read_text(encoding="utf-8"))

    assert base["external"]["telemetry"]["otlpEndpoint"].endswith("/v1/traces")
    assert macmini["external"]["telemetry"]["otlpEndpoint"] == ("http://foundry-lite-tempo:4318/v1/traces")


def test_tempo_qa_profile_bounds_ingester_memory_and_declares_its_resources() -> None:
    values = yaml.safe_load((ROOT / "deploy/helm/foundry-lite/values.yaml").read_text(encoding="utf-8"))
    identity = (ROOT / "deploy/helm/foundry-lite/templates/qa-observability-identity.yaml").read_text(encoding="utf-8")
    config = (ROOT / "deploy/helm/foundry-lite/templates/qa-dependencies-config.yaml").read_text(encoding="utf-8")

    tempo = values["qaDependencies"]["tempo"]
    assert tempo["resources"]["limits"]["memory"] == "2Gi"
    assert tempo["gcMemoryLimit"] == "1536MiB"
    assert tempo["ingester"] == {
        "maxBlockBytes": 33554432,
        "maxBlockDuration": "5m",
        "completeBlockTimeout": "5m",
    }
    assert "toYaml .Values.qaDependencies.tempo.resources" in identity
    assert "max_block_bytes: {{ .Values.qaDependencies.tempo.ingester.maxBlockBytes }}" in config
    assert "max_block_duration: {{ .Values.qaDependencies.tempo.ingester.maxBlockDuration }}" in config
    assert "complete_block_timeout: {{ .Values.qaDependencies.tempo.ingester.completeBlockTimeout }}" in config
    assert "name: GOMEMLIMIT" in identity
    assert ".Values.qaDependencies.tempo.gcMemoryLimit | quote" in identity
    assert tempo["limits"] == {
        "rateLimitBytes": 4194304,
        "burstSizeBytes": 8388608,
        "maxTracesPerUser": 2000,
        "maxBytesPerTrace": 5242880,
    }
    assert "rate_limit_bytes: {{ .Values.qaDependencies.tempo.limits.rateLimitBytes }}" in config
    assert "max_bytes_per_trace: {{ .Values.qaDependencies.tempo.limits.maxBytesPerTrace }}" in config
    assert "max_attribute_bytes" not in config


def test_kafka_outbox_subscription_is_packaged_for_runtime_and_macmini() -> None:
    base = yaml.safe_load((ROOT / "deploy/helm/foundry-lite/values.yaml").read_text(encoding="utf-8"))
    macmini = yaml.safe_load((ROOT / "deploy/helm/foundry-lite/values.macmini-qa.yaml").read_text(encoding="utf-8"))
    configmap = (ROOT / "deploy/helm/foundry-lite/templates/configmap.yaml").read_text(encoding="utf-8")

    base_subscriptions = json.loads(base["external"]["kafka"]["subscriptionsJson"])
    macmini_subscriptions = json.loads(macmini["external"]["kafka"]["subscriptionsJson"])

    assert base_subscriptions == [{"streamName": "foundry-lite-outbox", "topic": "foundry-lite-outbox", "partition": 0}]
    assert macmini_subscriptions[0]["defaultTenantId"] == "tenant-demo"
    assert "FOUNDRY_LITE_KAFKA_SUBSCRIPTIONS_JSON" in configmap
    assert ".Values.external.kafka.subscriptionsJson" in configmap


def test_runtime_and_migration_database_principals_are_separate() -> None:
    values = yaml.safe_load((ROOT / "deploy/helm/foundry-lite/values.yaml").read_text(encoding="utf-8"))
    jobs = (ROOT / "deploy/helm/foundry-lite/templates/jobs.yaml").read_text(encoding="utf-8")
    helpers = (ROOT / "deploy/helm/foundry-lite/templates/_helpers.tpl").read_text(encoding="utf-8")

    assert values["secrets"]["applicationExistingSecret"] != values["secrets"]["migrationExistingSecret"]
    assert values["secrets"]["runtimeApplicationExistingSecret"] == "foundry-lite-runtime-application"
    assert values["qaDependencies"]["postgresql"]["applicationRole"] == "foundry_lite_app"
    assert "bootstrap_postgres_application_role.py" in jobs
    assert jobs.count(".Values.secrets.migrationExistingSecret") == 2
    assert "key: POSTGRES_APP_PASSWORD" in jobs
    assert "distinct application and migration database secrets" in helpers


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
        "mountPath: /usr/share/elasticsearch/logs",
        "mountPath: /var/log/clamav",
        "mountPath: /run/clamav",
        "mountPath: /opt/keycloak/lib/quarkus",
    ):
        assert writable_mount in templates
    assert "command: [clamd]" in templates


def test_postgresql_probes_use_the_digest_image_binary_path() -> None:
    datastores = (ROOT / "deploy/helm/foundry-lite/templates/qa-datastores.yaml").read_text(encoding="utf-8")

    assert datastores.count("/usr/bin/pg_isready") == 2
    assert "/usr/local/bin/pg_isready" not in datastores


def test_keycloak_waits_for_postgresql_before_starting() -> None:
    identity = (ROOT / "deploy/helm/foundry-lite/templates/qa-observability-identity.yaml").read_text(encoding="utf-8")

    keycloak = identity.split("app.kubernetes.io/component: keycloak", 1)[1]
    assert "name: wait-for-postgresql" in keycloak
    assert 'args: [--host, {{ include "foundry-lite.fullname" . }}-postgresql, --port, "5432"' in keycloak


def test_runtime_pvc_can_be_deferred_until_a_consumer_exists() -> None:
    values = yaml.safe_load((ROOT / "deploy/helm/foundry-lite/values.yaml").read_text(encoding="utf-8"))
    runtime_pvc = (ROOT / "deploy/helm/foundry-lite/templates/runtime-pvc.yaml").read_text(encoding="utf-8")

    assert values["runtimePersistence"]["enabled"] is True
    assert runtime_pvc.startswith("{{- if .Values.runtimePersistence.enabled }}")


def _copy_gate_tree(target: Path) -> None:
    for relative in REQUIRED_PATHS:
        source = ROOT / relative
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
