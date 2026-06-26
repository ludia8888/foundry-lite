"""Environment-backed SecretProvider adapter for local and dev profiles."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract, AdapterFailureMode
from foundry_lite.application.ports.secret_provider import REDACTED_VALUE, SecretProvider, SecretValue
from foundry_lite.domain.errors import ValidationFailed

DEFAULT_SECRET_ENV_ALIASES: Final[dict[str, str]] = {
    "webhook_signing_key": "FOUNDRY_LITE_WEBHOOK_SIGNING_KEY",
}


@dataclass(frozen=True)
class EnvSecretProvider:
    """Resolve named secrets from environment variables without logging values."""

    env_aliases: Mapping[str, str] = field(default_factory=lambda: dict(DEFAULT_SECRET_ENV_ALIASES))
    environ: Mapping[str, str] | None = None
    profile_name: str = "env-secret"

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "get_secret",
                    "validation",
                    True,
                    "The requested environment-backed secret is missing or blank.",
                ),
            ),
        )

    def get_secret(self, name: str, *, version: str | None = None) -> SecretValue:
        env_var = self._env_var(name)
        source = self._source()
        value = source.get(env_var, "")
        if value != "":
            secret = SecretValue(name=name, version=_secret_version(value), value=value)
            if version is None or secret.version == version:
                return secret
        if version is None:
            raise ValidationFailed(
                "secret is not configured",
                details={"secret_name": name, "env_var": env_var, "secret_value": REDACTED_VALUE},
            )
        versioned_env_var = _versioned_env_var(env_var, version)
        versioned_value = source.get(versioned_env_var, "")
        if versioned_value == "":
            raise ValidationFailed(
                "secret version is not configured",
                details={
                    "secret_name": name,
                    "env_var": versioned_env_var,
                    "secret_version": version,
                    "secret_value": REDACTED_VALUE,
                },
            )
        secret = SecretValue(name=name, version=_secret_version(versioned_value), value=versioned_value)
        if secret.version != version:
            raise ValidationFailed(
                "secret version mismatch",
                details={
                    "secret_name": name,
                    "env_var": versioned_env_var,
                    "expected_version": version,
                    "actual_version": secret.version,
                    "secret_value": REDACTED_VALUE,
                },
            )
        return secret

    def _env_var(self, name: str) -> str:
        return self.env_aliases.get(name, _default_env_var(name))

    def _source(self) -> Mapping[str, str]:
        return os.environ if self.environ is None else self.environ


def secret_provider_from_env(environ: Mapping[str, str] | None = None) -> EnvSecretProvider:
    """Build the local environment-backed SecretProvider."""
    return EnvSecretProvider(environ=environ)


def _default_env_var(name: str) -> str:
    normalized = "".join(char if char.isalnum() else "_" for char in name).upper()
    return f"FOUNDRY_LITE_SECRET_{normalized}"


def _versioned_env_var(env_var: str, version: str) -> str:
    normalized = "".join(char if char.isalnum() else "_" for char in version).upper()
    return f"{env_var}__VERSION_{normalized}"


def _secret_version(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:12]}"


_: SecretProvider = EnvSecretProvider()
