from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import ObjectOrderBy, ObjectQueryCursor, ObjectRecordRow
from foundry_lite.application.primitives import _json_ready
from foundry_lite.domain.errors import ValidationFailed

CURSOR_PREFIX = "oqc1."


def encode_object_query_cursor(
    row: ObjectRecordRow,
    order_by: Sequence[ObjectOrderBy],
    filter_ast: Mapping[str, object] | None,
) -> str:
    payload = {
        "order": _order_signature(order_by),
        "shape": _shape_checksum(order_by, filter_ast),
        "values": [_json_ready(row["properties"].get(order["property"])) for order in order_by],
        "objectId": row["object_id"],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return CURSOR_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_object_query_cursor(
    cursor: str | None,
    order_by: Sequence[ObjectOrderBy],
    filter_ast: Mapping[str, object] | None,
) -> ObjectQueryCursor | None:
    if cursor is None:
        return None
    if not cursor.startswith(CURSOR_PREFIX):
        return _legacy_cursor(cursor, order_by, filter_ast)
    payload = _cursor_payload(cursor)
    if payload.get("order") != _order_signature(order_by):
        raise ValidationFailed("object query cursor does not match orderBy")
    if payload.get("shape") != _shape_checksum(order_by, filter_ast):
        raise ValidationFailed("object query cursor does not match query shape")
    values = payload.get("values")
    object_id = payload.get("objectId")
    if not isinstance(values, list) or not isinstance(object_id, str):
        raise ValidationFailed("invalid object query cursor")
    if len(values) != len(order_by):
        raise ValidationFailed("object query cursor does not match orderBy")
    return {"values": values, "object_id": object_id}


def _legacy_cursor(
    cursor: str,
    order_by: Sequence[ObjectOrderBy],
    filter_ast: Mapping[str, object] | None,
) -> ObjectQueryCursor:
    if order_by or filter_ast:
        raise ValidationFailed("object query cursor does not match orderBy")
    return {"values": [], "object_id": cursor}


def _cursor_payload(cursor: str) -> dict[str, object]:
    encoded = cursor.removeprefix(CURSOR_PREFIX)
    padded = encoded + "=" * (-len(encoded) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationFailed("invalid object query cursor") from exc
    if not isinstance(payload, dict):
        raise ValidationFailed("invalid object query cursor")
    return {str(key): value for key, value in payload.items()}


def _order_signature(order_by: Sequence[ObjectOrderBy]) -> list[str]:
    return [f"{order['property']}:{order['direction']}" for order in order_by]


def _shape_checksum(order_by: Sequence[ObjectOrderBy], filter_ast: Mapping[str, object] | None) -> str:
    payload = json.dumps(
        {"filter": _json_ready(filter_ast or {}), "order": _order_signature(order_by)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
