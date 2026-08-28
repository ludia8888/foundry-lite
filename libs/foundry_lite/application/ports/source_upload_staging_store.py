"""Port for staging user-provided Source upload streams."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract


@dataclass(frozen=True)
class SourceUploadStageRequest:
    """One upload stream to stage beneath a Source-owned namespace."""

    source_name: str
    file_name: str
    source: BinaryIO


@dataclass(frozen=True)
class StagedSourceArtifact:
    """Opaque staged artifact reference plus byte-level evidence."""

    file_name: str
    storage_uri: str
    content_hash: str
    byte_size: int


class SourceUploadStagingStore(Protocol):
    """Stage, materialize, read, and clean up transient Source uploads."""

    profile_name: str

    def failure_contract(self) -> AdapterFailureContract: ...

    def stage_uploads(self, requests: Sequence[SourceUploadStageRequest]) -> tuple[StagedSourceArtifact, ...]: ...

    def materialize_path(self, storage_uri: str) -> AbstractContextManager[Path]: ...

    def open_upload(self, storage_uri: str) -> BinaryIO: ...

    def cleanup_uploads(self, storage_uris: Sequence[str]) -> None: ...
