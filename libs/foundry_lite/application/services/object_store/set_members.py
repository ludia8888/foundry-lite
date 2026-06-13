from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.services.object_store.set_protocols import SetObjectQuery
from foundry_lite.application.services.object_store.set_types import ObjectSetMembers
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation

OBJECT_SET_MEMBER_PAGE_LIMIT = 500


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
