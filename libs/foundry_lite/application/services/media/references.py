"""Application service helpers for references workflows."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.ports.media_repository import MediaItemVersionRecord, MediaReference
from foundry_lite.application.ports.media_storage import ByteRange, MediaReadGrant, MediaReadGrantRequest
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.media.read_access import require_media_version_clearance
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation, NotFound, ValidationFailed


@dataclass(frozen=True)
class ResolvedMediaReference:
    """A reference resolved to its exact immutable version, verified against storage."""

    reference: MediaReference
    version: MediaItemVersionRecord


@dataclass(frozen=True)
class SourceMediaRead:
    """Verified immutable source bytes plus their scoped storage read grant."""

    resolved: ResolvedMediaReference
    grant: MediaReadGrant
    byte_range: ByteRange | None


class MediaReferenceService(CoreService):
    """Resolve a reference to its exact immutable version (doc §6.2 / §6.3).

    A reference pins ``media_item_version_id``, never the moving logical-path head, so a
    later overwrite never changes what an existing reference resolves to. The DB COMMITTED
    row is the serving truth: if the blob is missing or its stat/hash disagrees with the
    committed row, that is a hard failure, never a silent empty read.
    """

    required_dependencies = ("engine", "policy", "media_repository", "media_storage")
    required_collaborators = ()

    def resolve(self, ctx: RequestContext, *, media_item_version_id: str) -> ResolvedMediaReference:
        self.policy.require(ctx, "media:read")
        with self.engine.begin() as conn:
            version = self.media_repository.media_item_version_by_id(
                transaction=conn, tenant_id=ctx.tenant_id, media_item_version_id=media_item_version_id
            )
            if version is None:
                raise NotFound("media version not found", details={"media_item_version_id": media_item_version_id})
            if version.status != "COMMITTED":
                raise NotFound(
                    "media version is not committed",
                    details={"media_item_version_id": media_item_version_id, "status": version.status},
                )
            require_media_version_clearance(ctx, version)
            item = self.media_repository.media_item_by_id(
                transaction=conn, tenant_id=ctx.tenant_id, media_item_id=version.media_item_id
            )
        if item is None:
            raise NotFound("media item not found", details={"media_item_id": version.media_item_id})
        reference = MediaReference(
            media_set_id=item.media_set_id,
            media_item_id=version.media_item_id,
            media_item_version_id=version.media_item_version_id,
            logical_path=item.logical_path,
            content_hash=version.content_hash,
        )
        self._verify_blob(version)
        return ResolvedMediaReference(reference=reference, version=version)

    def source_read(
        self,
        ctx: RequestContext,
        *,
        media_item_version_id: str,
        byte_range: ByteRange | None = None,
        should_proxy: bool = False,
    ) -> SourceMediaRead:
        resolved = self.resolve(ctx, media_item_version_id=media_item_version_id)
        normalized_range = _normalized_byte_range(byte_range, resolved.version.byte_size)
        if should_proxy:
            grant = MediaReadGrant(
                grant_kind="inline",
                object_key=resolved.version.blob_key,
                stream=self.media_storage.open_stream(resolved.version.blob_key, normalized_range),
            )
        else:
            grant = self.media_storage.issue_read_grant(
                MediaReadGrantRequest(
                    tenant_id=ctx.tenant_id,
                    media_item_version_id=media_item_version_id,
                    object_key=resolved.version.blob_key,
                    byte_range=normalized_range,
                )
            )
        return SourceMediaRead(resolved, grant, normalized_range)

    def _verify_blob(self, version: MediaItemVersionRecord) -> None:
        stat = self.media_storage.stat(version.blob_key)
        if not stat.is_present:
            raise InvariantViolation(
                "committed_media_version_storage_missing",
                details={"media_item_version_id": version.media_item_version_id, "blob_key": version.blob_key},
            )
        if stat.content_hash != version.content_hash or stat.byte_size != version.byte_size:
            raise InvariantViolation(
                "committed_media_version_storage_corrupt",
                details={
                    "media_item_version_id": version.media_item_version_id,
                    "expected_hash": version.content_hash,
                    "actual_hash": stat.content_hash,
                },
            )


def _normalized_byte_range(byte_range: ByteRange | None, byte_size: int) -> ByteRange | None:
    if byte_range is None:
        return None
    if byte_range.start < 0 or byte_range.start >= byte_size:
        raise ValidationFailed(
            "media byte range start is outside the committed source",
            details={"start": byte_range.start, "byteSize": byte_size},
        )
    end = byte_size - 1 if byte_range.end is None else byte_range.end
    if end < byte_range.start or end >= byte_size:
        raise ValidationFailed(
            "media byte range end is outside the committed source",
            details={"start": byte_range.start, "end": end, "byteSize": byte_size},
        )
    return ByteRange(byte_range.start, end)
