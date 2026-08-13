from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from foundry_lite.application.runtime_profile import RuntimeProfile
from foundry_lite.infrastructure.protected_runtime_host import (
    DURABLE_STATE_MOUNT_ENV,
    require_protected_runtime_host,
)
from foundry_lite_api import main
from foundry_lite_api import runtime as api_runtime

ROOT = Path(__file__).resolve().parents[2]
BLUEPRINT = ROOT / "deploy" / "render" / "render.staging-bootstrap.yaml"
DOCKERFILE = ROOT / "deploy" / "render" / "Dockerfile.api"


def test_protected_runtime_host_requires_postgres_and_active_durable_mount(tmp_path: Path) -> None:
    mount = tmp_path / "durable"
    mount.mkdir()
    home = mount / "foundry-lite"

    host = require_protected_runtime_host(
        profile=RuntimeProfile.from_value("staging"),
        database_url="postgresql+psycopg://user:secret@db.internal/foundry_lite",
        runtime_home=home,
        environ={DURABLE_STATE_MOUNT_ENV: str(mount)},
        mount_checker=lambda candidate: candidate == mount.resolve(),
    )

    assert host.database_backend == "postgresql"
    assert host.runtime_home == home.resolve()


@pytest.mark.parametrize(
    ("database_url", "runtime_home", "mount_value", "is_mounted", "message"),
    [
        ("sqlite:///local.db", "/durable/home", "/durable", True, "PostgreSQL"),
        (None, "/durable/home", "/durable", True, "DB_URL"),
        ("postgresql://user@db/name", "relative", "/durable", True, "must be absolute"),
        ("postgresql://user@db/name", "/tmp/home", "/durable", True, "must be located under"),
        ("postgresql://user@db/name", "/durable/home", "/durable", False, "active filesystem mount"),
    ],
)
def test_protected_runtime_host_fails_closed_on_ephemeral_or_local_configuration(
    database_url: str | None,
    runtime_home: str,
    mount_value: str,
    is_mounted: bool,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        require_protected_runtime_host(
            profile=RuntimeProfile.from_value("production"),
            database_url=database_url,
            runtime_home=runtime_home,
            environ={DURABLE_STATE_MOUNT_ENV: mount_value},
            mount_checker=lambda _candidate: is_mounted,
        )


def test_readyz_reports_real_composition_summary_without_configuration_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        api_runtime,
        "probe_api_readiness",
        lambda: api_runtime.ApiReadiness(runtime_profile="staging", database_backend="postgresql"),
    )

    response = main.readyz()

    assert response.status_code == 200
    assert json.loads(bytes(response.body)) == {
        "status": "ready",
        "runtimeProfile": "staging",
        "databaseBackend": "postgresql",
    }


def test_readyz_returns_safe_503_when_composition_or_database_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_probe() -> api_runtime.ApiReadiness:
        raise RuntimeError("postgresql://user:raw-password@db.internal/foundry")

    monkeypatch.setattr(api_runtime, "probe_api_readiness", fail_probe)

    response = main.readyz()

    assert response.status_code == 503
    assert json.loads(bytes(response.body)) == {"status": "not_ready"}
    assert b"raw-password" not in bytes(response.body)


def test_render_blueprint_is_manual_protected_staging_bootstrap() -> None:
    blueprint = yaml.safe_load(BLUEPRINT.read_text(encoding="utf-8"))
    service = blueprint["services"][0]
    env = {item["key"]: item for item in service["envVars"]}

    assert service["runtime"] == "docker"
    assert service["autoDeployTrigger"] == "off"
    assert service["healthCheckPath"] == "/readyz"
    assert service["preDeployCommand"] == "/app/deploy/render/predeploy_migrate.sh"
    assert service["disk"]["mountPath"] == "/var/data"
    assert service["numInstances"] == 1
    assert env["FOUNDRY_LITE_RUNTIME_PROFILE"]["value"] == "staging"
    assert env["FOUNDRY_LITE_DB_URL"]["fromDatabase"]["property"] == "connectionString"
    assert env["FOUNDRY_LITE_HOME"]["value"].startswith(service["disk"]["mountPath"] + "/")
    assert env["FOUNDRY_LITE_RENDER_RELEASE_SERVICE_ID"]["fromService"]["envVarKey"] == "RENDER_SERVICE_ID"
    assert env["FOUNDRY_LITE_MCP_PUBLIC_BASE_URL"]["fromService"]["envVarKey"] == "RENDER_EXTERNAL_URL"
    assert env["FOUNDRY_LITE_OIDC_GRANT_TYPE_CLAIM"] == {
        "key": "FOUNDRY_LITE_OIDC_GRANT_TYPE_CLAIM",
        "sync": False,
    }
    assert env["FOUNDRY_LITE_OIDC_GRANT_TYPE_VALUE"] == {
        "key": "FOUNDRY_LITE_OIDC_GRANT_TYPE_VALUE",
        "sync": False,
    }
    assert blueprint["databases"][0]["plan"] != "free"


def test_render_blueprint_has_no_committed_provider_secret_defaults() -> None:
    blueprint = yaml.safe_load(BLUEPRINT.read_text(encoding="utf-8"))
    env = {item["key"]: item for item in blueprint["services"][0]["envVars"]}
    protected_inputs = {
        "FOUNDRY_LITE_OIDC_JWKS_JSON",
        "FOUNDRY_LITE_GOVERNED_RELEASE_APPLICATION_ID",
        "FOUNDRY_LITE_S3_ACCESS_KEY_ID",
        "FOUNDRY_LITE_S3_SECRET_ACCESS_KEY",
        "FOUNDRY_LITE_SECRET_GITHUB_RELEASE_TOKEN",
        "FOUNDRY_LITE_SECRET_RENDER_RELEASE_TOKEN",
        "FOUNDRY_LITE_SECRET_ANTHROPIC_API_KEY",
        "FOUNDRY_LITE_SECRET_AIP_PROMPT_ARTIFACT_ENCRYPTION_KEY",
        "FOUNDRY_LITE_SECRET_AIP_CITATION_NAVIGATION_SIGNER",
        "FOUNDRY_LITE_CODE_EXECUTION_IMAGE",
        "FOUNDRY_LITE_NODE_CODE_EXECUTION_IMAGE",
        "FOUNDRY_LITE_TRAINED_MODEL_IMAGE",
    }

    assert protected_inputs <= env.keys()
    assert all(env[key] == {"key": key, "sync": False} for key in protected_inputs)


def test_render_blueprint_keeps_live_release_evidence_on_the_durable_mount() -> None:
    blueprint = yaml.safe_load(BLUEPRINT.read_text(encoding="utf-8"))
    service = blueprint["services"][0]
    env = {item["key"]: item for item in service["envVars"]}
    evidence_keys = {
        "FOUNDRY_LITE_GOVERNED_RELEASE_GOLDEN_MANIFEST_PATH",
        "FOUNDRY_LITE_GOVERNED_RELEASE_LIVE_PREFLIGHT_PATH",
        "FOUNDRY_LITE_GOVERNED_RELEASE_GOLDEN_EVIDENCE_PATH",
        "FOUNDRY_LITE_GOVERNED_RELEASE_GOLDEN_VERIFICATION_PATH",
    }
    runtime_home = env["FOUNDRY_LITE_HOME"]["value"]

    assert all(env[key]["value"].startswith(runtime_home + "/operator-evidence/") for key in evidence_keys)


def test_api_container_is_non_root_and_binds_render_port_through_a_fixed_entrypoint() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    start_script = (ROOT / "deploy" / "render" / "start_api.sh").read_text(encoding="utf-8")
    migration_script = (ROOT / "deploy" / "render" / "predeploy_migrate.sh").read_text(encoding="utf-8")
    normalization_script = (ROOT / "deploy" / "render" / "normalize_render_environment.sh").read_text(encoding="utf-8")

    assert "USER 10001:10001" in dockerfile
    assert "--frozen" in dockerfile
    assert "--all-extras" in dockerfile
    assert "--group deployment" in dockerfile
    assert "--host 0.0.0.0" in start_script
    assert '--port "${PORT:-10000}"' in start_script
    assert "run_migrations.py" in migration_script
    assert "--revision head" in migration_script
    assert "postgresql+psycopg://" in normalization_script
    assert "normalize_render_environment.sh" in start_script
    assert "normalize_render_environment.sh" in migration_script
