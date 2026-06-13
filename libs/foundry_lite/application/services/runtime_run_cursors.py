from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping

from foundry_lite.application.ports import RuntimeRow, RuntimeRunPageCursor, RuntimeRunType
from foundry_lite.domain.errors import ValidationFailed

OPERATIONS_CURSOR_PREFIX = "orc1."


def encode_runtime_run_cursor(
    row: RuntimeRow,
    *,
    run_type: RuntimeRunType,
    status: str | None,
    since: str | None,
    until: str | None,
) -> str:
    payload = {
        "runType": run_type,
        "shape": _shape_checksum(run_type=run_type, status=status, since=since, until=until),
        "timestamp": _row_timestamp(row),
        "runId": row["id"],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"{OPERATIONS_CURSOR_PREFIX}{base64.urlsafe_b64encode(raw).decode().rstrip('=')}"


def decode_runtime_run_cursor(
    cursor: str | None,
    *,
    run_type: RuntimeRunType,
    status: str | None,
    since: str | None,
    until: str | None,
) -> RuntimeRunPageCursor | None:
    if cursor is None or cursor == "":
        return None
    if not cursor.startswith(OPERATIONS_CURSOR_PREFIX):
        raise ValidationFailed("invalid operations cursor")
    payload = _cursor_payload(cursor)
    if payload.get("runType") != run_type:
        raise ValidationFailed("operations cursor does not match run type")
    expected_shape = _shape_checksum(run_type=run_type, status=status, since=since, until=until)
    if payload.get("shape") != expected_shape:
        raise ValidationFailed("operations cursor does not match query shape")
    timestamp = payload.get("timestamp")
    run_id = payload.get("runId")
    if not isinstance(timestamp, str) or not isinstance(run_id, str) or not timestamp or not run_id:
        raise ValidationFailed("invalid operations cursor payload")
    return {"timestamp": timestamp, "run_id": run_id}


def _cursor_payload(cursor: str) -> Mapping[str, object]:
    encoded = cursor.removeprefix(OPERATIONS_CURSOR_PREFIX)
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
    except Exception as exc:
        raise ValidationFailed("invalid operations cursor") from exc
    if not isinstance(payload, dict):
        raise ValidationFailed("invalid operations cursor payload")
    return payload


def _shape_checksum(*, run_type: RuntimeRunType, status: str | None, since: str | None, until: str | None) -> str:
    shape = {"runType": run_type, "status": status or "", "since": since or "", "until": until or ""}
    raw = json.dumps(shape, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _row_timestamp(row: RuntimeRow) -> str:
    for key in ("created_at", "failed_at", "completed_at", "published_at", "updated_at"):
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    raise ValidationFailed("operations run row cannot be paged without timestamp")
