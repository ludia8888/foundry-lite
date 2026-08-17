"""S3-compatible encrypted prompt artifact storage adapter."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

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
    current_prompt_artifact_key,
    hash_artifact_bytes,
    prompt_artifact_key_for_ref,
    require_artifact_hash,
)
from foundry_lite.infrastructure.adapters.s3_client import build_s3_client, is_not_found_error, join_key

_ARTIFACT_SCHEME = "s3-prompt-artifact://"
_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9_.=-]+")


@dataclass(frozen=True)
class S3PromptArtifactStoreConfig:
    bucket: str
    endpoint_url: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None
    region_name: str = "us-east-1"
    prefix: str = "foundry-lite/prompt-artifacts"
    max_artifact_bytes: int = 4 * 1024 * 1024


class S3PromptArtifactStore:
    """Store encrypted prompt bytes in an S3 bucket outside the ledger DB."""

    profile_name = "s3-prompt-artifact-store"

    def __init__(
        self,
        config: S3PromptArtifactStoreConfig,
        secret_provider: SecretProvider,
        *,
        key_name: str = DEFAULT_PROMPT_ARTIFACT_KEY_NAME,
        client: Any | None = None,
    ) -> None:
        if not config.bucket.strip() or config.max_artifact_bytes <= 0:
            raise ValueError("S3 prompt artifact bucket and positive size limit are required")
        self._config = config
        self._secret_provider = secret_provider
        self._key_name = key_name
        self._prefix = config.prefix.strip("/")
        self._client = client or build_s3_client(
            endpoint_url=config.endpoint_url,
            access_key_id=config.access_key_id,
            secret_access_key=config.secret_access_key,
            region_name=config.region_name,
        )

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "write_prompt_artifact",
                    "unavailable",
                    True,
                    "S3 prompt artifact storage could not write the encrypted prompt bytes.",
                ),
                AdapterFailureMode(
                    "read_prompt_artifact",
                    "authentication",
                    True,
                    "Prompt artifact encryption key is missing or its receipt version is unavailable.",
                ),
                AdapterFailureMode(
                    "read_prompt_artifact",
                    "conflict",
                    False,
                    "Prompt artifact receipt hashes do not match the encrypted S3 object.",
                ),
                AdapterFailureMode(
                    "read_prompt_artifact",
                    "not_found",
                    False,
                    "Prompt artifact reference does not resolve inside the configured tenant prefix.",
                ),
                AdapterFailureMode(
                    "delete_prompt_artifact",
                    "unavailable",
                    True,
                    "S3 prompt artifact storage could not delete an uncommitted encrypted object.",
                ),
            ),
        )

    def write_prompt_artifact(self, request: PromptArtifactWrite) -> PromptArtifactBlob:
        key = current_prompt_artifact_key(self._secret_provider, self._key_name)
        ciphertext = Fernet(key.fernet_key).encrypt(request.plaintext.encode("utf-8"))
        if len(ciphertext) > self._config.max_artifact_bytes:
            raise PromptArtifactStoreError("prompt artifact exceeds the configured encrypted size limit")
        object_key = self._key_for_parts(request.tenant_id, request.ai_run_id, request.artifact_id)
        try:
            self._client.put_object(
                Bucket=self._config.bucket,
                Key=object_key,
                Body=ciphertext,
                ContentType="application/octet-stream",
                IfNoneMatch="*",
            )
        except Exception as exc:
            raise PromptArtifactStoreError("prompt artifact could not be stored") from exc
        return PromptArtifactBlob(
            artifact_ref=self._ref_for_key(object_key),
            artifact_hash=hash_artifact_bytes(ciphertext),
            byte_size=len(ciphertext),
            encryption_key_ref=key.key_ref,
            encryption_algorithm=PROMPT_ARTIFACT_ALGORITHM,
        )

    def read_prompt_artifact(self, request: PromptArtifactRead) -> str:
        key = prompt_artifact_key_for_ref(self._secret_provider, request.encryption_key_ref, self._key_name)
        object_key = self._key_for_ref(request.artifact_ref, request.tenant_id)
        ciphertext = self._get_bounded_object(object_key)
        try:
            require_artifact_hash(ciphertext, request.expected_artifact_hash, "artifact")
            plaintext = Fernet(key.fernet_key).decrypt(ciphertext).decode("utf-8")
            require_artifact_hash(plaintext.encode("utf-8"), request.expected_content_hash, "content")
            return plaintext
        except PromptArtifactStoreError:
            raise
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise PromptArtifactStoreError("prompt artifact could not be decrypted") from exc

    def delete_prompt_artifact(self, request: PromptArtifactDelete) -> None:
        object_key = self._key_for_ref(request.artifact_ref, request.tenant_id)
        try:
            self._client.delete_object(Bucket=self._config.bucket, Key=object_key)
        except Exception as exc:
            raise PromptArtifactStoreError("prompt artifact could not be deleted") from exc

    def _get_bounded_object(self, object_key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self._config.bucket, Key=object_key)
            content_length = response.get("ContentLength")
            if isinstance(content_length, int) and content_length > self._config.max_artifact_bytes:
                raise PromptArtifactStoreError("prompt artifact exceeds the configured encrypted size limit")
            body = response["Body"].read(self._config.max_artifact_bytes + 1)
        except PromptArtifactStoreError:
            raise
        except Exception as exc:
            if is_not_found_error(exc):
                raise PromptArtifactStoreError("prompt artifact was not found") from exc
            raise PromptArtifactStoreError("prompt artifact could not be read") from exc
        if not isinstance(body, bytes) or len(body) > self._config.max_artifact_bytes:
            raise PromptArtifactStoreError("prompt artifact exceeds the configured encrypted size limit")
        return body

    def _key_for_parts(self, tenant_id: str, ai_run_id: str, artifact_id: str) -> str:
        return join_key(
            self._prefix,
            _safe_segment(tenant_id),
            _safe_segment(ai_run_id),
            f"{_safe_segment(artifact_id)}.fernet",
        )

    def _ref_for_key(self, object_key: str) -> str:
        return f"{_ARTIFACT_SCHEME}{self._config.bucket}/{object_key}"

    def _key_for_ref(self, artifact_ref: str, tenant_id: str) -> str:
        expected_prefix = f"{_ARTIFACT_SCHEME}{self._config.bucket}/"
        if not artifact_ref.startswith(expected_prefix):
            raise PromptArtifactStoreError("unsupported prompt artifact ref")
        object_key = artifact_ref.removeprefix(expected_prefix)
        tenant_prefix = join_key(self._prefix, _safe_segment(tenant_id)) + "/"
        if not object_key.startswith(tenant_prefix) or object_key.count("/") != tenant_prefix.count("/") + 1:
            raise PromptArtifactStoreError("prompt artifact ref is outside the tenant boundary")
        return object_key


def _safe_segment(value: str) -> str:
    cleaned = _SAFE_SEGMENT.sub("-", value).strip(".-/")
    if not cleaned:
        raise PromptArtifactStoreError("prompt artifact object key segment is empty")
    return cleaned[:120]
