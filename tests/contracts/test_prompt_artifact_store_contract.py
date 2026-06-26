"""Contract for encrypted prompt artifact storage."""

from __future__ import annotations

import hashlib
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

    def get_secret(self, name: str) -> SecretValue:
        assert name == self.secret.name
        return self.secret


@dataclass(frozen=True)
class _MissingSecretProvider:
    profile_name: str = "missing-secret"

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=())

    def get_secret(self, name: str) -> SecretValue:
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


def _hash_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"
