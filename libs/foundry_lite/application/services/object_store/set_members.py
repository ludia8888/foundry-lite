"""Application service helpers for set members workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import ObjectRecordRow
from foundry_lite.application.services.object_store.row_policies import RowPolicyScope, row_visible
from foundry_lite.application.services.object_store.set_protocols import SetObjectQuery
from foundry_lite.application.services.object_store.set_semantics import ObjectSetMembers
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation

OBJECT_SET_MEMBER_PAGE_LIMIT = 500


def collect_static_object_set_members(
    object_query_service: SetObjectQuery,
    object_type_api_name: str,
    *,
    ctx: RequestContext,
    object_ids: Sequence[str],
    records: Mapping[str, ObjectRecordRow],
    scope: RowPolicyScope,
    include_items: bool,
) -> ObjectSetMembers:
    """Materialize static set members under the caller's row policy scope.

    When the type is row-policy protected, hidden rows drop from BOTH ids and
    items so a static set cannot leak the existence of rows its reader may not
    see; unrestricted types keep the historical behavior of returning every
    declared id (even ones whose records are currently missing).
    """
    visible_ids = [oid for oid in object_ids if oid in records and row_visible(scope, records[oid]["properties"])]
    member_ids = list(object_ids) if scope.is_unrestricted else visible_ids
    if not include_items:
        return member_ids, []
    items = [object_query_service._object_query_item(ctx, object_type_api_name, records[oid]) for oid in visible_ids]
    return member_ids, items


def collect_dynamic_object_set_members(
    object_query_service: SetObjectQuery,
    object_type_api_name: str,
    *,
    ctx: RequestContext,
    filter_ast: Mapping[str, object],
    include_items: bool,
) -> ObjectSetMembers:
    object_ids: list[str] = []
    items = []
    cursor: str | None = None
    while True:
        result = object_query_service.query_objects(
            object_type_api_name,
            ctx=ctx,
            filter_ast=filter_ast,
            limit=OBJECT_SET_MEMBER_PAGE_LIMIT,
            cursor=cursor,
        )
        object_ids.extend(item["objectId"] for item in result["items"])
        if include_items:
            items.extend(result["items"])
        next_cursor = result["nextCursor"]
        if next_cursor is None:
            return object_ids, items
        if next_cursor == cursor:
            raise InvariantViolation("object set member cursor did not advance")
        cursor = next_cursor
