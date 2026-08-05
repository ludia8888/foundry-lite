"""Branch-aware snapshot helpers for linked-object Action criteria."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass

from foundry_lite.application.action_branch_types import ActionBranchLinkRow, ActionBranchObjectRow
from foundry_lite.application.ports import (
    ObjectLinkRow,
    ObjectReadRepository,
    ObjectRecordRow,
    TransactionContext,
)
from foundry_lite.application.ports.action_branch_repository import ActionBranchRepository
from foundry_lite.application.services.action_branch_lookup import composed_branch_record
from foundry_lite.domain.action_runtime.action_conditions import LinkedObjectPropertyReference
from foundry_lite.domain.action_runtime.edit_plan import CriteriaReadExpectation
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected


@dataclass(frozen=True, slots=True)
class CriteriaLink:
    from_object_id: str
    to_object_id: str
    evidence: str
    is_active: bool = True


def base_criteria_link(row: ObjectLinkRow) -> CriteriaLink:
    return CriteriaLink(
        from_object_id=row["from_object_id"],
        to_object_id=row["to_object_id"],
        evidence=f"main:{row['id']}:{row['link_version']}",
    )


def branch_link_overlays(
    transaction: TransactionContext,
    ctx: RequestContext,
    target: ObjectRecordRow,
    reference: LinkedObjectPropertyReference,
    repository: ActionBranchRepository | None,
    branch_id: str | None,
    limit: int,
) -> list[ActionBranchLinkRow]:
    if repository is None or branch_id is None:
        return []
    if reference.direction == "outgoing":
        return repository.link_overlays_from(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            branch_id=branch_id,
            link_type_api_name=reference.link_type,
            from_api_name=target["object_type_api_name"],
            from_object_id=target["object_id"],
            limit=limit,
        )
    return repository.link_overlays_to(
        transaction=transaction,
        tenant_id=ctx.tenant_id,
        branch_id=branch_id,
        link_type_api_name=reference.link_type,
        to_api_name=target["object_type_api_name"],
        to_object_id=target["object_id"],
        limit=limit,
    )


def merge_link_overlays(base: Sequence[CriteriaLink], overlays: Sequence[ActionBranchLinkRow]) -> list[CriteriaLink]:
    merged = {(row.from_object_id, row.to_object_id): row for row in base}
    for overlay in overlays:
        key = (overlay["from_object_id"], overlay["to_object_id"])
        merged[key] = CriteriaLink(
            from_object_id=key[0],
            to_object_id=key[1],
            evidence=(
                f"branch:{overlay['id']}:{overlay['base_link_version']}:"
                f"{overlay['overlay_version']}:{int(overlay['deleted'])}"
            ),
            is_active=not overlay["deleted"],
        )
    return [merged[key] for key in sorted(merged)]


def compose_branch_records(
    transaction: TransactionContext,
    ctx: RequestContext,
    object_type: str,
    object_ids: Sequence[str],
    base_records: Sequence[ObjectRecordRow],
    repository: ActionBranchRepository | None,
    branch_id: str | None,
) -> list[ObjectRecordRow]:
    if repository is None or branch_id is None:
        return list(base_records)
    overlays = repository.object_overlays_for_ids(
        transaction=transaction,
        tenant_id=ctx.tenant_id,
        branch_id=branch_id,
        object_type_api_name=object_type,
        object_ids=object_ids,
    )
    return _composed_records(ctx, object_ids, base_records, overlays)


def _composed_records(
    ctx: RequestContext,
    object_ids: Sequence[str],
    base_records: Sequence[ObjectRecordRow],
    overlays: Sequence[ActionBranchObjectRow],
) -> list[ObjectRecordRow]:
    base_by_id = {row["object_id"]: row for row in base_records}
    overlay_by_id = {row["object_id"]: row for row in overlays}
    records = [
        composed_branch_record(ctx, overlay_by_id.get(object_id), base_by_id.get(object_id), include_deleted=True)
        for object_id in dict.fromkeys(object_ids)
    ]
    return [record for record in records if record is not None]


def criteria_snapshot_fingerprint(links: Sequence[CriteriaLink], records: Sequence[ObjectRecordRow]) -> str:
    payload = {
        "links": sorted(link.evidence for link in links),
        "records": sorted(
            (record["object_type_api_name"], record["object_id"], record["object_version"], record["deleted"])
            for record in records
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def criteria_target(
    transaction: TransactionContext,
    ctx: RequestContext,
    repository: ObjectReadRepository,
    expectation: CriteriaReadExpectation,
    branch_repository: ActionBranchRepository | None,
    branch_id: str | None,
) -> ObjectRecordRow:
    base = repository.object_record(
        transaction=transaction,
        tenant_id=ctx.tenant_id,
        object_type_api_name=expectation.anchor_object_type,
        object_id=expectation.anchor_object_id,
    )
    if branch_repository is not None and branch_id is not None:
        overlay = branch_repository.object_overlay(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            branch_id=branch_id,
            object_type_api_name=expectation.anchor_object_type,
            object_id=expectation.anchor_object_id,
        )
        base = composed_branch_record(ctx, overlay, base)
    if base is None:
        raise ConflictDetected("submission-criteria target changed after planning")
    return base
