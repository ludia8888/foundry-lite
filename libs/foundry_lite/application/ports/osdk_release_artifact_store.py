"""Application port for OSDK release artifact persistence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract


@dataclass(frozen=True)
class OsdkReleaseArtifactWrite:
    tenant_id: str
    app_id: str
    version: str
    file_name: str
    content: bytes


@dataclass(frozen=True)
class OsdkStoredReleaseArtifact:
    storage_uri: str
    content_hash: str
    byte_size: int


@dataclass(frozen=True)
class OsdkReleaseArtifactContent:
    content: bytes
    content_hash: str
    byte_size: int


@runtime_checkable
class OsdkReleaseArtifactStore(Protocol):
    """Persist and read generated OSDK package bytes independently of application rules."""

    @property
    def profile_name(self) -> str: ...

    def failure_contract(self) -> AdapterFailureContract:
        """Return the operator-facing failure taxonomy for this store."""
        ...

    def write_artifact(self, request: OsdkReleaseArtifactWrite) -> OsdkStoredReleaseArtifact:
        """Persist one deterministic release artifact."""
        ...

    def read_artifact(self, storage_uri: str) -> OsdkReleaseArtifactContent:
        """Read one previously persisted release artifact."""
        ...
