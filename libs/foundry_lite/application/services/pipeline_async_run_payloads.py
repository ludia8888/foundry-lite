"""Public payload and cursor helpers for asynchronous Pipeline runs."""

from __future__ import annotations

import base64
import json

from foundry_lite.application.ports.pipeline_execution_repository import PipelineRunEventRow
from foundry_lite.application.ports.pipeline_repository import PipelineRunRow
from foundry_lite.domain.errors import ValidationFailed

JsonObject = dict[str, object]


def event_payload(row: PipelineRunEventRow) -> JsonObject:
    """Project one durable event without exposing private lease tokens."""

    return {
        "id": row["sequence"],
        "event": row["event_type"],
        "data": row["payload"],
        "nodeId": row["node_id"],
        "attemptNumber": row["attempt_number"],
        "workerId": row["worker_id"],
        "fencingToken": row["fencing_token"],
        "createdAt": row["created_at"],
    }


def decode_run_cursor(cursor: str | None) -> tuple[str | None, str | None]:
    """Decode the stable started-at and run-id pagination cursor."""

    if cursor is None:
        return None, None
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
        return str(payload["startedAt"]), str(payload["runId"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationFailed("pipeline run cursor is invalid") from exc


def next_run_cursor(rows: list[PipelineRunRow], limit: int) -> str | None:
    """Return a cursor only when another run-history page exists."""

    if len(rows) <= limit:
        return None
    last = rows[limit - 1]
    payload = json.dumps({"startedAt": last["started_at"], "runId": last["id"]}).encode()
    return base64.urlsafe_b64encode(payload).decode("ascii")
