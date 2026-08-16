"""Immutable S3-compatible backup artifact storage adapter."""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from foundry_lite.application.ports.backup_artifact_store import (
    BackupArtifactIntegrityError,
    BackupArtifactNotFoundError,
)
from foundry_lite.application.runtime_repository_backup_restore import (
    BackupRestoreArtifact,
    BackupRestoreArtifactReceipt,
    BackupRestorePreflightReport,
)
from foundry_lite.infrastructure.adapters.backup_artifact_codec import (
    backup_artifact_hash,
    backup_artifact_payload,
    build_backup_artifact,
    replay_backup_artifact_receipt,
    verify_backup_artifact,
)
from foundry_lite.infrastructure.adapters.s3_client import (
    build_s3_client,
    is_not_found_error,
    is_precondition_failed_error,
    join_key,
)

_ARTIFACT_SCHEME = "s3-backup-artifact://"
_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9_.=-]+")


@dataclass(frozen=True)
class S3BackupArtifactStoreConfig:
    bucket: str
    endpoint_url: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None
    region_name: str = "us-east-1"
    prefix: str = "foundry-lite/backup-artifacts"
    max_artifact_bytes: int = 16 * 1024 * 1024


class S3BackupArtifactStore:
    """Persist restore receipts as immutable, hash-bound S3 objects."""

    profile_name = "s3-backup-artifact-store"

    def __init__(self, config: S3BackupArtifactStoreConfig, *, client: Any | None = None) -> None:
        if not config.bucket.strip() or config.max_artifact_bytes <= 0:
            raise ValueError("S3 backup artifact bucket and positive size limit are required")
        self._config = config
        self._prefix = config.prefix.strip("/")
        self._client = client or build_s3_client(
            endpoint_url=config.endpoint_url,
            access_key_id=config.access_key_id,
            secret_access_key=config.secret_access_key,
            region_name=config.region_name,
        )

    def write_backup_artifact(self, report: BackupRestorePreflightReport) -> BackupRestoreArtifactReceipt:
        object_key = self._key_for_report(report)
        artifact_ref = self._ref_for_key(object_key)
        requested_hash = backup_artifact_hash(backup_artifact_payload(report))
        receipt, encoded = build_backup_artifact(report, artifact_ref)
        if len(encoded) > self._config.max_artifact_bytes:
            raise BackupArtifactIntegrityError(artifact_ref, "artifact exceeds configured size limit")
        try:
            self._client.put_object(
                Bucket=self._config.bucket,
                Key=object_key,
                Body=encoded,
                ContentType="application/json",
                IfNoneMatch="*",
            )
            return receipt
        except Exception as exc:
            if not is_precondition_failed_error(exc):
                raise RuntimeError("backup artifact storage is unavailable") from exc
        existing = self._get_bounded_object(object_key, artifact_ref)
        return replay_backup_artifact_receipt(
            existing,
            artifact_ref,
            requested_hash=requested_hash,
            backup_id=report["backupId"],
        )

    def read_backup_artifact(
        self,
        artifact_ref: str,
        *,
        expected_hash: str | None = None,
    ) -> BackupRestoreArtifact:
        object_key = self._key_for_ref(artifact_ref)
        encoded = self._get_bounded_object(object_key, artifact_ref)
        return verify_backup_artifact(encoded, artifact_ref, expected_hash)

    def _get_bounded_object(self, object_key: str, artifact_ref: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self._config.bucket, Key=object_key)
            content_length = response.get("ContentLength")
            if isinstance(content_length, int) and content_length > self._config.max_artifact_bytes:
                raise BackupArtifactIntegrityError(artifact_ref, "artifact exceeds configured size limit")
            body = response["Body"].read(self._config.max_artifact_bytes + 1)
        except BackupArtifactIntegrityError:
            raise
        except Exception as exc:
            if is_not_found_error(exc):
                raise BackupArtifactNotFoundError(artifact_ref) from exc
            raise RuntimeError("backup artifact storage is unavailable") from exc
        if not isinstance(body, bytes) or len(body) > self._config.max_artifact_bytes:
            raise BackupArtifactIntegrityError(artifact_ref, "artifact exceeds configured size limit")
        return body

    def _key_for_report(self, report: BackupRestorePreflightReport) -> str:
        token = sha256(f"{report['tenantId']}:{report['backupId']}".encode()).hexdigest()
        return join_key(self._prefix, _safe_segment(report["tenantId"]), f"{token}.json")

    def _ref_for_key(self, object_key: str) -> str:
        return f"{_ARTIFACT_SCHEME}{self._config.bucket}/{object_key}"

    def _key_for_ref(self, artifact_ref: str) -> str:
        expected_prefix = f"{_ARTIFACT_SCHEME}{self._config.bucket}/"
        if not artifact_ref.startswith(expected_prefix):
            raise BackupArtifactIntegrityError(artifact_ref, "unsupported backup artifact ref")
        object_key = artifact_ref.removeprefix(expected_prefix)
        if not object_key.startswith(self._prefix + "/") or object_key.count("/") != self._prefix.count("/") + 2:
            raise BackupArtifactIntegrityError(artifact_ref, "backup artifact ref is outside the configured prefix")
        return object_key


def _safe_segment(value: str) -> str:
    cleaned = _SAFE_SEGMENT.sub("-", value).strip(".-/")
    if not cleaned:
        raise ValueError("backup artifact object key segment is empty")
    return cleaned[:120]
