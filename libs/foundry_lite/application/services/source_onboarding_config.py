"""Validation and staging helpers for source onboarding uploads."""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, cast

from foundry_lite.application.ports import SourceConnectionRecord
from foundry_lite.application.ports.source_registry_repository import SourceConnectionKind
from foundry_lite.application.primitives import SQL_IDENTIFIER_PATTERN, _dataset_ref_parts, _json_hash, _new_id, _now
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed


@dataclass(frozen=True)
class SourceUpload:
    """One uploaded file and its target dataset reference."""

    file_name: str
    dataset_ref: str
    source: BinaryIO


@dataclass(frozen=True)
class StagedSourceUpload:
    """A dataset upload copied to local staging with byte evidence."""

    file_name: str
    dataset_ref: str
    path: Path
    content_hash: str
    byte_size: int


@dataclass(frozen=True)
class StagedMediaSourceUpload:
    """A media upload copied to local staging with byte evidence."""

    file_name: str
    path: Path
    content_hash: str
    byte_size: int


RAW_SECRET_FIELDS = frozenset({"token", "password", "headerValue", "apiKey", "clientSecret", "privateKey"})


def source_record(
    ctx: RequestContext,
    *,
    source_name: str,
    display_name: str,
    kind: SourceConnectionKind,
    target_dataset_ref: str | None,
    target_media_set_id: str | None,
    config_summary: Mapping[str, object],
) -> SourceConnectionRecord:
    """Build the tenant-scoped source registry row from validated inputs."""

    normalized = source_config(
        source_name=source_name,
        display_name=display_name,
        kind=kind,
        target_dataset_ref=target_dataset_ref,
        target_media_set_id=target_media_set_id,
        config_summary=config_summary,
    )
    created_at = _now()
    return SourceConnectionRecord(
        source_id=_new_id("source"),
        tenant_id=ctx.tenant_id,
        source_name=str(normalized["sourceName"]),
        display_name=str(normalized["displayName"]),
        kind=cast(SourceConnectionKind, normalized["kind"]),
        target_dataset_ref=cast(str | None, normalized["targetDatasetRef"]),
        target_media_set_id=cast(str | None, normalized["targetMediaSetId"]),
        status="active",
        config_summary=cast(Mapping[str, object], normalized["configSummary"]),
        config_fingerprint=source_fingerprint(normalized),
        last_run_id=None,
        last_workflow_run_id=None,
        last_commit_ref=None,
        created_at=created_at,
        updated_at=created_at,
    )


def source_config(
    *,
    source_name: str,
    display_name: str,
    kind: SourceConnectionKind,
    target_dataset_ref: str | None,
    target_media_set_id: str | None,
    config_summary: Mapping[str, object],
) -> dict[str, object]:
    """Normalize source configuration before fingerprinting or persistence."""

    require_identifier(source_name, "sourceName")
    require_text(display_name, "displayName")
    if target_dataset_ref is not None:
        _dataset_ref_parts(target_dataset_ref)
    reject_raw_secret_payload(config_summary)
    return {
        "sourceName": source_name,
        "displayName": display_name,
        "kind": kind,
        "targetDatasetRef": target_dataset_ref,
        "targetMediaSetId": target_media_set_id,
        "configSummary": dict(config_summary),
    }


def copy_uploads(root: Path, source_name: str, uploads: Sequence[SourceUpload]) -> list[StagedSourceUpload]:
    """Stage all dataset uploads for a source connection."""

    staged_root = root / "source-uploads" / source_name
    staged_root.mkdir(parents=True, exist_ok=True)
    return [copy_upload(staged_root, upload) for upload in uploads]


def copy_upload(staged_root: Path, upload: SourceUpload) -> StagedSourceUpload:
    """Copy one dataset upload and compute its content hash."""

    require_text(upload.file_name, "fileName")
    _dataset_ref_parts(upload.dataset_ref)
    safe_name = Path(upload.file_name).name
    target = staged_root / f"{_new_id('upload')}-{safe_name}"
    digest = hashlib.sha256()
    byte_size = 0
    with target.open("wb") as output:
        for chunk in iter(lambda: upload.source.read(1024 * 1024), b""):
            byte_size += len(chunk)
            digest.update(chunk)
            output.write(chunk)
    return StagedSourceUpload(
        file_name=safe_name,
        dataset_ref=upload.dataset_ref,
        path=target,
        content_hash=f"sha256:{digest.hexdigest()}",
        byte_size=byte_size,
    )


def copy_media_upload(root: Path, source_name: str, file_name: str, source: BinaryIO) -> StagedMediaSourceUpload:
    """Copy one media upload and compute its content hash."""

    require_text(file_name, "fileName")
    staged_root = root / "source-uploads" / source_name
    staged_root.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file_name).name
    target = staged_root / f"{_new_id('media_upload')}-{safe_name}"
    digest = hashlib.sha256()
    byte_size = 0
    with target.open("wb") as output:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            byte_size += len(chunk)
            digest.update(chunk)
            output.write(chunk)
    return StagedMediaSourceUpload(
        file_name=safe_name,
        path=target,
        content_hash=f"sha256:{digest.hexdigest()}",
        byte_size=byte_size,
    )


def cleanup_uploads(staged: Sequence[StagedSourceUpload]) -> None:
    """Remove staged dataset uploads after commit or failure."""

    for upload in staged:
        upload.path.unlink(missing_ok=True)
    for parent in {upload.path.parent for upload in staged}:
        shutil.rmtree(parent, ignore_errors=True)


def cleanup_media_upload(staged: StagedMediaSourceUpload) -> None:
    """Remove one staged media upload after commit or failure."""

    staged.path.unlink(missing_ok=True)
    shutil.rmtree(staged.path.parent, ignore_errors=True)


def upload_summary(kind: str, staged: Sequence[StagedSourceUpload], extra: Mapping[str, object]) -> dict[str, object]:
    """Return raw-value-free upload evidence for operator surfaces."""

    return {
        "kind": kind,
        "files": [
            {
                "fileName": upload.file_name,
                "datasetRef": upload.dataset_ref,
                "contentHash": upload.content_hash,
                "byteSize": upload.byte_size,
            }
            for upload in staged
        ],
        **dict(extra),
    }


def source_fingerprint(value: Mapping[str, object]) -> str:
    return f"sha256:{_json_hash(value)}"


def require_identifier(value: str, field: str) -> None:
    if SQL_IDENTIFIER_PATTERN.fullmatch(value):
        return
    raise ValidationFailed(f"{field} must be a safe identifier", details={field: value})


def require_text(value: str, field: str) -> None:
    if value.strip():
        return
    raise ValidationFailed(f"{field} is required", details={"field": field})


def require_idempotency_key(value: str) -> None:
    if value.strip():
        return
    raise ValidationFailed("Idempotency-Key is required")


def reject_raw_secret_payload(payload: Mapping[str, object]) -> None:
    for key, value in payload.items():
        if key in RAW_SECRET_FIELDS:
            raise ValidationFailed("source onboarding accepts secretRef only", details={key: "***REDACTED***"})
        if isinstance(value, Mapping):
            reject_raw_secret_payload(value)
        elif isinstance(value, list):
            reject_raw_secret_list(value)


def reject_raw_secret_list(values: Sequence[object]) -> None:
    for value in values:
        if isinstance(value, Mapping):
            reject_raw_secret_payload(value)
        elif isinstance(value, list):
            reject_raw_secret_list(value)
