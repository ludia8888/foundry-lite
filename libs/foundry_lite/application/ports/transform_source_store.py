"""Application port for persisted transform source artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract

TransformSourceLanguage = Literal["python", "sql"]


@dataclass(frozen=True)
class TransformSourceWrite:
    """Source payload that must be persisted before a transform definition is committed."""

    tenant_id: str
    api_name: str
    language: TransformSourceLanguage
    source_code: str


@dataclass(frozen=True)
class TransformSourceArtifact:
    """Stable entrypoint returned by a transform source store."""

    entrypoint: str
    content_hash: str
    byte_size: int


@dataclass(frozen=True)
class TransformSourceRead:
    """Stable source entrypoint requested by a transform execution."""

    entrypoint: str


@dataclass(frozen=True)
class TransformSourceContent:
    """Decoded source plus integrity metadata returned by the store."""

    source_code: str
    content_hash: str
    byte_size: int


@runtime_checkable
class TransformSourceStore(Protocol):
    """Boundary that keeps transform source I/O outside the application layer."""

    @property
    def profile_name(self) -> str: ...

    def failure_contract(self) -> AdapterFailureContract:
        """Return the operator-facing failure taxonomy for this store."""
        ...

    def write_source(self, request: TransformSourceWrite) -> TransformSourceArtifact:
        """Persist one source artifact and return its stable execution entrypoint."""
        ...

    def read_source(self, request: TransformSourceRead) -> TransformSourceContent:
        """Read one persisted UTF-8 source artifact through the storage boundary."""
        ...
