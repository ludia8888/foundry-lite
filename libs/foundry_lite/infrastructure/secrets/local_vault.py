"""Local encrypted SecretVault adapter for source onboarding."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, cast

from cryptography.fernet import Fernet, InvalidToken

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract, AdapterFailureMode
from foundry_lite.application.ports.secret_provider import REDACTED_VALUE, SecretValue, SecretVault
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure.secrets.env import EnvSecretProvider


class LocalSecretVaultProvider:
    """File-backed encrypted vault with env-secret fallback for local/dev profiles."""

    profile_name = "local-secret-vault"

    def __init__(self, root: Path, *, fallback: EnvSecretProvider | None = None) -> None:
        self._root = root
        self._fallback = fallback or EnvSecretProvider()
        self._vault_path = root / "secrets-vault.json"
        self._fernet = Fernet(_local_vault_key(root))

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode("get_secret", "validation", True, "The requested secret is missing or corrupt."),
                AdapterFailureMode("put_secret", "validation", False, "The requested secret name or value is invalid."),
            ),
        )

    def get_secret(self, name: str, *, version: str | None = None) -> SecretValue:
        payload = self._payload()
        record = _secret_record(payload, name, version)
        if record is None:
            return self._fallback.get_secret(name, version=version)
        try:
            value = self._fernet.decrypt(str(record["ciphertext"]).encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise ValidationFailed("secret vault entry cannot be decrypted", details=_redacted_details(name)) from exc
        return SecretValue(name=name, version=str(record["version"]), value=value)

    def put_secret(
        self,
        name: str,
        value: str,
        *,
        metadata: Mapping[str, object] | None = None,
    ) -> SecretValue:
        _require_secret_name(name)
        if value == "":
            raise ValidationFailed("secret value cannot be blank", details=_redacted_details(name))
        secret = SecretValue(name=name, version=_secret_version(value), value=value)
        payload = self._payload()
        secrets = cast(dict[str, object], payload.setdefault("secrets", {}))
        versions = cast(
            dict[str, object], cast(dict[str, object], secrets.setdefault(name, {})).setdefault("versions", {})
        )
        versions[secret.version] = _version_record(self._fernet, secret, metadata)
        cast(dict[str, object], secrets[name])["currentVersion"] = secret.version
        self._write_payload(payload)
        return secret

    def _payload(self) -> dict[str, object]:
        if not self._vault_path.exists():
            return {"version": 1, "secrets": {}}
        try:
            return cast(dict[str, object], json.loads(self._vault_path.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            raise ValidationFailed(
                "secret vault metadata is not valid JSON", details={"path": str(self._vault_path)}
            ) from exc

    def _write_payload(self, payload: Mapping[str, object]) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        self._vault_path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
        self._vault_path.chmod(0o600)


def local_secret_vault_provider(root: Path, fallback: EnvSecretProvider | None = None) -> LocalSecretVaultProvider:
    """Build the local encrypted SecretVault adapter."""
    return LocalSecretVaultProvider(root, fallback=fallback)


def _local_vault_key(root: Path) -> bytes:
    key_path = root / "secrets-vault.key"
    if key_path.exists():
        return key_path.read_bytes().strip()
    key = Fernet.generate_key()
    root.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key)
    key_path.chmod(0o600)
    return key


def _secret_record(payload: Mapping[str, object], name: str, version: str | None) -> Mapping[str, object] | None:
    secrets = payload.get("secrets")
    if not isinstance(secrets, Mapping):
        return None
    entry = secrets.get(name)
    if not isinstance(entry, Mapping):
        return None
    resolved_version = version or _text(entry.get("currentVersion"))
    versions = entry.get("versions")
    if not resolved_version or not isinstance(versions, Mapping):
        return None
    record = versions.get(resolved_version)
    return record if isinstance(record, Mapping) else None


def _version_record(fernet: Fernet, secret: SecretValue, metadata: Mapping[str, object] | None) -> dict[str, object]:
    return {
        "version": secret.version,
        "ciphertext": fernet.encrypt(secret.value.encode("utf-8")).decode("utf-8"),
        "metadata": _safe_metadata(metadata),
    }


def _safe_metadata(metadata: Mapping[str, object] | None) -> dict[str, object]:
    safe = dict(metadata or {})
    for key in list(safe):
        if "secret" in key.lower() or "token" in key.lower() or "password" in key.lower():
            safe[key] = REDACTED_VALUE
    return safe


def _require_secret_name(name: str) -> None:
    if not name or any(char.isspace() for char in name):
        raise ValidationFailed("secret name must be non-blank and space-free", details={"secret_name": name})


def _redacted_details(name: str) -> dict[str, object]:
    return {"secret_name": name, "secret_value": REDACTED_VALUE}


def _secret_version(value: str) -> str:
    import hashlib

    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:12]}"


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


if TYPE_CHECKING:
    _: SecretVault = LocalSecretVaultProvider(Path("."))
