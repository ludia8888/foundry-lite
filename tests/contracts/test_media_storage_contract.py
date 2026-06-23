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

    @property
    def profile_name(self) -> str:
        return "fake-local"

    def initiate_upload(self, request: InitiateMediaUpload) -> UploadSession:
        return UploadSession(upload_id="up-1", staged_object_key=f"staged/{request.logical_path}")

    def complete_staged_upload(self, request: CompleteMediaUpload) -> StagedMediaObject:
        return StagedMediaObject(
            object_key=request.staged_object_key, byte_size=4, content_hash="h", sniffed_mime_type="application/pdf"
        )

    def stat(self, object_key: str) -> MediaObjectStat:
        return MediaObjectStat(object_key=object_key, byte_size=4, content_hash="h", is_present=True)

    def open_stream(self, object_key: str, byte_range: ByteRange | None = None) -> BinaryIO:
        return io.BytesIO(b"data")

    def commit_reference(self, staged_key: str, committed_key: str) -> CommittedMediaObject:
        return CommittedMediaObject(object_key=committed_key, byte_size=4, content_hash="h")

    def delete_uncommitted(self, object_key: str) -> None:
        return None

    def issue_read_grant(self, request: MediaReadGrantRequest) -> MediaReadGrant:
        return MediaReadGrant(grant_kind="inline", object_key=request.object_key, stream=io.BytesIO(b"data"))


def test_media_storage_adapter_shape_is_satisfiable() -> None:
    adapter: MediaStorageAdapter = _FakeMediaStorage()
    session = adapter.initiate_upload(
        InitiateMediaUpload(
            tenant_id="t", media_set_id="ms", logical_path="/a.pdf", supplied_mime_type="application/pdf"
        )
    )
    staged = adapter.complete_staged_upload(
        CompleteMediaUpload(upload_id=session.upload_id, staged_object_key=session.staged_object_key)
    )
    stat = adapter.stat(staged.object_key)
    committed = adapter.commit_reference(staged.object_key, "committed/a.pdf")
    grant = adapter.issue_read_grant(
        MediaReadGrantRequest(tenant_id="t", media_item_version_id="miv", object_key=committed.object_key)
    )

    assert adapter.profile_name == "fake-local"
    assert stat.is_present is True
    assert grant.object_key == "committed/a.pdf"
    assert adapter.open_stream(committed.object_key, ByteRange(start=0, end=1)).read() == b"data"
