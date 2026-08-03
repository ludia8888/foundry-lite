"""Public projections and cursors for durable Action runs."""

from __future__ import annotations

import base64
import json

from foundry_lite.application.action_async_execution_types import (
    ActionAsyncRunRow,
    ActionRunEventRow,
    ActionRunStepRow,
    ActionStepAttemptRow,
)
from foundry_lite.domain.errors import ValidationFailed


def action_run_snapshot(
    row: ActionAsyncRunRow,
    steps: list[ActionRunStepRow],
    attempts: list[ActionStepAttemptRow],
) -> dict[str, object]:
    return {
        "actionRunId": row["id"],
        "actionApiName": row["action_type_api_name"],
        "status": row["status"],
        "target": {"objectType": row["target_object_type_api_name"], "objectId": row["target_object_id"]},
        "expectedObjectVersion": row["expected_object_version"],
        "parameters": row["parameters"],
        "planHash": row["plan_hash"],
        "definitionVersion": row["definition_version"],
        "orchestration": {
            "workflowRunId": row["workflow_run_id"],
            "dispatchStatus": row["dispatch_status"],
            "dispatchAttempts": row["dispatch_attempt_count"],
            "dispatchError": row["dispatch_error"],
        },
        "steps": [_step_payload(step) for step in steps],
        "attempts": [_attempt_payload(attempt) for attempt in attempts],
        "cancel": {
            "requestedAt": row["cancel_requested_at"],
            "reason": row["cancel_reason"],
        },
        "result": row["result"],
        "error": row["error"],
        "eventSequence": row["event_sequence"],
        "createdAt": row["created_at"],
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
    }


def action_event_payload(row: ActionRunEventRow) -> dict[str, object]:
    return {
        "id": row["sequence"],
        "event": row["event_type"],
        "data": row["payload"],
        "stepKey": row["step_key"],
        "attemptNumber": row["attempt_number"],
        "workerId": row["worker_id"],
        "fencingToken": row["fencing_token"],
        "createdAt": row["created_at"],
    }


def decode_action_run_cursor(cursor: str | None) -> tuple[str | None, str | None]:
    if cursor is None:
        return None, None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        return str(payload["createdAt"]), str(payload["runId"])
    except (KeyError, TypeError, ValueError, UnicodeDecodeError) as exc:
        raise ValidationFailed("Action run cursor is invalid") from exc


def next_action_run_cursor(rows: list[ActionAsyncRunRow], limit: int) -> str | None:
    if len(rows) <= limit:
        return None
    last = rows[limit - 1]
    raw = json.dumps({"createdAt": last["created_at"], "runId": last["id"]}).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _step_payload(row: ActionRunStepRow) -> dict[str, object]:
    return {
        "stepKey": row["step_key"],
        "kind": row["step_kind"],
        "status": row["status"],
        "attemptCount": row["attempt_count"],
        "output": row["output_manifest"],
        "error": row["error"],
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
    }


def _attempt_payload(row: ActionStepAttemptRow) -> dict[str, object]:
    return {
        "stepId": row["step_id"],
        "attemptNumber": row["attempt_number"],
        "status": row["status"],
        "workerId": row["worker_id"],
        "fencingToken": row["fencing_token"],
        "heartbeatAt": row["heartbeat_at"],
        "retryAt": row["retry_at"],
        "errorKind": row["error_kind"],
        "externalExecutionId": row["external_execution_id"],
        "output": row["output_manifest"],
        "error": row["error"],
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
    }
