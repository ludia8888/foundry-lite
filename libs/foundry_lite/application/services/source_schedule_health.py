"""Derived health state for recurring managed Source schedules."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from foundry_lite.domain.errors import ValidationFailed

DEFAULT_AUTO_PAUSE_FAILURES = 3
MAX_AUTO_PAUSE_FAILURES = 10


@dataclass(frozen=True)
class SourceScheduleHealth:
    """Operator-visible failure streak derived from durable sync-run evidence."""

    consecutive_failure_count: int
    auto_pause_after_failures: int
    is_auto_paused: bool
    last_failure_run_id: str | None
    last_failure_at: str | None
    last_failure_error: Mapping[str, object] | None
    successful_recovery_run_id: str | None

    @property
    def should_auto_pause(self) -> bool:
        return self.consecutive_failure_count >= self.auto_pause_after_failures

    def as_dict(self) -> dict[str, object]:
        return {
            "consecutiveFailureCount": self.consecutive_failure_count,
            "autoPauseAfterFailures": self.auto_pause_after_failures,
            "autoPaused": self.is_auto_paused,
            "lastFailureRunId": self.last_failure_run_id,
            "lastFailureAt": self.last_failure_at,
            "lastFailureError": dict(self.last_failure_error) if self.last_failure_error else None,
        }


def source_schedule_health(sync: Mapping[str, object], runs: Sequence[Mapping[str, object]]) -> SourceScheduleHealth:
    """Project newest-first run history into a stable schedule health view."""
    threshold = auto_pause_failure_threshold(_mapping(sync.get("schedule")))
    failure_count, recovery_run_id = _scheduled_failure_streak(runs)
    last_failure = next((row for row in runs if row.get("status") == "failed"), None)
    return SourceScheduleHealth(
        consecutive_failure_count=failure_count,
        auto_pause_after_failures=threshold,
        is_auto_paused=str(sync.get("status")) == "paused" and failure_count >= threshold,
        last_failure_run_id=_text(last_failure, "id"),
        last_failure_at=_text(last_failure, "completed_at") or _text(last_failure, "created_at"),
        last_failure_error=_mapping(last_failure.get("error")) if last_failure else None,
        successful_recovery_run_id=recovery_run_id,
    )


def auto_pause_failure_threshold(schedule: Mapping[str, object]) -> int:
    raw = schedule.get("autoPauseAfterFailures", DEFAULT_AUTO_PAUSE_FAILURES)
    if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= MAX_AUTO_PAUSE_FAILURES:
        raise ValidationFailed(
            "autoPauseAfterFailures must be between 1 and 10",
            details={"field": "autoPauseAfterFailures", "value": raw},
        )
    return raw


def _scheduled_failure_streak(runs: Sequence[Mapping[str, object]]) -> tuple[int, str | None]:
    count = 0
    for row in runs:
        trigger_type = row.get("trigger_type")
        status = row.get("status")
        if trigger_type == "recovery" and status == "succeeded":
            return 0, _text(row, "id")
        if trigger_type != "scheduled" or status == "running":
            continue
        if status != "failed":
            break
        count += 1
    return count, None


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _text(row: Mapping[str, object] | None, key: str) -> str | None:
    value = row.get(key) if row else None
    return value if isinstance(value, str) and value else None
