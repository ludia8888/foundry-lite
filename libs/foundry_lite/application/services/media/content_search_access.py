"""Final object-inherited authorization for public media content search hits."""

from __future__ import annotations

from foundry_lite.application.ports.content_index import ContentSearchHit
from foundry_lite.application.ports.media_reference_binding_repository import MediaReferenceBindingRecord
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.media.attachment_access import (
    AttachmentObjectQuery,
    has_visible_attachment_holder,
)
from foundry_lite.domain.context import RequestContext


class MediaContentSearchAccessService(CoreService):
    """Drop attachment hits unless at least one holder object is visible."""

    required_dependencies = ("engine", "media_repository", "media_reference_binding_repository")
    required_collaborators = ("object_query_service",)
    object_query_service: AttachmentObjectQuery

    def filter_hits(self, ctx: RequestContext, hits: list[ContentSearchHit]) -> list[ContentSearchHit]:
        if not hits:
            return []
        version_ids = sorted({hit.source_media_item_version_id for hit in hits})
        with self.engine.begin() as transaction:
            versions = self.media_repository.get_media_item_versions(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                ids=version_ids,
            )
            bindings = self.media_reference_binding_repository.bindings_for_media_versions(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                media_item_version_ids=version_ids,
            )
        envelopes = {row.media_item_version_id: row.security_envelope for row in versions}
        grouped = _bindings_by_version(bindings)
        return [
            hit
            for hit in hits
            if hit.source_media_item_version_id in envelopes
            and has_visible_attachment_holder(
                ctx,
                envelopes[hit.source_media_item_version_id],
                grouped.get(hit.source_media_item_version_id, []),
                self.object_query_service,
            )
        ]


def _bindings_by_version(
    bindings: list[MediaReferenceBindingRecord],
) -> dict[str, list[MediaReferenceBindingRecord]]:
    grouped: dict[str, list[MediaReferenceBindingRecord]] = {}
    for binding in bindings:
        grouped.setdefault(binding.media_item_version_id, []).append(binding)
    return grouped
