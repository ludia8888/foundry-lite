"""Bounded recovery rules for idempotently replayed Pipeline runs."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from foundry_lite.domain.errors import InvariantViolation

_STALE_EXECUTION_AFTER = timedelta(hours=1)


class PipelineTerminalCommitError(RuntimeError):
    """Preserve atomic failure evidence instead of falling back to a split terminal write."""


def replayed_pipeline_run_action(row: Mapping[str, object]) -> str:
    if row.get("status") == "running":
        return "execute"
    return "fail_stale" if is_stale_pipeline_execution(row) else "read"


def stale_pipeline_run_error(row: Mapping[str, object]) -> InvariantViolation:
    return InvariantViolation(
        "stale pipeline execution was recovered as terminal failure",
        details={"run_id": row.get("id"), "started_at": row.get("started_at")},
    )


def is_stale_pipeline_execution(row: Mapping[str, object], *, now: datetime | None = None) -> bool:
    if row.get("status") != "executing":
        return False
    return (now or datetime.now(UTC)) - _execution_claimed_at(row) >= _STALE_EXECUTION_AFTER


def _execution_claimed_at(row: Mapping[str, object]) -> datetime:
    timeline = row.get("timeline")
    if isinstance(timeline, list):
        for item in reversed(timeline):
            if isinstance(item, Mapping) and item.get("event") == "pipeline.run.execution_claimed":
                return _timestamp(str(item.get("at", "")))
    return _timestamp(str(row.get("started_at", "")))


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
