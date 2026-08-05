"""Cursor and public projections for normalized Action logs."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping

from foundry_lite.application.action_log_types import ActionLogEntryRow, ActionLogObjectRow
from foundry_lite.domain.errors import ValidationFailed

ACTION_LOG_OBJECT_TYPE_PREFIX = "[LOG] "
ACTION_LOG_EDITED_OBJECT_LINK_PREFIX = "[LOG LINK] "


def action_log_object_type_api_name(action_api_name: str) -> str:
    """Return the one-to-one Ontology Action Log object type name."""
    return f"{ACTION_LOG_OBJECT_TYPE_PREFIX}{action_api_name}"


def action_api_name_from_log_object_type(object_type_api_name: str) -> str | None:
    """Resolve a virtual Action Log object type back to its Action API name."""
    if not object_type_api_name.startswith(ACTION_LOG_OBJECT_TYPE_PREFIX):
        return None
    action_api_name = object_type_api_name.removeprefix(ACTION_LOG_OBJECT_TYPE_PREFIX).strip()
    return action_api_name or None


def action_log_edited_object_link_type_api_name(action_api_name: str, object_type_api_name: str) -> str:
    """Return the virtual link from one Action Log type to one edited Object Type."""
    return f"{ACTION_LOG_EDITED_OBJECT_LINK_PREFIX}{action_api_name}::{object_type_api_name}"


def action_and_object_from_log_link(link_type_api_name: str) -> tuple[str, str] | None:
    """Resolve a virtual Action Log link name into its action and object coordinates."""
    if not link_type_api_name.startswith(ACTION_LOG_EDITED_OBJECT_LINK_PREFIX):
        return None
    coordinate = link_type_api_name.removeprefix(ACTION_LOG_EDITED_OBJECT_LINK_PREFIX)
    action_api_name, separator, object_type_api_name = coordinate.partition("::")
    if separator != "::" or not action_api_name.strip() or not object_type_api_name.strip():
        return None
    return action_api_name.strip(), object_type_api_name.strip()


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
