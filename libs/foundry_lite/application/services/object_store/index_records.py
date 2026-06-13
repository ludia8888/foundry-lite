from __future__ import annotations

from foundry_lite.application.ports import LinkTypeRow, ObjectLinkInsert
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.domain.context import RequestContext


def build_object_link_insert(
    ctx: RequestContext,
    link: LinkTypeRow,
    from_id: str,
    to_id: str,
    source_dataset_version_id: str,
) -> ObjectLinkInsert:
    return ObjectLinkInsert(
        link_id=_new_id("olink"),
        tenant_id=ctx.tenant_id,
        link_type_id=link["id"],
        link_type_api_name=link["api_name"],
        from_object_type_id=link["from_object_type_id"],
        from_api_name=link["from_api_name"],
        from_object_id=from_id,
        to_object_type_id=link["to_object_type_id"],
        to_api_name=link["to_api_name"],
        to_object_id=to_id,
        properties={},
        source_dataset_version_id=source_dataset_version_id,
        link_version=1,
        deleted=False,
        deletion_reason=None,
        updated_at=_now(),
    )
