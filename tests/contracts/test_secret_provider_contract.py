"""Contract tests for the SecretProvider port and local environment adapter."""

from __future__ import annotations

import hashlib

import pytest
from foundry_lite.application.ports.secret_provider import SecretProvider, SecretValue
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure.secrets import EnvSecretProvider


def test_env_secret_provider_is_runtime_checkable() -> None:
    assert isinstance(EnvSecretProvider(environ={}), SecretProvider)


def test_env_secret_provider_resolves_named_secret_from_alias() -> None:
    provider = EnvSecretProvider(environ={"FOUNDRY_LITE_WEBHOOK_SIGNING_KEY": "local-secret"})

    secret = provider.get_secret("webhook_signing_key")

    assert secret.name == "webhook_signing_key"
    assert secret.value == "local-secret"
    assert secret.version.startswith("sha256:")


def test_env_secret_provider_resolves_default_secret_name() -> None:
    provider = EnvSecretProvider(environ={"FOUNDRY_LITE_SECRET_CONNECTOR_TOKEN": "connector-secret"})

    secret = provider.get_secret("connector-token")

    assert secret == SecretValue(name="connector-token", version=secret.version, value="connector-secret")


def test_env_secret_provider_resolves_current_secret_by_version() -> None:
    provider = EnvSecretProvider(environ={"FOUNDRY_LITE_SECRET_CONNECTOR_TOKEN": "connector-secret"})
    secret = provider.get_secret("connector-token")

    resolved = provider.get_secret("connector-token", version=secret.version)

    assert resolved == secret


def test_env_secret_provider_resolves_historical_versioned_secret() -> None:
    old_secret = "old-connector-secret"
    old_version = _secret_version(old_secret)
    provider = EnvSecretProvider(
        environ={
            "FOUNDRY_LITE_SECRET_CONNECTOR_TOKEN": "new-connector-secret",
            _versioned_env_var("FOUNDRY_LITE_SECRET_CONNECTOR_TOKEN", old_version): old_secret,
        }
    )

    resolved = provider.get_secret("connector-token", version=old_version)

    assert resolved == SecretValue(name="connector-token", version=old_version, value=old_secret)


def test_secret_never_appears_in_operator_evidence() -> None:
    secret = SecretValue(name="api-key", version="v1", value="super-secret")

    assert "super-secret" not in repr(secret)
    assert secret.redacted() == {"name": "api-key", "version": "v1", "value": "***REDACTED***"}


def test_missing_secret_error_does_not_leak_value() -> None:
    provider = EnvSecretProvider(environ={"FOUNDRY_LITE_SECRET_API_KEY": "present-but-not-requested"})

    with pytest.raises(ValidationFailed) as exc:
        provider.get_secret("missing-api-key")

    assert exc.value.details == {
        "secret_name": "missing-api-key",
        "env_var": "FOUNDRY_LITE_SECRET_MISSING_API_KEY",
        "secret_value": "***REDACTED***",
    }
    assert "present-but-not-requested" not in str(exc.value.details)


def test_versioned_secret_mismatch_does_not_leak_value() -> None:
    provider = EnvSecretProvider(
        environ={
            _versioned_env_var("FOUNDRY_LITE_SECRET_API_KEY", "sha256:expected"): "wrong-version-secret",
        }
    )

    with pytest.raises(ValidationFailed) as exc:
        provider.get_secret("api-key", version="sha256:expected")

    assert exc.value.details["secret_value"] == "***REDACTED***"
    assert "wrong-version-secret" not in str(exc.value.details)


def test_missing_versioned_secret_does_not_leak_current_value() -> None:
    provider = EnvSecretProvider(environ={"FOUNDRY_LITE_SECRET_API_KEY": "current-secret"})

    with pytest.raises(ValidationFailed) as exc:
        provider.get_secret("api-key", version="sha256:missing")

    assert exc.value.details == {
        "secret_name": "api-key",
        "env_var": "FOUNDRY_LITE_SECRET_API_KEY__VERSION_SHA256_MISSING",
        "secret_version": "sha256:missing",
        "secret_value": "***REDACTED***",
    }
    assert "current-secret" not in str(exc.value.details)


def test_secret_provider_failure_contract_declares_missing_secret() -> None:
    contract = EnvSecretProvider(environ={}).failure_contract()

    assert contract.adapter_profile == "env-secret"
    assert contract.modes[0].operation == "get_secret"
    assert contract.modes[0].kind == "validation"


def _secret_version(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:12]}"


def _versioned_env_var(env_var: str, version: str) -> str:
    normalized = "".join(char if char.isalnum() else "_" for char in version).upper()
    return f"{env_var}__VERSION_{normalized}"
