from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO

from foundry_lite.application.ports.media_repository import MediaItemRecord, MediaItemVersionRecord
from foundry_lite.application.ports.media_storage import (
    CompleteMediaUpload,
    InitiateMediaUpload,
    StagedMediaObject,
    UploadSession,
)
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext


@dataclass(frozen=True)
class MediaUploadInput:
    """Per-upload inputs for staging one immutable version into an open transaction."""

    media_set_id: str
    media_transaction_id: str
    logical_path: str
    supplied_mime_type: str
    schema_type: str
    format: str
    security_envelope: dict[str, object]
    probe_metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class StagedUpload:
    """Outcome of staging one immutable version into an open media transaction."""

    media_item_id: str
    media_item_version_id: str
    version_number: int
    blob_key: str
    is_duplicate: bool


class MediaUploadService(CoreService):
    """Stage immutable media versions for an open transaction (doc §6.1 steps 1-6).

    Bytes land at one immutable object key; a metadata-pointer commit later makes the
    version reachable. ``complete`` is idempotent for one upload session: replaying it
    with the same object key resolves to the existing STAGED version, never a second one.
    """

    required_dependencies = ("engine", "media_repository", "media_storage")
    required_collaborators = ()

    def initiate(
        self, ctx: RequestContext, *, media_set_id: str, logical_path: str, supplied_mime_type: str
    ) -> UploadSession:
        return self.media_storage.initiate_upload(
            InitiateMediaUpload(
                tenant_id=ctx.tenant_id,
                media_set_id=media_set_id,
                logical_path=logical_path,
                supplied_mime_type=supplied_mime_type,
            )
        )

    def complete(
        self, ctx: RequestContext, *, inputs: MediaUploadInput, upload: UploadSession, source: BinaryIO
    ) -> StagedUpload:
        self.media_storage.write_staged(upload.staged_object_key, source)
        staged = self.media_storage.complete_staged_upload(
            CompleteMediaUpload(upload_id=upload.upload_id, staged_object_key=upload.staged_object_key)
        )
        now = _now()
        with self.engine.begin() as conn:
            item = self.media_repository.upsert_media_item(
                transaction=conn, record=_item_record(ctx, inputs.media_set_id, inputs.logical_path, now)
            )
            version_number = self.media_repository.next_version_number(
                transaction=conn, media_item_id=item.media_item_id
            )
            record = _staged_version_record(
                ctx,
                inputs,
                media_item_id=item.media_item_id,
                version_number=version_number,
                blob_key=upload.staged_object_key,
                staged=staged,
                created_at=now,
            )
            existing = self.media_repository.insert_version(transaction=conn, record=record)
        return _staged_upload(item.media_item_id, record, existing)


def _item_record(ctx: RequestContext, media_set_id: str, logical_path: str, now: str) -> MediaItemRecord:
    return MediaItemRecord(
        media_item_id=_new_id("mitem"),
        tenant_id=ctx.tenant_id,
        media_set_id=media_set_id,
        logical_path=logical_path,
        head_version_id=None,
        is_deleted=False,
        created_at=now,
        updated_at=now,
    )


def _staged_version_record(
    ctx: RequestContext,
    inputs: MediaUploadInput,
    *,
    media_item_id: str,
    version_number: int,
    blob_key: str,
    staged: StagedMediaObject,
    created_at: str,
) -> MediaItemVersionRecord:
    return MediaItemVersionRecord(
        media_item_version_id=_new_id("mver"),
        tenant_id=ctx.tenant_id,
        media_item_id=media_item_id,
        media_transaction_id=inputs.media_transaction_id,
        version_number=version_number,
        blob_key=blob_key,
        content_hash=staged.content_hash,
        byte_size=staged.byte_size,
        supplied_mime_type=inputs.supplied_mime_type,
        sniffed_mime_type=staged.sniffed_mime_type,
        schema_type=inputs.schema_type,
        format=inputs.format,
        probe_metadata=inputs.probe_metadata or {},
        security_envelope=inputs.security_envelope,
        source_ref=None,
        status="STAGED",
        created_at=created_at,
    )


def _staged_upload(
    media_item_id: str, record: MediaItemVersionRecord, existing: MediaItemVersionRecord | None
) -> StagedUpload:
    resolved = existing if existing is not None else record
    return StagedUpload(
        media_item_id=media_item_id,
        media_item_version_id=resolved.media_item_version_id,
        version_number=resolved.version_number,
        blob_key=resolved.blob_key,
        is_duplicate=existing is not None,
    )
