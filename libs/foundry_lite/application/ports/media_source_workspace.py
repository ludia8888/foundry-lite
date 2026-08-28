"""Port for materializing verified media bytes into a processor workspace."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import BinaryIO, Protocol

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract

MediaSourceWriter = Callable[[BinaryIO], None]


@dataclass(frozen=True)
class MediaSourceWorkspaceRequest:
    """One sandbox file requested by a processor-facing application flow."""

    file_name: str


@dataclass(frozen=True)
class MaterializedMediaSource:
    """Short-lived local path valid only inside the returned context manager."""

    source_path: str


class MediaSourceWorkspace(Protocol):
    """Own temporary path creation, source writing, and guaranteed cleanup."""

    profile_name: str

    def failure_contract(self) -> AdapterFailureContract: ...

    def materialize(
        self,
        request: MediaSourceWorkspaceRequest,
        write_source: MediaSourceWriter,
    ) -> AbstractContextManager[MaterializedMediaSource]: ...
