"""Result and failure-state projections for Pipeline scheduler ticks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from foundry_lite.application.ports.pipeline_repository import (
    PipelineRunRow,
    PipelineScheduleOperationRow,
    PipelineScheduleRow,
)
from foundry_lite.application.services.pipeline_execution_contracts import PipelineScheduleSpec
from foundry_lite.application.services.pipeline_payloads import schedule_payload
from foundry_lite.application.services.pipeline_schedule_runtime import (
    next_due_after_slot,
    pipeline_schedule_spec_from_row,
    resumed_next_due_at,
    schedule_iso,
)
from foundry_lite.domain.errors import ConflictDetected


@dataclass(frozen=True, slots=True)
class ScheduleOperationReservation:
    operation_id: str | None
    replay_result: dict[str, object] | None


def upsert_request(
    version_id: str,
    schedule: Mapping[str, object],
    is_enabled: bool,
) -> dict[str, object]:
    return {
        "versionId": version_id,
        "schedule": dict(schedule),
        "enabled": is_enabled,
    }


def replayed_operation(row: PipelineScheduleOperationRow, fingerprint: str) -> dict[str, object]:
    if row["request_fingerprint"] != fingerprint:
        raise ConflictDetected(
            "pipeline schedule idempotency key was reused with a different request",
            details={"idempotencyKey": row["idempotency_key"], "operation": row["operation"]},
        )
    if row["result"] is None:
        raise ConflictDetected("pipeline schedule operation is still in progress")
    return dict(row["result"])


def status_next_due(
    current: PipelineScheduleRow,
    target_status: str,
    now: datetime,
) -> str | None:
    if target_status == "paused":
        return current["next_due_at"]
    spec = pipeline_schedule_spec_from_row(current["schedule"], is_enabled=True)
    return resumed_next_due_at(spec, now)


def completion_values(
    claimed: PipelineScheduleRow,
    slot_start: str,
    run: Mapping[str, object] | None,
    error: Mapping[str, object] | None,
    now: datetime,
    is_enqueued: bool,
) -> dict[str, object]:
    spec = pipeline_schedule_spec_from_row(claimed["schedule"], is_enabled=True)
    failure_count = int(claimed["failure_count"]) if is_enqueued else int(claimed["failure_count"]) + 1
    should_pause = _should_auto_pause(spec, failure_count)
    return {
        "last_tick_at": schedule_iso(now),
        "last_slot_at": slot_start,
        "next_due_at": None if should_pause else next_due_after_slot(spec, slot_start),
        "failure_count": failure_count,
        "last_failure_at": claimed["last_failure_at"] if is_enqueued else schedule_iso(now),
        "last_error": claimed["last_error"] if is_enqueued else dict(error or _run_error(run)),
        "status": "paused" if should_pause else "active",
        "enabled": not should_pause,
        "paused_reason": "consecutive_failures" if should_pause else None,
    }


def terminal_observation_values(
    schedule: PipelineScheduleRow,
    run: PipelineRunRow,
    observed_at: str,
) -> dict[str, object]:
    status = str(run["status"])
    is_failure = status in {"failed", "partial"}
    failure_count = int(schedule["failure_count"]) + 1 if is_failure else 0
    spec = pipeline_schedule_spec_from_row(schedule["schedule"], is_enabled=True)
    should_pause = _should_auto_pause(spec, failure_count)
    values: dict[str, object] = {
        "failure_count": failure_count,
        "last_failure_at": observed_at if is_failure else None,
        "last_error": dict(run["error"] or _run_error(run)) if is_failure else None,
        "last_terminal_run_id": run["id"],
        "last_terminal_slot_at": run["schedule_slot_at"],
        "last_terminal_status": status,
        "last_terminal_at": observed_at,
    }
    if should_pause:
        values.update(
            status="paused",
            enabled=False,
            paused_reason="consecutive_failures",
            next_due_at=None,
        )
    return values


def tick_result(
    schedule: PipelineScheduleRow,
    slot_start: str,
    run: dict[str, object] | None,
    error: dict[str, object] | None,
) -> dict[str, object]:
    return {
        "schedule": schedule_payload(schedule),
        "slotStart": slot_start,
        "fencingToken": schedule["fencing_token"],
        "run": run,
        "error": error,
    }


def lease_lost_result(
    claimed: PipelineScheduleRow,
    slot_start: str,
    run: dict[str, object] | None,
) -> dict[str, object]:
    return {
        "scheduleId": claimed["id"],
        "slotStart": slot_start,
        "run": run,
        "reason": "lease_lost_before_completion",
        "fencingToken": claimed["fencing_token"],
    }


def _should_auto_pause(spec: PipelineScheduleSpec, failure_count: int) -> bool:
    threshold = spec.auto_pause_after_failures
    return threshold is not None and failure_count >= threshold


def _run_error(run: Mapping[str, object] | None) -> dict[str, object]:
    if run is None:
        return {"code": "PIPELINE_SCHEDULE_RUN_FAILED", "message": "scheduled run did not return evidence"}
    return {
        "code": "PIPELINE_SCHEDULE_RUN_FAILED",
        "message": "scheduled pipeline run did not succeed",
        "runId": run.get("id"),
        "status": run.get("status"),
    }
