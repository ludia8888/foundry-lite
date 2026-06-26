"""Contract for encrypted prompt artifact storage."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.application.ports.prompt_artifact_store import (
    PromptArtifactDelete,
    PromptArtifactRead,
    PromptArtifactStoreError,
    PromptArtifactWrite,
)
from foundry_lite.application.ports.secret_provider import SecretValue
from foundry_lite.infrastructure.adapters.local_prompt_artifact_store import LocalPromptArtifactStore


@dataclass(frozen=True)
class _StaticSecretProvider:
    secret: SecretValue
    profile_name: str = "static-secret"

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=())

    def get_secret(self, name: str, *, version: str | None = None) -> SecretValue:
        assert name == self.secret.name
        if version is not None and version != self.secret.version:
            raise RuntimeError(f"missing secret version: {version}")
        return self.secret


@dataclass(frozen=True)
class _VersionedSecretProvider:
    secrets_by_version: Mapping[str, SecretValue]
    current_version: str
    profile_name: str = "versioned-secret"

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=())

    def get_secret(self, name: str, *, version: str | None = None) -> SecretValue:
        target_version = self.current_version if version is None else version
        secret = self.secrets_by_version[target_version]
        assert name == secret.name
        return secret


@dataclass(frozen=True)
class _MismatchedVersionSecretProvider:
    secret: SecretValue
    profile_name: str = "mismatched-version-secret"

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=())

    def get_secret(self, name: str, *, version: str | None = None) -> SecretValue:
        assert name == self.secret.name
        return self.secret


@dataclass(frozen=True)
class _MissingSecretProvider:
    profile_name: str = "missing-secret"

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=())

    def get_secret(self, name: str, *, version: str | None = None) -> SecretValue:
        raise RuntimeError(f"missing secret: {name}")


def test_local_prompt_artifact_store_encrypts_raw_prompt_and_returns_only_refs(tmp_path: Path) -> None:
    raw_prompt = "system prompt with supplier margin 42 and operator instruction"
    secret_value = Fernet.generate_key().decode("ascii")
    store = LocalPromptArtifactStore(
        tmp_path,
        _StaticSecretProvider(
            SecretValue(name="aip_prompt_artifact_encryption_key", version="sha256:test-key", value=secret_value)
        ),
        allow_local_dev_fallback=False,
    )

    blob = store.write_prompt_artifact(
        PromptArtifactWrite(
            tenant_id="tenant-demo",
            ai_run_id="ai-run-1",
            artifact_id="ai-run-1-compiled-prompt",
            plaintext=raw_prompt,
        )
    )
    encrypted_file = next(tmp_path.rglob("*.fernet"))
    decrypted = store.read_prompt_artifact(
        PromptArtifactRead(
            tenant_id="tenant-demo",
            artifact_ref=blob.artifact_ref,
            encryption_key_ref=blob.encryption_key_ref,
            expected_artifact_hash=blob.artifact_hash,
            expected_content_hash=_hash_text(raw_prompt),
        )
    )

    assert decrypted == raw_prompt
    assert raw_prompt.encode("utf-8") not in encrypted_file.read_bytes()
    assert blob.artifact_hash.startswith("sha256:")
    assert blob.encryption_key_ref == "secret://aip_prompt_artifact_encryption_key@sha256:test-key"
    assert secret_value not in str(blob)


def test_local_prompt_artifact_store_deletes_uncommitted_artifact(tmp_path: Path) -> None:
    raw_prompt = "prompt to clean up if receipt commit fails"
    store = LocalPromptArtifactStore(
        tmp_path,
        _StaticSecretProvider(
            SecretValue(
                name="aip_prompt_artifact_encryption_key",
                version="sha256:test-key",
                value=Fernet.generate_key().decode("ascii"),
            )
        ),
    )
    blob = store.write_prompt_artifact(
        PromptArtifactWrite(
            tenant_id="tenant-demo",
            ai_run_id="ai-run-1",
            artifact_id="ai-run-1-compiled-prompt",
            plaintext=raw_prompt,
        )
    )

    store.delete_prompt_artifact(PromptArtifactDelete(tenant_id="tenant-demo", artifact_ref=blob.artifact_ref))

    assert list(tmp_path.rglob("*.fernet")) == []


def test_local_prompt_artifact_store_local_dev_fallback_requires_explicit_opt_in(tmp_path: Path) -> None:
    raw_prompt = "local dev fallback prompt"
    closed_store = LocalPromptArtifactStore(tmp_path, _MissingSecretProvider())
    with pytest.raises(RuntimeError, match="missing secret"):
        closed_store.write_prompt_artifact(
            PromptArtifactWrite(
                tenant_id="tenant-demo",
                ai_run_id="ai-run-1",
                artifact_id="ai-run-1-compiled-prompt",
                plaintext=raw_prompt,
            )
        )

    opt_in_store = LocalPromptArtifactStore(tmp_path, _MissingSecretProvider(), allow_local_dev_fallback=True)
    blob = opt_in_store.write_prompt_artifact(
        PromptArtifactWrite(
            tenant_id="tenant-demo",
            ai_run_id="ai-run-1",
            artifact_id="ai-run-1-compiled-prompt",
            plaintext=raw_prompt,
        )
    )
    decrypted = opt_in_store.read_prompt_artifact(
        PromptArtifactRead(
            tenant_id="tenant-demo",
            artifact_ref=blob.artifact_ref,
            encryption_key_ref=blob.encryption_key_ref,
            expected_artifact_hash=blob.artifact_hash,
            expected_content_hash=_hash_text(raw_prompt),
        )
    )

    assert decrypted == raw_prompt
    assert blob.encryption_key_ref.startswith("local-dev://")


def test_local_prompt_artifact_store_fails_closed_on_hash_mismatch(tmp_path: Path) -> None:
    raw_prompt = "prompt with receipt integrity"
    store = LocalPromptArtifactStore(
        tmp_path,
        _StaticSecretProvider(
            SecretValue(
                name="aip_prompt_artifact_encryption_key",
                version="sha256:test-key",
                value=Fernet.generate_key().decode("ascii"),
            )
        ),
    )
    blob = store.write_prompt_artifact(
        PromptArtifactWrite(
            tenant_id="tenant-demo",
            ai_run_id="ai-run-1",
            artifact_id="ai-run-1-compiled-prompt",
            plaintext=raw_prompt,
        )
    )

    with pytest.raises(PromptArtifactStoreError, match="content hash mismatch"):
        store.read_prompt_artifact(
            PromptArtifactRead(
                tenant_id="tenant-demo",
                artifact_ref=blob.artifact_ref,
                encryption_key_ref=blob.encryption_key_ref,
                expected_artifact_hash=blob.artifact_hash,
                expected_content_hash=_hash_text("different prompt"),
            )
        )


def test_local_prompt_artifact_store_reads_artifact_with_receipt_key_version_after_rotation(tmp_path: Path) -> None:
    raw_prompt = "prompt encrypted before key rotation"
    old_secret = SecretValue(
        name="aip_prompt_artifact_encryption_key",
        version="sha256:old-key",
        value=Fernet.generate_key().decode("ascii"),
    )
    new_secret = SecretValue(
        name="aip_prompt_artifact_encryption_key",
        version="sha256:new-key",
        value=Fernet.generate_key().decode("ascii"),
    )
    provider = _VersionedSecretProvider(
        secrets_by_version={old_secret.version: old_secret, new_secret.version: new_secret},
        current_version=old_secret.version,
    )
    store = LocalPromptArtifactStore(tmp_path, provider)
    blob = store.write_prompt_artifact(
        PromptArtifactWrite(
            tenant_id="tenant-demo",
            ai_run_id="ai-run-1",
            artifact_id="ai-run-1-compiled-prompt",
            plaintext=raw_prompt,
        )
    )

    rotated_store = LocalPromptArtifactStore(
        tmp_path,
        _VersionedSecretProvider(
            secrets_by_version={old_secret.version: old_secret, new_secret.version: new_secret},
            current_version=new_secret.version,
        ),
    )
    decrypted = rotated_store.read_prompt_artifact(
        PromptArtifactRead(
            tenant_id="tenant-demo",
            artifact_ref=blob.artifact_ref,
            encryption_key_ref=blob.encryption_key_ref,
            expected_artifact_hash=blob.artifact_hash,
            expected_content_hash=_hash_text(raw_prompt),
        )
    )

    assert decrypted == raw_prompt
    assert blob.encryption_key_ref == "secret://aip_prompt_artifact_encryption_key@sha256:old-key"


def test_local_prompt_artifact_store_fails_closed_when_receipt_key_version_is_unavailable(tmp_path: Path) -> None:
    raw_prompt = "prompt encrypted before key rotation"
    old_secret = SecretValue(
        name="aip_prompt_artifact_encryption_key",
        version="sha256:old-key",
        value=Fernet.generate_key().decode("ascii"),
    )
    new_secret = SecretValue(
        name="aip_prompt_artifact_encryption_key",
        version="sha256:new-key",
        value=Fernet.generate_key().decode("ascii"),
    )
    store = LocalPromptArtifactStore(
        tmp_path,
        _VersionedSecretProvider(
            secrets_by_version={old_secret.version: old_secret}, current_version=old_secret.version
        ),
    )
    blob = store.write_prompt_artifact(
        PromptArtifactWrite(
            tenant_id="tenant-demo",
            ai_run_id="ai-run-1",
            artifact_id="ai-run-1-compiled-prompt",
            plaintext=raw_prompt,
        )
    )
    rotated_store_without_old_key = LocalPromptArtifactStore(
        tmp_path,
        _VersionedSecretProvider(
            secrets_by_version={new_secret.version: new_secret}, current_version=new_secret.version
        ),
    )

    with pytest.raises(PromptArtifactStoreError, match="key version is unavailable"):
        rotated_store_without_old_key.read_prompt_artifact(
            PromptArtifactRead(
                tenant_id="tenant-demo",
                artifact_ref=blob.artifact_ref,
                encryption_key_ref=blob.encryption_key_ref,
                expected_artifact_hash=blob.artifact_hash,
                expected_content_hash=_hash_text(raw_prompt),
            )
        )


def test_local_prompt_artifact_store_rejects_local_dev_ref_when_fallback_is_disabled(tmp_path: Path) -> None:
    raw_prompt = "local-dev encrypted prompt"
    opt_in_store = LocalPromptArtifactStore(tmp_path, _MissingSecretProvider(), allow_local_dev_fallback=True)
    blob = opt_in_store.write_prompt_artifact(
        PromptArtifactWrite(
            tenant_id="tenant-demo",
            ai_run_id="ai-run-1",
            artifact_id="ai-run-1-compiled-prompt",
            plaintext=raw_prompt,
        )
    )
    closed_store = LocalPromptArtifactStore(tmp_path, _MissingSecretProvider())

    with pytest.raises(PromptArtifactStoreError, match="fallback is not enabled"):
        closed_store.read_prompt_artifact(
            PromptArtifactRead(
                tenant_id="tenant-demo",
                artifact_ref=blob.artifact_ref,
                encryption_key_ref=blob.encryption_key_ref,
                expected_artifact_hash=blob.artifact_hash,
                expected_content_hash=_hash_text(raw_prompt),
            )
        )


def test_local_prompt_artifact_store_rejects_mismatched_local_dev_key_ref(tmp_path: Path) -> None:
    store = LocalPromptArtifactStore(tmp_path, _MissingSecretProvider(), allow_local_dev_fallback=True)

    with pytest.raises(PromptArtifactStoreError, match="local-dev key reference does not match"):
        store.read_prompt_artifact(
            PromptArtifactRead(
                tenant_id="tenant-demo",
                artifact_ref="local-prompt-artifact://tenant-demo/ai-run-1/artifact.fernet",
                encryption_key_ref="local-dev://aip_prompt_artifact_encryption_key@sha256:not-this-store",
                expected_artifact_hash="sha256:artifact",
                expected_content_hash="sha256:content",
            )
        )


def test_local_prompt_artifact_store_rejects_unsupported_or_incomplete_key_ref(tmp_path: Path) -> None:
    store = LocalPromptArtifactStore(tmp_path, _MissingSecretProvider())
    request = PromptArtifactRead(
        tenant_id="tenant-demo",
        artifact_ref="local-prompt-artifact://tenant-demo/ai-run-1/artifact.fernet",
        encryption_key_ref="kms://aip_prompt_artifact_encryption_key@sha256:key",
        expected_artifact_hash="sha256:artifact",
        expected_content_hash="sha256:content",
    )

    with pytest.raises(PromptArtifactStoreError, match="unsupported prompt artifact encryption key ref"):
        store.read_prompt_artifact(request)
    with pytest.raises(PromptArtifactStoreError, match="key ref is incomplete"):
        store.read_prompt_artifact(
            PromptArtifactRead(
                tenant_id=request.tenant_id,
                artifact_ref=request.artifact_ref,
                encryption_key_ref="secret://@sha256:key",
                expected_artifact_hash=request.expected_artifact_hash,
                expected_content_hash=request.expected_content_hash,
            )
        )


def test_local_prompt_artifact_store_rejects_wrong_secret_key_name_or_version(tmp_path: Path) -> None:
    secret = SecretValue(
        name="aip_prompt_artifact_encryption_key",
        version="sha256:actual",
        value=Fernet.generate_key().decode("ascii"),
    )
    request = PromptArtifactRead(
        tenant_id="tenant-demo",
        artifact_ref="local-prompt-artifact://tenant-demo/ai-run-1/artifact.fernet",
        encryption_key_ref="secret://other_prompt_key@sha256:actual",
        expected_artifact_hash="sha256:artifact",
        expected_content_hash="sha256:content",
    )

    with pytest.raises(PromptArtifactStoreError, match="key name is not supported"):
        LocalPromptArtifactStore(tmp_path, _MismatchedVersionSecretProvider(secret)).read_prompt_artifact(request)
    with pytest.raises(PromptArtifactStoreError, match="key version does not match receipt"):
        LocalPromptArtifactStore(tmp_path, _MismatchedVersionSecretProvider(secret)).read_prompt_artifact(
            PromptArtifactRead(
                tenant_id=request.tenant_id,
                artifact_ref=request.artifact_ref,
                encryption_key_ref="secret://aip_prompt_artifact_encryption_key@sha256:expected",
                expected_artifact_hash=request.expected_artifact_hash,
                expected_content_hash=request.expected_content_hash,
            )
        )


def test_local_prompt_artifact_store_declares_failure_taxonomy(tmp_path: Path) -> None:
    store = LocalPromptArtifactStore(tmp_path, _MissingSecretProvider())

    contract = store.failure_contract()
    modes = {(mode.operation, mode.kind, mode.is_retryable) for mode in contract.modes}

    assert contract.adapter_profile == "local-prompt-artifact-store"
    assert ("write_prompt_artifact", "unavailable", True) in modes
    assert ("read_prompt_artifact", "authentication", True) in modes
    assert ("read_prompt_artifact", "conflict", False) in modes
    assert ("read_prompt_artifact", "not_found", False) in modes
    assert ("delete_prompt_artifact", "unavailable", True) in modes


def _hash_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"
