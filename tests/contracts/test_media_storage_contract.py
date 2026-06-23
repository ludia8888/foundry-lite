"""Contract for ``MediaStorageAdapter`` (ADR-0001 §5.1).

Storage is the swap point between the local filesystem profile (this sprint) and a
future S3 profile. The contract pins the staged/committed blob lifecycle and the
DTO shapes so any profile is interchangeable. Behaviour-bearing assertions land
with the first concrete adapter; here we lock the Protocol shape and DTO surface.
"""

from __future__ import annotations

import io
from typing import BinaryIO

from foundry_lite.application.ports.media_storage import (
    ByteRange,
    CommittedMediaObject,
    CompleteMediaUpload,
    InitiateMediaUpload,
    MediaObjectStat,
    MediaReadGrant,
    MediaReadGrantRequest,
    MediaStorageAdapter,
    StagedMediaObject,
    UploadSession,
)


class _FakeMediaStorage:
    """Minimal in-memory adapter that satisfies the MediaStorageAdapter contract."""

    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    @property
    def profile_name(self) -> str:
        return "fake-local"

    def initiate_upload(self, request: InitiateMediaUpload) -> UploadSession:
        return UploadSession(upload_id="up-1", staged_object_key=f"staged/{request.logical_path}")

    def write_staged(self, staged_object_key: str, source: BinaryIO) -> None:
        self._blobs[staged_object_key] = source.read()

    def complete_staged_upload(self, request: CompleteMediaUpload) -> StagedMediaObject:
        body = self._blobs[request.staged_object_key]
        return StagedMediaObject(
            object_key=request.staged_object_key,
            byte_size=len(body),
            content_hash=f"h{len(body)}",
            sniffed_mime_type="application/pdf",
        )

    def stat(self, object_key: str) -> MediaObjectStat:
        body = self._blobs.get(object_key)
        return MediaObjectStat(
            object_key=object_key,
            byte_size=len(body) if body is not None else 0,
            content_hash=f"h{len(body)}" if body is not None else "",
            is_present=body is not None,
        )

    def open_stream(self, object_key: str, byte_range: ByteRange | None = None) -> BinaryIO:
        return io.BytesIO(self._blobs[object_key])

    def commit_reference(self, staged_key: str, committed_key: str) -> CommittedMediaObject:
        body = self._blobs.pop(staged_key)
        self._blobs[committed_key] = body
        return CommittedMediaObject(object_key=committed_key, byte_size=len(body), content_hash=f"h{len(body)}")

    def delete_uncommitted(self, object_key: str) -> None:
        self._blobs.pop(object_key, None)

    def issue_read_grant(self, request: MediaReadGrantRequest) -> MediaReadGrant:
        return MediaReadGrant(
            grant_kind="inline", object_key=request.object_key, stream=io.BytesIO(self._blobs[request.object_key])
        )


def test_media_storage_adapter_shape_is_satisfiable() -> None:
    adapter: MediaStorageAdapter = _FakeMediaStorage()
    session = adapter.initiate_upload(
        InitiateMediaUpload(
            tenant_id="t", media_set_id="ms", logical_path="/a.pdf", supplied_mime_type="application/pdf"
        )
    )
    adapter.write_staged(session.staged_object_key, io.BytesIO(b"data"))
    staged = adapter.complete_staged_upload(
        CompleteMediaUpload(upload_id=session.upload_id, staged_object_key=session.staged_object_key)
    )
    stat = adapter.stat(staged.object_key)
    committed = adapter.commit_reference(staged.object_key, "committed/a.pdf")
    grant = adapter.issue_read_grant(
        MediaReadGrantRequest(tenant_id="t", media_item_version_id="miv", object_key=committed.object_key)
    )

    assert adapter.profile_name == "fake-local"
    assert stat.is_present is True and stat.byte_size == 4
    assert committed.object_key == "committed/a.pdf"
    assert grant.object_key == "committed/a.pdf"
    assert adapter.open_stream(committed.object_key, ByteRange(start=0, end=1)).read() == b"data"
