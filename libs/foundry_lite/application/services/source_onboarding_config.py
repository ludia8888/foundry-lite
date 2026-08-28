"""Validation and staging helpers for source onboarding uploads."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import BinaryIO, cast

from foundry_lite.application.ports import SourceConnectionRecord
from foundry_lite.application.ports.source_registry_repository import SourceConnectionKind
from foundry_lite.application.ports.source_upload_staging_store import (
    SourceUploadStageRequest,
    SourceUploadStagingStore,
)
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
    storage_uri: str
    content_hash: str
    byte_size: int


@dataclass(frozen=True)
class StagedMediaSourceUpload:
    """A media upload copied to local staging with byte evidence."""

    file_name: str
    storage_uri: str
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


def copy_uploads(
    store: SourceUploadStagingStore, source_name: str, uploads: Sequence[SourceUpload]
) -> list[StagedSourceUpload]:
    """Stage all dataset uploads for a source connection."""

    require_identifier(source_name, "sourceName")
    for upload in uploads:
        require_text(upload.file_name, "fileName")
        _dataset_ref_parts(upload.dataset_ref)
    artifacts = store.stage_uploads(
        [SourceUploadStageRequest(source_name, upload.file_name, upload.source) for upload in uploads]
    )
    return [
        StagedSourceUpload(
            file_name=artifact.file_name,
            dataset_ref=upload.dataset_ref,
            storage_uri=artifact.storage_uri,
            content_hash=artifact.content_hash,
            byte_size=artifact.byte_size,
        )
        for upload, artifact in zip(uploads, artifacts, strict=True)
    ]


def copy_media_upload(
    store: SourceUploadStagingStore, source_name: str, file_name: str, source: BinaryIO
) -> StagedMediaSourceUpload:
    """Copy one media upload and compute its content hash."""

    require_identifier(source_name, "sourceName")
    require_text(file_name, "fileName")
    artifact = store.stage_uploads([SourceUploadStageRequest(source_name, file_name, source)])[0]
    return StagedMediaSourceUpload(
        file_name=artifact.file_name,
        storage_uri=artifact.storage_uri,
        content_hash=artifact.content_hash,
        byte_size=artifact.byte_size,
    )


def cleanup_uploads(store: SourceUploadStagingStore, staged: Sequence[StagedSourceUpload]) -> None:
    """Remove staged dataset uploads after commit or failure."""

    store.cleanup_uploads([upload.storage_uri for upload in staged])


def cleanup_media_upload(store: SourceUploadStagingStore, staged: StagedMediaSourceUpload) -> None:
    """Remove one staged media upload after commit or failure."""

    store.cleanup_uploads([staged.storage_uri])


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


def csv_upload_source_record(
    ctx: RequestContext,
    source_name: str,
    display_name: str,
    staged: Sequence[StagedSourceUpload],
    sync_name: str | None,
    primary_key: Sequence[str],
) -> SourceConnectionRecord:
    """Build a Source row for one staged CSV upload."""

    upload = staged[0]
    summary = upload_summary("csv_upload", staged, {"syncName": sync_name, "primaryKey": list(primary_key)})
    return source_record(
        ctx,
        source_name=source_name,
        display_name=display_name,
        kind="csv_upload",
        target_dataset_ref=upload.dataset_ref,
        target_media_set_id=None,
        config_summary=summary,
    )


def batch_upload_source_record(
    ctx: RequestContext,
    source_name: str,
    display_name: str,
    staged: Sequence[StagedSourceUpload],
    sync_name: str | None,
) -> SourceConnectionRecord:
    """Build a Source row for one staged batch upload."""

    dataset_refs = [upload.dataset_ref for upload in staged]
    summary = upload_summary("batch_file", staged, {"syncName": sync_name, "datasetRefs": dataset_refs})
    return source_record(
        ctx,
        source_name=source_name,
        display_name=display_name,
        kind="batch_file",
        target_dataset_ref=dataset_refs[0] if dataset_refs else None,
        target_media_set_id=None,
        config_summary=summary,
    )


def media_upload_source_record(
    ctx: RequestContext,
    source_name: str,
    display_name: str,
    media_set_id: str,
    logical_path: str,
    staged: StagedMediaSourceUpload,
) -> SourceConnectionRecord:
    """Build a Source row for one staged media upload."""

    summary = dict(
        mediaSetId=media_set_id,
        logicalPath=logical_path,
        contentHash=staged.content_hash,
        byteSize=staged.byte_size,
    )
    return source_record(
        ctx,
        source_name=source_name,
        display_name=display_name,
        kind="media_upload",
        target_dataset_ref=None,
        target_media_set_id=media_set_id,
        config_summary=summary,
    )


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
