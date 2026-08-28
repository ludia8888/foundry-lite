"""Application orchestration for verified processor-facing media materialization."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import BinaryIO

from foundry_lite.application.media_byte_verification import (
    MediaByteVerificationFailure,
    copy_verified_committed_media,
    raise_media_byte_domain_error,
)
from foundry_lite.application.ports.media_repository import MediaItemVersionRecord
from foundry_lite.application.ports.media_source_workspace import (
    MediaSourceWorkspace,
    MediaSourceWorkspaceRequest,
)
from foundry_lite.application.ports.media_storage import MediaStorageAdapter

__all__ = [
    "MediaByteVerificationFailure",
    "materialized_verified_media",
    "raise_media_byte_domain_error",
]


@contextmanager
def materialized_verified_media(
    workspace: MediaSourceWorkspace,
    media_storage: MediaStorageAdapter,
    version: MediaItemVersionRecord,
    *,
    file_name: str,
) -> Iterator[str]:
    """Verify committed bytes while writing them into a short-lived workspace."""

    def write_source(sink: BinaryIO) -> None:
        copy_verified_committed_media(media_storage, version, sink)

    with workspace.materialize(MediaSourceWorkspaceRequest(file_name), write_source) as source:
        yield source.source_path
