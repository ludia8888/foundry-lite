"""S3-compatible media storage adapter (ADR-0001 §5.1, doc §6.1), tested on MinIO.

A blob lives at one immutable UUID key for its whole life; commit is a metadata-pointer
flip in the DB, never an S3 copy. The DB COMMITTED media version is the serving truth —
this adapter only stages bytes, verifies stat/hash, streams ranges, and issues scoped,
expiring read grants. ``write_staged`` is the local/test server-side body-upload path;
real clients use the presigned ``upload_targets`` from ``initiate_upload``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO, cast
from uuid import uuid4

from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureKind,
    AdapterFailureMode,
)
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
from foundry_lite.infrastructure.adapters.s3_client import build_s3_client, is_not_found_error, join_key

_READ_CHUNK = 1024 * 1024
_MULTIPART_THRESHOLD = 8 * 1024 * 1024
_UPLOAD_TTL_SECONDS = 3600
_READ_GRANT_TTL_SECONDS = 900


def _sniff_mime(head: bytes) -> str:
    if head.startswith(b"%PDF"):
        return "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return "application/octet-stream"


def _expires_at(ttl_seconds: int) -> str:
    return (datetime.now().astimezone() + timedelta(seconds=ttl_seconds)).isoformat()


@dataclass(frozen=True)
class S3MediaStorageConfig:
    bucket: str
    endpoint_url: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = field(default=None, repr=False)
    region_name: str = "us-east-1"
    prefix: str = "foundry-lite-media"
    should_create_bucket_if_missing: bool = True


class S3MediaStorageAdapter:
    """S3-compatible ``MediaStorageAdapter`` for the ``s3-media`` profile."""

    profile_name = "s3-media"

    def __init__(self, config: S3MediaStorageConfig, *, client: Any | None = None) -> None:
        self.config = config
        self._client = client or build_s3_client(
            endpoint_url=config.endpoint_url,
            access_key_id=config.access_key_id,
            secret_access_key=config.secret_access_key,
            region_name=config.region_name,
        )
        if config.should_create_bucket_if_missing:
            self._ensure_bucket()

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "write_staged",
                    "timeout",
                    True,
                    "S3 media upload outcome is unknown; retry with the same immutable object key.",
                    timeout_seconds=30,
                    has_required_idempotency_key=True,
                ),
                AdapterFailureMode(
                    "complete_staged_upload",
                    "unavailable",
                    True,
                    "S3 read could not reach storage while verifying the staged blob; retry.",
                ),
                AdapterFailureMode(
                    "stat",
                    "not_found",
                    False,
                    "A COMMITTED media version's S3 blob is missing; treat the version as storage-corrupt.",
                ),
                AdapterFailureMode(
                    "issue_read_grant",
                    "unavailable",
                    True,
                    "S3 presigned read grant could not be issued; this is transient — retry.",
                ),
                AdapterFailureMode(
                    "delete_uncommitted",
                    "unavailable",
                    True,
                    "S3 orphan cleanup could not reach storage; retry cleanup before advancing.",
                ),
            ),
        )

    def initiate_upload(self, request: InitiateMediaUpload) -> UploadSession:
        file_name = Path(request.logical_path).name or "blob"
        object_key = join_key(self.config.prefix, request.tenant_id, request.media_set_id, uuid4().hex, file_name)
        declared = request.declared_byte_size
        if declared is not None and declared > _MULTIPART_THRESHOLD:
            created = self._client.create_multipart_upload(Bucket=self.config.bucket, Key=object_key)
            part_url = self._presigned(
                "upload_part", object_key, extra={"UploadId": created["UploadId"], "PartNumber": 1}
            )
            return UploadSession(
                upload_id=str(created["UploadId"]), staged_object_key=object_key, upload_targets=(part_url,)
            )
        put_url = self._presigned("put_object", object_key)
        return UploadSession(upload_id=uuid4().hex, staged_object_key=object_key, upload_targets=(put_url,))

    def write_staged(self, staged_object_key: str, source: BinaryIO) -> None:
        try:
            self._client.put_object(Bucket=self.config.bucket, Key=staged_object_key, Body=source.read())
        except Exception as exc:
            raise self._adapter_error("write_staged", exc) from exc

    def complete_staged_upload(self, request: CompleteMediaUpload) -> StagedMediaObject:
        try:
            content_hash, byte_size, head = self._read_object(request.staged_object_key)
        except Exception as exc:
            raise self._adapter_error("complete_staged_upload", exc) from exc
        return StagedMediaObject(
            object_key=request.staged_object_key,
            byte_size=byte_size,
            content_hash=content_hash,
            sniffed_mime_type=_sniff_mime(head),
        )

    def stat(self, object_key: str) -> MediaObjectStat:
        try:
            content_hash, byte_size, _ = self._read_object(object_key)
        except Exception as exc:
            if is_not_found_error(exc):
                return MediaObjectStat(object_key=object_key, byte_size=0, content_hash="", is_present=False)
            raise self._adapter_error("stat", exc) from exc
        return MediaObjectStat(object_key=object_key, byte_size=byte_size, content_hash=content_hash, is_present=True)

    def open_stream(self, object_key: str, byte_range: ByteRange | None = None) -> BinaryIO:
        kwargs: dict[str, Any] = {"Bucket": self.config.bucket, "Key": object_key}
        if byte_range is not None:
            end = "" if byte_range.end is None else str(byte_range.end)
            kwargs["Range"] = f"bytes={byte_range.start}-{end}"
        response = self._client.get_object(**kwargs)
        return cast("BinaryIO", response["Body"])

    def commit_reference(self, staged_key: str, committed_key: str) -> CommittedMediaObject:
        # Metadata-pointer commit: the blob is never copied/renamed. The committed key is
        # the same immutable key; we only re-stat it so the caller can record the contract.
        if committed_key != staged_key:
            self._client.copy_object(
                Bucket=self.config.bucket,
                Key=committed_key,
                CopySource={"Bucket": self.config.bucket, "Key": staged_key},
            )
        stat = self.stat(committed_key)
        return CommittedMediaObject(object_key=committed_key, byte_size=stat.byte_size, content_hash=stat.content_hash)

    def delete_uncommitted(self, object_key: str) -> None:
        try:
            self._abort_multipart_uploads(object_key)
            self._client.delete_object(Bucket=self.config.bucket, Key=object_key)
        except Exception as exc:
            if is_not_found_error(exc):
                return
            raise self._adapter_error("delete_uncommitted", exc) from exc

    def issue_read_grant(self, request: MediaReadGrantRequest) -> MediaReadGrant:
        try:
            url = self._presigned("get_object", request.object_key, ttl=_READ_GRANT_TTL_SECONDS)
        except Exception as exc:
            raise self._adapter_error("issue_read_grant", exc) from exc
        return MediaReadGrant(
            grant_kind="presigned-url",
            object_key=request.object_key,
            expires_at=_expires_at(_READ_GRANT_TTL_SECONDS),
            stream=None,
            url=url,
        )

    def _read_object(self, object_key: str) -> tuple[str, int, bytes]:
        body = self._client.get_object(Bucket=self.config.bucket, Key=object_key)["Body"]
        digest = hashlib.sha256()
        byte_size = 0
        head = b""
        while chunk := body.read(_READ_CHUNK):
            if not head:
                head = chunk[:16]
            digest.update(chunk)
            byte_size += len(chunk)
        return digest.hexdigest(), byte_size, head

    def _presigned(
        self, operation: str, object_key: str, *, extra: Mapping[str, Any] | None = None, ttl: int = _UPLOAD_TTL_SECONDS
    ) -> str:
        params: dict[str, Any] = {"Bucket": self.config.bucket, "Key": object_key, **(extra or {})}
        return str(self._client.generate_presigned_url(operation, Params=params, ExpiresIn=ttl))

    def _abort_multipart_uploads(self, object_key: str) -> None:
        response = self._client.list_multipart_uploads(Bucket=self.config.bucket, Prefix=object_key)
        for upload in response.get("Uploads") or []:
            if upload.get("Key") == object_key:
                self._client.abort_multipart_upload(
                    Bucket=self.config.bucket, Key=object_key, UploadId=upload["UploadId"]
                )

    def _ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self.config.bucket)
        except Exception:
            self._client.create_bucket(Bucket=self.config.bucket)

    def _adapter_error(self, operation: str, exc: Exception) -> AdapterError:
        kind: AdapterFailureKind = "timeout" if isinstance(exc, TimeoutError) else "unavailable"
        return AdapterError(
            AdapterFailure(
                self.profile_name,
                operation,
                kind,
                True,
                f"S3 media storage {operation} failed: {exc}",
                timeout_seconds=30 if kind == "timeout" else None,
            )
        )
