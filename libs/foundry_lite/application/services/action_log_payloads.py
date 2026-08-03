"""Cursor and public projections for normalized Action logs."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping

from foundry_lite.application.action_log_types import ActionLogEntryRow, ActionLogObjectRow
from foundry_lite.domain.errors import ValidationFailed


def action_log_payload(
    row: ActionLogEntryRow,
    objects: list[ActionLogObjectRow],
    parameters: Mapping[str, object],
    effect_count: int,
) -> dict[str, object]:
    """Build the stable public Action log representation."""
    return {
        "logEntryId": row["id"],
        "logObject": {"objectType": row["log_object_type_api_name"], "objectId": row["log_object_id"]},
        "actionRunId": row["action_run_id"],
        "actionApiName": row["action_type_api_name"],
        "definitionVersion": row["definition_version"],
        "actorUserId": row["actor_user_id"],
        "status": row["status"],
        "parameters": dict(parameters),
        "result": dict(row["result"]),
        "branchId": row["branch_id"],
        "planHash": row["plan_hash"],
        "approvalId": row["approval_id"],
        "revert": {
            "isAllowed": row["revert_allowed"],
            "status": row["revert_status"],
            "revertedByRunId": row["reverted_by_run_id"],
        },
        "effectReceiptCount": effect_count,
        "editedObjects": [_object_payload(item) for item in objects],
        "createdAt": row["created_at"],
        "completedAt": row["completed_at"],
    }


def decode_action_log_cursor(cursor: str | None) -> tuple[str | None, str | None]:
    """Decode a stable newest-first Action log cursor."""
    if cursor is None:
        return None, None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        return str(payload["createdAt"]), str(payload["logEntryId"])
    except (KeyError, TypeError, ValueError, UnicodeDecodeError) as exc:
        raise ValidationFailed("Action log cursor is invalid") from exc


def next_action_log_cursor(rows: list[ActionLogEntryRow], limit: int) -> str | None:
    """Encode the last visible row when another Action log page exists."""
    if len(rows) <= limit:
        return None
    last = rows[limit - 1]
    raw = json.dumps({"createdAt": last["created_at"], "logEntryId": last["id"]}).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _object_payload(row: ActionLogObjectRow) -> dict[str, object]:
    return {
        "objectEditId": row["object_edit_id"],
        "objectType": row["object_type_api_name"],
        "objectId": row["object_id"],
        "operation": row["edit_type"],
        "ordinal": row["ordinal"],
    }
