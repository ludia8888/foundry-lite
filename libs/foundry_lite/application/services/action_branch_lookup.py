"""Object lookup adapter that composes main records with branch overlays."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from foundry_lite.application.action_branch_types import ActionBranchObjectRow
from foundry_lite.application.ports import ObjectRecordRow, TransactionContext
from foundry_lite.application.ports.action_branch_repository import ActionBranchRepository
from foundry_lite.application.services.action_protocols import ActionObjectRecordLookup
from foundry_lite.domain.context import RequestContext


@dataclass(frozen=True, slots=True)
class BranchObjectRecordLookup:
    base_lookup: ActionObjectRecordLookup
    repository: ActionBranchRepository
    branch_id: str

    def _object_record(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type_api_name: str,
        object_id: str,
        object_type_id: str | None = None,
    ) -> ObjectRecordRow | None:
        overlay = self.repository.object_overlay(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            branch_id=self.branch_id,
            object_type_api_name=object_type_api_name,
            object_id=object_id,
        )
        base = self.base_lookup._object_record(conn, ctx, object_type_api_name, object_id, object_type_id)
        return composed_branch_record(ctx, overlay, base)


def composed_branch_record(
    ctx: RequestContext,
    overlay: ActionBranchObjectRow | None,
    base: ObjectRecordRow | None,
    *,
    include_deleted: bool = False,
) -> ObjectRecordRow | None:
    if overlay is None:
        return base
    if overlay["deleted"] and not include_deleted:
        return None
    if base is not None:
        return _compose_existing(base, overlay)
    return _compose_created(ctx, overlay)


def _compose_existing(base: ObjectRecordRow, overlay: ActionBranchObjectRow) -> ObjectRecordRow:
    return cast(
        ObjectRecordRow,
        {
            **base,
            "properties": dict(overlay["properties"]),
            "edit_properties": dict(overlay["properties"]),
            "object_version": overlay["overlay_version"],
            "deleted": overlay["deleted"],
            "deletion_reason": "branch_overlay" if overlay["deleted"] else None,
            "updated_at": overlay["updated_at"],
        },
    )


def _compose_created(ctx: RequestContext, overlay: ActionBranchObjectRow) -> ObjectRecordRow:
    return cast(
        ObjectRecordRow,
        {
            "id": overlay["id"],
            "tenant_id": ctx.tenant_id,
            "object_type_id": overlay["object_type_id"],
            "object_type_api_name": overlay["object_type_api_name"],
            "object_id": overlay["object_id"],
            "index_version": f"branch:{overlay['branch_id']}",
            "is_active": True,
            "properties": dict(overlay["properties"]),
            "base_properties": {},
            "edit_properties": dict(overlay["properties"]),
            "property_versions": {},
            "source_dataset_version_id": None,
            "source_hash": None,
            "object_version": overlay["overlay_version"],
            "deleted": overlay["deleted"],
            "deletion_reason": "branch_overlay" if overlay["deleted"] else None,
            "created_at": overlay["created_at"],
            "updated_at": overlay["updated_at"],
        },
    )
