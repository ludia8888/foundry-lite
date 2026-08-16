"""Local encrypted prompt artifact storage adapter."""

from __future__ import annotations

import hashlib
import os
import re
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
from foundry_lite.infrastructure.adapters.prompt_artifact_crypto import (
    DEFAULT_PROMPT_ARTIFACT_KEY_NAME,
    PROMPT_ARTIFACT_ALGORITHM,
    PromptArtifactEncryptionKey,
    current_prompt_artifact_key,
    fernet_key,
    hash_artifact_bytes,
    prompt_artifact_key_for_ref,
    require_artifact_hash,
)

_ARTIFACT_SCHEME = "local-prompt-artifact://"
_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9_.=-]+")


class LocalPromptArtifactStore:
    """Filesystem-backed encrypted prompt artifact store for local/test profiles."""

    profile_name = "local-prompt-artifact-store"

    def __init__(
        self,
        root: Path,
        secret_provider: SecretProvider,
        *,
        key_name: str = DEFAULT_PROMPT_ARTIFACT_KEY_NAME,
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
            artifact_hash=hash_artifact_bytes(ciphertext),
            byte_size=len(ciphertext),
            encryption_key_ref=key.key_ref,
            encryption_algorithm=PROMPT_ARTIFACT_ALGORITHM,
        )

    def read_prompt_artifact(self, request: PromptArtifactRead) -> str:
        key = self._artifact_key_for_ref(request.encryption_key_ref)
        artifact_path = self._path_for_ref(request.artifact_ref, request.tenant_id)
        try:
            ciphertext = artifact_path.read_bytes()
            require_artifact_hash(ciphertext, request.expected_artifact_hash, "artifact")
            plaintext = Fernet(key.fernet_key).decrypt(ciphertext).decode("utf-8")
            require_artifact_hash(plaintext.encode("utf-8"), request.expected_content_hash, "content")
            return plaintext
        except (OSError, InvalidToken, UnicodeDecodeError) as exc:
            raise PromptArtifactStoreError("prompt artifact could not be decrypted") from exc

    def delete_prompt_artifact(self, request: PromptArtifactDelete) -> None:
        artifact_path = self._path_for_ref(request.artifact_ref, request.tenant_id)
        artifact_path.unlink(missing_ok=True)

    def _current_artifact_key(self) -> PromptArtifactEncryptionKey:
        try:
            return current_prompt_artifact_key(self._secret_provider, self._key_name)
        except Exception:
            if not self._allow_local_dev_fallback:
                raise
            return _local_dev_key(self._root, self._key_name)

    def _artifact_key_for_ref(self, key_ref: str) -> PromptArtifactEncryptionKey:
        if key_ref.startswith("local-dev://"):
            if not self._allow_local_dev_fallback:
                raise PromptArtifactStoreError("local-dev prompt artifact key fallback is not enabled")
            key = _local_dev_key(self._root, self._key_name)
            if key.key_ref != key_ref:
                raise PromptArtifactStoreError("prompt artifact local-dev key reference does not match this store")
            return key
        return prompt_artifact_key_for_ref(self._secret_provider, key_ref, self._key_name)

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


def _local_dev_key(root: Path, secret_name: str) -> PromptArtifactEncryptionKey:
    raw = f"local-dev:{root.resolve()}:{secret_name}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return PromptArtifactEncryptionKey(
        key_ref=f"local-dev://{secret_name}@sha256:{digest[:12]}",
        fernet_key=fernet_key(raw),
    )


def _safe_segment(value: str) -> str:
    cleaned = _SAFE_SEGMENT.sub("-", value).strip(".-/")
    if not cleaned:
        raise PromptArtifactStoreError("prompt artifact path segment is empty")
    return cleaned[:120]


def _atomic_write(path: Path, data: bytes) -> None:
    tmp_path = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
    tmp_path.write_bytes(data)
    tmp_path.replace(path)
