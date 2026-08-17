"""Encryption and receipt helpers shared by prompt artifact stores."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass

from foundry_lite.application.ports.prompt_artifact_store import PromptArtifactStoreError
from foundry_lite.application.ports.secret_provider import SecretProvider

PROMPT_ARTIFACT_ALGORITHM = "fernet-v1"
DEFAULT_PROMPT_ARTIFACT_KEY_NAME = "aip_prompt_artifact_encryption_key"


@dataclass(frozen=True)
class PromptArtifactEncryptionKey:
    key_ref: str
    fernet_key: bytes


def current_prompt_artifact_key(
    secret_provider: SecretProvider,
    key_name: str = DEFAULT_PROMPT_ARTIFACT_KEY_NAME,
) -> PromptArtifactEncryptionKey:
    secret = secret_provider.get_secret(key_name)
    return PromptArtifactEncryptionKey(
        key_ref=f"secret://{secret.name}@{secret.version}",
        fernet_key=fernet_key(secret.value),
    )


def prompt_artifact_key_for_ref(
    secret_provider: SecretProvider,
    key_ref: str,
    key_name: str = DEFAULT_PROMPT_ARTIFACT_KEY_NAME,
) -> PromptArtifactEncryptionKey:
    name, version = parse_secret_key_ref(key_ref)
    if name != key_name:
        raise PromptArtifactStoreError("prompt artifact encryption key name is not supported by this store")
    try:
        secret = secret_provider.get_secret(name, version=version)
    except Exception as exc:
        raise PromptArtifactStoreError("prompt artifact encryption key version is unavailable") from exc
    key = PromptArtifactEncryptionKey(
        key_ref=f"secret://{secret.name}@{secret.version}",
        fernet_key=fernet_key(secret.value),
    )
    if key.key_ref != key_ref:
        raise PromptArtifactStoreError("prompt artifact encryption key version does not match receipt")
    return key


def parse_secret_key_ref(key_ref: str) -> tuple[str, str]:
    prefix = "secret://"
    if not key_ref.startswith(prefix) or "@" not in key_ref:
        raise PromptArtifactStoreError("unsupported prompt artifact encryption key ref")
    name, version = key_ref.removeprefix(prefix).split("@", 1)
    if not name or not version:
        raise PromptArtifactStoreError("prompt artifact encryption key ref is incomplete")
    return name, version


def fernet_key(value: str) -> bytes:
    candidate = value.encode("ascii", errors="ignore")
    if _is_fernet_key(candidate):
        return candidate
    return base64.urlsafe_b64encode(hashlib.sha256(value.encode("utf-8")).digest())


def hash_artifact_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def require_artifact_hash(value: bytes, expected_hash: str, label: str) -> None:
    actual_hash = hash_artifact_bytes(value)
    if actual_hash != expected_hash:
        raise PromptArtifactStoreError(f"prompt artifact {label} hash mismatch")


def _is_fernet_key(value: bytes) -> bool:
    try:
        return len(base64.urlsafe_b64decode(value)) == 32
    except Exception:
        return False
