"""Local filesystem media storage adapter (ADR-0001 §5.1, doc §6.1).

The local/test profile lands upload bytes in-process (``write_staged``) instead of
issuing presigned URLs. A blob lives at one immutable key for its whole life; commit
is a metadata-pointer flip that moves the staged key into the committed namespace.
The DB ``COMMITTED`` row is the serving truth — this adapter never decides visibility.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from foundry_lite.application.ports.media_storage import (
    ByteRange,
    CommittedMediaObject,
    CompleteMediaUpload,
    InitiateMediaUpload,
    MediaObjectStat,
    MediaReadGrant,
    MediaReadGrantRequest,
    StagedMediaObject,
    UploadSession,
)

_READ_CHUNK = 1024 * 1024


def _sniff_mime(head: bytes) -> str:
    if head.startswith(b"%PDF"):
        return "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return "application/octet-stream"


class LocalMediaStorageAdapter:
    """Filesystem-backed ``MediaStorageAdapter`` for the local/test profile."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def profile_name(self) -> str:
        return "local"

    def _path(self, object_key: str) -> Path:
        return self.root / object_key

    def initiate_upload(self, request: InitiateMediaUpload) -> UploadSession:
        file_name = Path(request.logical_path).name or "blob"
        staged_object_key = f"staged/{uuid4().hex}/{file_name}"
        return UploadSession(upload_id=uuid4().hex, staged_object_key=staged_object_key, upload_targets=None)

    def write_staged(self, staged_object_key: str, source: BinaryIO) -> None:
        path = self._path(staged_object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as sink:
            shutil.copyfileobj(source, sink)

    def complete_staged_upload(self, request: CompleteMediaUpload) -> StagedMediaObject:
        path = self._path(request.staged_object_key)
        digest = hashlib.sha256()
        byte_size = 0
        head = b""
        with path.open("rb") as handle:
            while chunk := handle.read(_READ_CHUNK):
                if not head:
                    head = chunk[:16]
                digest.update(chunk)
                byte_size += len(chunk)
        return StagedMediaObject(
            object_key=request.staged_object_key,
            byte_size=byte_size,
            content_hash=digest.hexdigest(),
            sniffed_mime_type=_sniff_mime(head),
        )

    def stat(self, object_key: str) -> MediaObjectStat:
        path = self._path(object_key)
        if not path.is_file():
            return MediaObjectStat(object_key=object_key, byte_size=0, content_hash="", is_present=False)
        digest = hashlib.sha256()
        byte_size = 0
        with path.open("rb") as handle:
            while chunk := handle.read(_READ_CHUNK):
                digest.update(chunk)
                byte_size += len(chunk)
        return MediaObjectStat(
            object_key=object_key, byte_size=byte_size, content_hash=digest.hexdigest(), is_present=True
        )

    def open_stream(self, object_key: str, byte_range: ByteRange | None = None) -> BinaryIO:
        handle = self._path(object_key).open("rb")
        if byte_range is not None:
            handle.seek(byte_range.start)
        return handle

    def commit_reference(self, staged_key: str, committed_key: str) -> CommittedMediaObject:
        source = self._path(staged_key)
        target = self._path(committed_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.move(str(source), str(target))
        stat = self.stat(committed_key)
        return CommittedMediaObject(object_key=committed_key, byte_size=stat.byte_size, content_hash=stat.content_hash)

    def delete_uncommitted(self, object_key: str) -> None:
        path = self._path(object_key)
        path.unlink(missing_ok=True)

    def issue_read_grant(self, request: MediaReadGrantRequest) -> MediaReadGrant:
        return MediaReadGrant(
            grant_kind="inline",
            object_key=request.object_key,
            expires_at=None,
            stream=self.open_stream(request.object_key, request.byte_range),
        )
