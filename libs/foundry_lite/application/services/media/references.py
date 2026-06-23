from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.ports.media_repository import MediaItemVersionRecord, MediaReference
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation, NotFound


@dataclass(frozen=True)
class ResolvedMediaReference:
    """A reference resolved to its exact immutable version, verified against storage."""

    reference: MediaReference
    version: MediaItemVersionRecord


class MediaReferenceService(CoreService):
    """Resolve a reference to its exact immutable version (doc §6.2 / §6.3).

    A reference pins ``media_item_version_id``, never the moving logical-path head, so a
    later overwrite never changes what an existing reference resolves to. The DB COMMITTED
    row is the serving truth: if the blob is missing or its stat/hash disagrees with the
    committed row, that is a hard failure, never a silent empty read.
    """

    required_dependencies = ("engine", "media_repository", "media_storage")
    required_collaborators = ()

    def resolve(self, ctx: RequestContext, *, media_item_version_id: str) -> ResolvedMediaReference:
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
