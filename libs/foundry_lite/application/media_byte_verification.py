"""Application-level immutable-byte verification for committed media reads."""

from __future__ import annotations

import hashlib
from typing import BinaryIO, Literal, NoReturn

from foundry_lite.application.ports.media_repository import MediaItemVersionRecord
from foundry_lite.application.ports.media_storage import MediaStorageAdapter
from foundry_lite.domain.errors import ConflictDetected, NotFound

MediaByteFailureReason = Literal[
    "storage_missing",
    "storage_metadata_mismatch",
    "stream_size_mismatch",
    "stream_hash_mismatch",
]
_READ_CHUNK_BYTES = 1024 * 1024


class MediaByteVerificationFailure(Exception):
    """Safe failure raised before unverified committed bytes reach a consumer."""

    def __init__(self, reason: MediaByteFailureReason, media_item_version_id: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.media_item_version_id = media_item_version_id


def copy_verified_committed_media(
    media_storage: MediaStorageAdapter,
    version: MediaItemVersionRecord,
    sink: BinaryIO,
) -> None:
    """Copy only when storage metadata and streamed bytes match catalog truth."""
    stat = media_storage.stat(version.blob_key)
    if not stat.is_present:
        raise MediaByteVerificationFailure("storage_missing", version.media_item_version_id)
    if stat.byte_size != version.byte_size or _normalized_hash(stat.content_hash) != _normalized_hash(
        version.content_hash
    ):
        raise MediaByteVerificationFailure("storage_metadata_mismatch", version.media_item_version_id)
    digest = hashlib.sha256()
    byte_count = 0
    with media_storage.open_stream(version.blob_key) as source:
        while chunk := source.read(_READ_CHUNK_BYTES):
            byte_count += len(chunk)
            if byte_count > version.byte_size:
                raise MediaByteVerificationFailure("stream_size_mismatch", version.media_item_version_id)
            digest.update(chunk)
            sink.write(chunk)
    if byte_count != version.byte_size:
        raise MediaByteVerificationFailure("stream_size_mismatch", version.media_item_version_id)
    if digest.hexdigest() != _normalized_hash(version.content_hash):
        raise MediaByteVerificationFailure("stream_hash_mismatch", version.media_item_version_id)


def raise_media_byte_domain_error(
    failure: MediaByteVerificationFailure,
    *,
    operation: str,
) -> NoReturn:
    details: dict[str, object] = {
        "mediaItemVersionId": failure.media_item_version_id,
        "reason": failure.reason,
        "operation": operation,
    }
    if failure.reason == "storage_missing":
        raise NotFound("committed media bytes are missing", details=details)
    raise ConflictDetected("committed media bytes do not match catalog truth", details=details)


def _normalized_hash(value: str) -> str:
    return value.removeprefix("sha256:")
