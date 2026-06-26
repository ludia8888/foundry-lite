"""Local encrypted prompt artifact storage adapter."""

from __future__ import annotations

import base64
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract, AdapterFailureMode
from foundry_lite.application.ports.prompt_artifact_store import (
    PromptArtifactBlob,
    PromptArtifactDelete,
    PromptArtifactRead,
    PromptArtifactStoreError,
    PromptArtifactWrite,
)
from foundry_lite.application.ports.secret_provider import SecretProvider

_ALGORITHM = "fernet-v1"
_ARTIFACT_SCHEME = "local-prompt-artifact://"
_DEFAULT_KEY_NAME = "aip_prompt_artifact_encryption_key"
_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9_.=-]+")


@dataclass(frozen=True)
class _ArtifactKey:
    key_ref: str
    fernet_key: bytes


class LocalPromptArtifactStore:
    """Filesystem-backed encrypted prompt artifact store for local/test profiles."""

    profile_name = "local-prompt-artifact-store"

    def __init__(
        self,
        root: Path,
        secret_provider: SecretProvider,
        *,
        key_name: str = _DEFAULT_KEY_NAME,
        allow_local_dev_fallback: bool = False,
    ) -> None:
        self._root = root
        self._secret_provider = secret_provider
        self._key_name = key_name
        self._allow_local_dev_fallback = allow_local_dev_fallback

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "write_prompt_artifact",
                    "unavailable",
                    True,
                    "Prompt artifact storage could not write the encrypted prompt bytes.",
                ),
                AdapterFailureMode(
                    "read_prompt_artifact",
                    "authentication",
                    True,
                    "Prompt artifact encryption key is missing or the requested version is unavailable.",
                ),
                AdapterFailureMode(
                    "read_prompt_artifact",
                    "conflict",
                    False,
                    "Prompt artifact receipt hashes do not match the stored encrypted artifact.",
                ),
                AdapterFailureMode(
                    "read_prompt_artifact",
                    "not_found",
                    False,
                    "Prompt artifact reference does not resolve to a tenant-scoped encrypted artifact.",
                ),
                AdapterFailureMode(
                    "delete_prompt_artifact",
                    "unavailable",
                    True,
                    "Prompt artifact storage could not delete an uncommitted encrypted artifact.",
                ),
            ),
        )

    def write_prompt_artifact(self, request: PromptArtifactWrite) -> PromptArtifactBlob:
        key = self._current_artifact_key()
        ciphertext = Fernet(key.fernet_key).encrypt(request.plaintext.encode("utf-8"))
        artifact_path = self._path_for_parts(request.tenant_id, request.ai_run_id, request.artifact_id)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(artifact_path, ciphertext)
        artifact_ref = self._ref_for_parts(request.tenant_id, request.ai_run_id, request.artifact_id)
        return PromptArtifactBlob(
            artifact_ref=artifact_ref,
            artifact_hash=_hash_bytes(ciphertext),
            byte_size=len(ciphertext),
            encryption_key_ref=key.key_ref,
            encryption_algorithm=_ALGORITHM,
        )

    def read_prompt_artifact(self, request: PromptArtifactRead) -> str:
        key = self._artifact_key_for_ref(request.encryption_key_ref)
        artifact_path = self._path_for_ref(request.artifact_ref, request.tenant_id)
        try:
            ciphertext = artifact_path.read_bytes()
            _require_hash(ciphertext, request.expected_artifact_hash, "artifact")
            plaintext = Fernet(key.fernet_key).decrypt(ciphertext).decode("utf-8")
            _require_hash(plaintext.encode("utf-8"), request.expected_content_hash, "content")
            return plaintext
        except (OSError, InvalidToken, UnicodeDecodeError) as exc:
            raise PromptArtifactStoreError("prompt artifact could not be decrypted") from exc

    def delete_prompt_artifact(self, request: PromptArtifactDelete) -> None:
        artifact_path = self._path_for_ref(request.artifact_ref, request.tenant_id)
        artifact_path.unlink(missing_ok=True)

    def _current_artifact_key(self) -> _ArtifactKey:
        try:
            secret = self._secret_provider.get_secret(self._key_name)
            return _ArtifactKey(
                key_ref=f"secret://{secret.name}@{secret.version}",
                fernet_key=_fernet_key(secret.value),
            )
        except Exception:
            if not self._allow_local_dev_fallback:
                raise
            return _local_dev_key(self._root, self._key_name)

    def _artifact_key_for_ref(self, key_ref: str) -> _ArtifactKey:
        if key_ref.startswith("local-dev://"):
            if not self._allow_local_dev_fallback:
                raise PromptArtifactStoreError("local-dev prompt artifact key fallback is not enabled")
            key = _local_dev_key(self._root, self._key_name)
            if key.key_ref != key_ref:
                raise PromptArtifactStoreError("prompt artifact local-dev key reference does not match this store")
            return key
        name, version = _parse_secret_key_ref(key_ref)
        if name != self._key_name:
            raise PromptArtifactStoreError("prompt artifact encryption key name is not supported by this store")
        try:
            secret = self._secret_provider.get_secret(name, version=version)
        except Exception as exc:
            raise PromptArtifactStoreError("prompt artifact encryption key version is unavailable") from exc
        key = _ArtifactKey(key_ref=f"secret://{secret.name}@{secret.version}", fernet_key=_fernet_key(secret.value))
        if key.key_ref != key_ref:
            raise PromptArtifactStoreError("prompt artifact encryption key version does not match receipt")
        return key

    def _ref_for_parts(self, tenant_id: str, ai_run_id: str, artifact_id: str) -> str:
        segments = [_safe_segment(tenant_id), _safe_segment(ai_run_id), f"{_safe_segment(artifact_id)}.fernet"]
        return f"{_ARTIFACT_SCHEME}{'/'.join(segments)}"

    def _path_for_parts(self, tenant_id: str, ai_run_id: str, artifact_id: str) -> Path:
        return self._root / _safe_segment(tenant_id) / _safe_segment(ai_run_id) / f"{_safe_segment(artifact_id)}.fernet"

    def _path_for_ref(self, artifact_ref: str, tenant_id: str) -> Path:
        if not artifact_ref.startswith(_ARTIFACT_SCHEME):
            raise PromptArtifactStoreError("unsupported prompt artifact ref")
        relative = Path(artifact_ref.removeprefix(_ARTIFACT_SCHEME))
        parts = relative.parts
        if len(parts) != 3 or parts[0] != _safe_segment(tenant_id):
            raise PromptArtifactStoreError("prompt artifact ref is outside the tenant boundary")
        artifact_path = (self._root / relative).resolve()
        if self._root.resolve() not in artifact_path.parents:
            raise PromptArtifactStoreError("prompt artifact ref escapes the storage root")
        return artifact_path


def _local_dev_key(root: Path, secret_name: str) -> _ArtifactKey:
    raw = f"local-dev:{root.resolve()}:{secret_name}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return _ArtifactKey(
        key_ref=f"local-dev://{secret_name}@sha256:{digest[:12]}",
        fernet_key=_fernet_key(raw),
    )


def _parse_secret_key_ref(key_ref: str) -> tuple[str, str]:
    prefix = "secret://"
    if not key_ref.startswith(prefix) or "@" not in key_ref:
        raise PromptArtifactStoreError("unsupported prompt artifact encryption key ref")
    name, version = key_ref.removeprefix(prefix).split("@", 1)
    if not name or not version:
        raise PromptArtifactStoreError("prompt artifact encryption key ref is incomplete")
    return name, version


def _fernet_key(value: str) -> bytes:
    candidate = value.encode("ascii", errors="ignore")
    if _is_fernet_key(candidate):
        return candidate
    return base64.urlsafe_b64encode(hashlib.sha256(value.encode("utf-8")).digest())


def _is_fernet_key(value: bytes) -> bool:
    try:
        return len(base64.urlsafe_b64decode(value)) == 32
    except Exception:
        return False


def _safe_segment(value: str) -> str:
    cleaned = _SAFE_SEGMENT.sub("-", value).strip(".-/")
    if not cleaned:
        raise PromptArtifactStoreError("prompt artifact path segment is empty")
    return cleaned[:120]


def _atomic_write(path: Path, data: bytes) -> None:
    tmp_path = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
    tmp_path.write_bytes(data)
    tmp_path.replace(path)


def _hash_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _require_hash(value: bytes, expected_hash: str, label: str) -> None:
    actual_hash = _hash_bytes(value)
    if actual_hash != expected_hash:
        raise PromptArtifactStoreError(f"prompt artifact {label} hash mismatch")
