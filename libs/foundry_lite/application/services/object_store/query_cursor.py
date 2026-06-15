from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import ObjectOrderBy, ObjectQueryCursor, ObjectRecordRow
from foundry_lite.application.primitives import _json_ready
from foundry_lite.domain.errors import ValidationFailed

CURSOR_PREFIX = "oqc1."
CURSOR_SIGNING_KEY_ENV = "FOUNDRY_LITE_OBJECT_QUERY_CURSOR_SIGNING_KEY"
DEFAULT_CURSOR_SIGNING_KEY = "foundry-lite-object-query-cursor-v1"


def encode_object_query_cursor(
    row: ObjectRecordRow,
    order_by: Sequence[ObjectOrderBy],
    filter_ast: Mapping[str, object] | None,
    active_index_version: str,
) -> str:
    payload = _signed_payload(
        {
            "activeIndexVersion": active_index_version,
            "order": _order_signature(order_by),
            "shape": _shape_checksum(order_by, filter_ast),
            "values": [_json_ready(row["properties"].get(order["property"])) for order in order_by],
            "objectId": row["object_id"],
        }
    )
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return CURSOR_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_object_query_cursor(
    cursor: str | None,
    order_by: Sequence[ObjectOrderBy],
    filter_ast: Mapping[str, object] | None,
    active_index_version: str,
) -> ObjectQueryCursor | None:
    if cursor is None:
        return None
    if not cursor.startswith(CURSOR_PREFIX):
        raise ValidationFailed("invalid object query cursor")
    payload = _cursor_payload(cursor)
    if payload.get("activeIndexVersion") != active_index_version:
        raise ValidationFailed(
            "object query cursor active index version changed",
            details={
                "cursorActiveIndexVersion": payload.get("activeIndexVersion"),
                "currentActiveIndexVersion": active_index_version,
            },
        )
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
    return {"values": values, "object_id": object_id, "active_index_version": active_index_version}


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
    normalized = {str(key): value for key, value in payload.items()}
    _require_valid_signature(normalized)
    return normalized


def _signed_payload(payload: Mapping[str, object]) -> dict[str, object]:
    signed = dict(payload)
    signed["signature"] = _payload_signature(signed)
    return signed


def _require_valid_signature(payload: Mapping[str, object]) -> None:
    provided = payload.get("signature")
    if not isinstance(provided, str):
        raise ValidationFailed("invalid object query cursor")
    expected = _payload_signature(payload)
    if not hmac.compare_digest(provided, expected):
        raise ValidationFailed("invalid object query cursor")


def _payload_signature(payload: Mapping[str, object]) -> str:
    raw = _canonical_payload(_unsigned_payload(payload)).encode("utf-8")
    signing_key = os.getenv(CURSOR_SIGNING_KEY_ENV, DEFAULT_CURSOR_SIGNING_KEY).encode("utf-8")
    return hmac.new(signing_key, raw, hashlib.sha256).hexdigest()[:32]


def _unsigned_payload(payload: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key != "signature"}


def _canonical_payload(payload: Mapping[str, object]) -> str:
    return json.dumps(_json_ready(payload), sort_keys=True, separators=(",", ":"))


def _order_signature(order_by: Sequence[ObjectOrderBy]) -> list[str]:
    return [f"{order['property']}:{order['direction']}" for order in order_by]


def _shape_checksum(order_by: Sequence[ObjectOrderBy], filter_ast: Mapping[str, object] | None) -> str:
    payload = json.dumps(
        {"filter": _json_ready(filter_ast or {}), "order": _order_signature(order_by)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
