"""Pure query helpers for virtual one-to-one Ontology Action Log objects."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from functools import cmp_to_key
from typing import cast

from foundry_lite.application.action_log_types import ACTION_LOG_PROPERTY_TYPES
from foundry_lite.application.ports import (
    ObjectOrderBy,
    ObjectQueryCursor,
    ObjectQueryItem,
    ObjectRecordRow,
    ObjectSortDirection,
)
from foundry_lite.application.query_filters import matches_filter, validate_filter_ast
from foundry_lite.application.services.object_store.query_cursor import (
    decode_object_query_cursor,
    encode_object_query_cursor,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed


def action_log_properties(payload: Mapping[str, object]) -> dict[str, object]:
    revert = _mapping(payload.get("revert"))
    raw_edited = payload.get("editedObjects")
    edited = list(raw_edited) if isinstance(raw_edited, list) else []
    return {
        "actionRunId": payload["actionRunId"],
        "logEntryId": payload["logEntryId"],
        "definitionVersion": payload["definitionVersion"],
        "actorUserId": payload["actorUserId"],
        "status": payload["status"],
        "parameters": payload["parameters"],
        "result": payload["result"],
        "branchId": payload.get("branchId"),
        "planHash": payload.get("planHash"),
        "approvalId": payload.get("approvalId"),
        "revertAllowed": revert.get("isAllowed", False),
        "revertStatus": revert.get("status"),
        "revertedByRunId": revert.get("revertedByRunId"),
        "effectReceiptCount": payload["effectReceiptCount"],
        "editedObjectCount": len(edited),
        "editedObjects": edited,
        "createdAt": payload["createdAt"],
        "completedAt": payload["completedAt"],
    }


def action_log_query_item(object_type: str, payload: Mapping[str, object]) -> ObjectQueryItem:
    properties = action_log_properties(payload)
    return {
        "objectType": object_type,
        "objectId": str(payload["actionRunId"]),
        "objectVersion": 2 if properties["revertedByRunId"] is not None else 1,
        "properties": properties,
    }


def normalized_log_order(order_by: Sequence[Mapping[str, str]] | None) -> list[ObjectOrderBy]:
    raw = list(order_by or ({"property": "createdAt", "direction": "desc"},))
    if len(raw) > 2:
        raise ValidationFailed("Action Log query supports at most two orderBy properties")
    normalized = [_log_order_item(item) for item in raw]
    if all(item["property"] != "actionRunId" for item in normalized):
        normalized.append({"property": "actionRunId", "direction": normalized[-1]["direction"]})
    return normalized


def decode_log_cursor(
    cursor: str | None,
    *,
    ctx: RequestContext,
    object_type: str,
    order_by: Sequence[ObjectOrderBy],
    filter_ast: Mapping[str, object] | None,
    search_text: str | None,
) -> ObjectQueryCursor | None:
    return decode_object_query_cursor(
        cursor,
        order_by,
        _cursor_shape(filter_ast, search_text),
        f"action-log:{object_type}",
        actor_user_id=ctx.actor_user_id,
        tenant_id=ctx.tenant_id,
    )


def next_log_cursor(
    page: Sequence[ObjectQueryItem],
    *,
    has_more: bool,
    ctx: RequestContext,
    object_type: str,
    order_by: Sequence[ObjectOrderBy],
    filter_ast: Mapping[str, object] | None,
    search_text: str | None,
) -> str | None:
    if not page or not has_more:
        return None
    last = page[-1]
    row = cast(ObjectRecordRow, {"object_id": last["objectId"], "properties": last["properties"]})
    return encode_object_query_cursor(
        row,
        order_by,
        _cursor_shape(filter_ast, search_text),
        f"action-log:{object_type}",
        actor_user_id=ctx.actor_user_id,
        tenant_id=ctx.tenant_id,
    )


def filtered_sorted_logs(
    items: Sequence[ObjectQueryItem],
    filter_ast: Mapping[str, object] | None,
    order_by: Sequence[ObjectOrderBy],
    search_text: str | None,
) -> list[ObjectQueryItem]:
    validate_log_filter(filter_ast)
    visible = [item for item in items if filter_ast is None or matches_filter(item["properties"], filter_ast)]
    if search_text:
        needle = search_text.casefold()
        visible = [item for item in visible if needle in json.dumps(item["properties"], default=str).casefold()]

    def compare(left: ObjectQueryItem, right: ObjectQueryItem) -> int:
        return _compare_items(left, right, order_by)

    return sorted(visible, key=cmp_to_key(compare))


def log_cursor_page(
    items: Sequence[ObjectQueryItem],
    *,
    ctx: RequestContext,
    object_type: str,
    order_by: Sequence[ObjectOrderBy],
    filter_ast: Mapping[str, object] | None,
    search_text: str | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[ObjectQueryItem], str | None]:
    shape = _cursor_shape(filter_ast, search_text)
    version = f"action-log:{object_type}"
    decoded = decode_object_query_cursor(
        cursor,
        order_by,
        shape,
        version,
        actor_user_id=ctx.actor_user_id,
        tenant_id=ctx.tenant_id,
    )
    start = _cursor_start(items, order_by, decoded)
    page = list(items[start : start + limit])
    return page, _next_cursor(items, page, start, order_by, shape, version, ctx)


def _cursor_start(
    items: Sequence[ObjectQueryItem],
    order_by: Sequence[ObjectOrderBy],
    cursor: Mapping[str, object] | None,
) -> int:
    if cursor is None:
        return 0
    for index, item in enumerate(items):
        if item["objectId"] != cursor.get("object_id"):
            continue
        values = [item["properties"].get(order["property"]) for order in order_by]
        if values != cursor.get("values"):
            raise ValidationFailed("Action Log query cursor row changed")
        return index + 1
    raise ValidationFailed("Action Log query cursor object was not found")


def _next_cursor(
    items: Sequence[ObjectQueryItem],
    page: Sequence[ObjectQueryItem],
    start: int,
    order_by: Sequence[ObjectOrderBy],
    shape: Mapping[str, object],
    version: str,
    ctx: RequestContext,
) -> str | None:
    if not page or start + len(page) >= len(items):
        return None
    last = page[-1]
    row = cast(ObjectRecordRow, {"object_id": last["objectId"], "properties": last["properties"]})
    return encode_object_query_cursor(
        row,
        order_by,
        shape,
        version,
        actor_user_id=ctx.actor_user_id,
        tenant_id=ctx.tenant_id,
    )


def validate_log_filter(filter_ast: Mapping[str, object] | None) -> None:
    if filter_ast is None:
        return
    validate_filter_ast(filter_ast)
    unknown = _filter_properties(filter_ast) - ACTION_LOG_PROPERTY_TYPES.keys()
    if unknown:
        raise ValidationFailed(
            "Action Log query references unknown properties",
            details={"properties": sorted(unknown)},
        )
    _validate_sql_filter_operations(filter_ast)


def validate_log_group_by(group_by: Sequence[str] | None) -> None:
    complex_names = [name for name in group_by or () if ACTION_LOG_PROPERTY_TYPES.get(name) in {"struct", "array"}]
    if complex_names:
        raise ValidationFailed(
            "Action Log aggregation cannot group by structured properties",
            details={"properties": complex_names},
        )


def _validate_sql_filter_operations(filter_ast: Mapping[str, object]) -> None:
    for logical in ("and", "or"):
        group = filter_ast.get(logical)
        if isinstance(group, Sequence) and not isinstance(group, str | bytes):
            for item in group:
                if isinstance(item, Mapping):
                    _validate_sql_filter_operations(item)
            return
    prop, operation = filter_ast.get("property"), filter_ast.get("op")
    if prop in {"parameters", "result", "editedObjects"} and operation != "contains":
        raise ValidationFailed(
            "Action Log structured properties support contains filters only",
            details={"property": prop, "operation": operation},
        )


def _filter_properties(filter_ast: Mapping[str, object]) -> set[str]:
    for logical in ("and", "or"):
        group = filter_ast.get(logical)
        if isinstance(group, Sequence) and not isinstance(group, str | bytes):
            return {name for item in group if isinstance(item, Mapping) for name in _filter_properties(item)}
    value = filter_ast.get("property")
    return {value} if isinstance(value, str) else set()


def _log_order_item(item: Mapping[str, str]) -> ObjectOrderBy:
    prop, direction = item.get("property"), item.get("direction", "asc")
    if prop not in ACTION_LOG_PROPERTY_TYPES:
        raise ValidationFailed("Action Log query orderBy property is unknown", details={"property": prop})
    if ACTION_LOG_PROPERTY_TYPES[prop] in {"struct", "array"}:
        raise ValidationFailed("Action Log query cannot order by a structured property", details={"property": prop})
    if direction not in {"asc", "desc"}:
        raise ValidationFailed("Action Log query orderBy direction must be asc or desc")
    return {"property": str(prop), "direction": cast(ObjectSortDirection, direction)}


def _compare_items(left: ObjectQueryItem, right: ObjectQueryItem, order_by: Sequence[ObjectOrderBy]) -> int:
    for order in order_by:
        compared = _compare_values(
            left["properties"].get(order["property"]),
            right["properties"].get(order["property"]),
        )
        if compared:
            return compared if order["direction"] == "asc" else -compared
    return (left["objectId"] > right["objectId"]) - (left["objectId"] < right["objectId"])


def _compare_values(left: object, right: object) -> int:
    if left is None or right is None:
        return (left is not None) - (right is not None)
    if isinstance(left, str) and isinstance(right, str):
        return (left > right) - (left < right)
    if (
        isinstance(left, (int, float))
        and not isinstance(left, bool)
        and isinstance(right, (int, float))
        and not isinstance(right, bool)
    ):
        return (left > right) - (left < right)
    left_text = json.dumps(left, sort_keys=True, default=str)
    right_text = json.dumps(right, sort_keys=True, default=str)
    return (left_text > right_text) - (left_text < right_text)


def _cursor_shape(filter_ast: Mapping[str, object] | None, search_text: str | None) -> Mapping[str, object]:
    return {
        "and": [
            filter_ast or {"op": "eq", "property": "__all__", "value": True},
            {"op": "eq", "property": "__search__", "value": search_text or ""},
        ]
    }


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}
