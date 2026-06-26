"""Encrypted prompt artifact storage port.

General AI ledger rows keep refs and hashes only. This port is the separate
protected boundary for raw compiled prompt bytes when an explicit reader needs
to inspect them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class PromptArtifactWrite:
    tenant_id: str
    ai_run_id: str
    artifact_id: str
    plaintext: str


@dataclass(frozen=True)
class PromptArtifactBlob:
    artifact_ref: str
    artifact_hash: str
    byte_size: int
    encryption_key_ref: str
    encryption_algorithm: str


@dataclass(frozen=True)
class PromptArtifactRead:
    tenant_id: str
    artifact_ref: str
    encryption_key_ref: str


@runtime_checkable
class PromptArtifactStore(Protocol):
    """Boundary for encrypted prompt payloads outside the general ledger DB."""

    @property
    def profile_name(self) -> str: ...

    def write_prompt_artifact(self, request: PromptArtifactWrite) -> PromptArtifactBlob:
        """Encrypt and store one raw prompt artifact."""
        ...

    def read_prompt_artifact(self, request: PromptArtifactRead) -> str:
        """Decrypt one prompt artifact for an explicitly authorized caller."""
        ...
