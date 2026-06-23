from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO, Protocol


@dataclass(frozen=True)
class ByteRange:
    """Inclusive-start, optional-end byte range for partial reads."""

    start: int
    end: int | None = None


@dataclass(frozen=True)
class InitiateMediaUpload:
    """Request to begin staging an immutable media blob for one logical path."""

    tenant_id: str
    media_set_id: str
    logical_path: str
    supplied_mime_type: str
    declared_byte_size: int | None = None


@dataclass(frozen=True)
class UploadSession:
    """Handle for an in-flight staged upload (local body upload or multipart targets)."""

    upload_id: str
    staged_object_key: str
    upload_targets: tuple[str, ...] | None = None


@dataclass(frozen=True)
class CompleteMediaUpload:
    """Request to finalize a staged upload into a stat-able staged object."""

    upload_id: str
    staged_object_key: str


@dataclass(frozen=True)
class StagedMediaObject:
    """A staged (not yet committed) blob with its measured size, hash, and sniffed MIME."""

    object_key: str
    byte_size: int
    content_hash: str
    sniffed_mime_type: str


@dataclass(frozen=True)
class MediaObjectStat:
    """Storage-level stat used to verify the DB-committed version against real bytes."""

    object_key: str
    byte_size: int
    content_hash: str
    is_present: bool


@dataclass(frozen=True)
class CommittedMediaObject:
    """A blob promoted into the committed namespace by a metadata-pointer commit."""

    object_key: str
    byte_size: int
    content_hash: str


@dataclass(frozen=True)
class MediaReadGrantRequest:
    """Request for a scoped, tenant/version-pinned read of a committed media blob."""

    tenant_id: str
    media_item_version_id: str
    object_key: str
    byte_range: ByteRange | None = None


@dataclass(frozen=True)
class MediaReadGrant:
    """Application-level read grant; local profile inlines a stream, S3 issues a signed URL."""

    grant_kind: str
    object_key: str
    expires_at: str | None = None
    stream: BinaryIO | None = None


class MediaStorageAdapter(Protocol):
    """Storage boundary for staged and committed immutable media blobs.

    Separate from ``DatasetStorageAdapter``: media is binary, range-readable, and has
    no ``row_count``/Parquet semantics. The DB ``COMMITTED`` media version is the
    serving truth; blob existence alone is never success (the metadata-pointer commit
    only makes a blob reachable, it does not certify it).
    """

    @property
    def profile_name(self) -> str: ...

    def initiate_upload(self, request: InitiateMediaUpload) -> UploadSession:
        """Allocate an immutable staged object key and start an upload session."""
        ...

    def complete_staged_upload(self, request: CompleteMediaUpload) -> StagedMediaObject:
        """Finalize staged bytes and return measured size, content hash, and sniffed MIME."""
        ...

    def stat(self, object_key: str) -> MediaObjectStat:
        """Return blob existence/size/hash so a committed version can be verified against storage."""
        ...

    def open_stream(self, object_key: str, byte_range: ByteRange | None = None) -> BinaryIO:
        """Open a (optionally range-bounded) read stream over a stored blob."""
        ...

    def commit_reference(self, staged_key: str, committed_key: str) -> CommittedMediaObject:
        """Promote a staged blob into the committed namespace (pointer flip, not the truth)."""
        ...

    def delete_uncommitted(self, object_key: str) -> None:
        """Remove an unreachable staged blob during orphan cleanup."""
        ...

    def issue_read_grant(self, request: MediaReadGrantRequest) -> MediaReadGrant:
        """Issue a scoped read grant for one tenant-scoped, version-pinned blob."""
        ...
