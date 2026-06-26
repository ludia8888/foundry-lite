"""Contract for encrypted prompt artifact storage."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet
from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.application.ports.prompt_artifact_store import PromptArtifactRead, PromptArtifactWrite
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
        )
    )

    assert decrypted == raw_prompt
    assert raw_prompt.encode("utf-8") not in encrypted_file.read_bytes()
    assert blob.artifact_hash.startswith("sha256:")
    assert blob.encryption_key_ref == "secret://aip_prompt_artifact_encryption_key@sha256:test-key"
    assert secret_value not in str(blob)
