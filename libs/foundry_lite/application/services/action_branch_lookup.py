"""Object lookup adapter that composes main records with branch overlays."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from foundry_lite.application.action_branch_types import ActionBranchLinkRow, ActionBranchObjectRow
from foundry_lite.application.ports import ObjectLinkRow, ObjectRecordRow, TransactionContext
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


def branch_link_view(
    repository: ActionBranchRepository,
    transaction: TransactionContext,
    ctx: RequestContext,
    branch_id: str,
    link_type: str,
    from_object_id: str,
    to_object_id: str,
) -> dict[str, object] | None:
    overlay = repository.link_overlay(
        transaction=transaction,
        tenant_id=ctx.tenant_id,
        branch_id=branch_id,
        link_type_api_name=link_type,
        from_object_id=from_object_id,
        to_object_id=to_object_id,
    )
    base = _base_link(repository, transaction, ctx, link_type, from_object_id, to_object_id)
    if overlay is None and base is None:
        return None
    return _link_payload(branch_id, link_type, from_object_id, to_object_id, overlay, base)


def branch_link_diff_items(
    repository: ActionBranchRepository,
    transaction: TransactionContext,
    ctx: RequestContext,
    branch_id: str,
    overlays: list[ActionBranchLinkRow],
) -> list[dict[str, object]]:
    return [
        _link_payload(
            branch_id,
            overlay["link_type_api_name"],
            overlay["from_object_id"],
            overlay["to_object_id"],
            overlay,
            _base_link(
                repository,
                transaction,
                ctx,
                overlay["link_type_api_name"],
                overlay["from_object_id"],
                overlay["to_object_id"],
            ),
        )
        for overlay in overlays
    ]


def _base_link(
    repository: ActionBranchRepository,
    transaction: TransactionContext,
    ctx: RequestContext,
    link_type: str,
    from_object_id: str,
    to_object_id: str,
) -> ObjectLinkRow | None:
    return repository.active_base_link(
        transaction=transaction,
        tenant_id=ctx.tenant_id,
        link_type_api_name=link_type,
        from_object_id=from_object_id,
        to_object_id=to_object_id,
    )


def _link_payload(
    branch_id: str,
    link_type: str,
    from_object_id: str,
    to_object_id: str,
    overlay: ActionBranchLinkRow | None,
    base: ObjectLinkRow | None,
) -> dict[str, object]:
    identity = overlay if overlay is not None else base
    assert identity is not None
    base_version = overlay["base_link_version"] if overlay else (base["link_version"] if base else None)
    current_version = base["link_version"] if base else None
    return {
        "branchId": branch_id,
        "linkType": link_type,
        "fromApiName": identity["from_api_name"],
        "fromObjectId": from_object_id,
        "toApiName": identity["to_api_name"],
        "toObjectId": to_object_id,
        "linkVersion": overlay["overlay_version"] if overlay else current_version,
        "isDeleted": overlay["deleted"] if overlay else False,
        "baseLinkVersion": base_version,
        "currentMainVersion": current_version,
        "hasMainDrift": current_version != base_version,
        "lastActionRunId": overlay["last_action_run_id"] if overlay else None,
    }
