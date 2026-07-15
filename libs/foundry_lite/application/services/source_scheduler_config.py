"""Application service helpers for source scheduler config workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from foundry_lite.application.services.source_schedule_health import (
    SourceScheduleHealth,
    auto_pause_failure_threshold,
    source_schedule_health,
)
from foundry_lite.domain.errors import ValidationFailed

_SCHEDULER_PREFIX = "scheduler"
_MAX_CRON_LOOKBACK_MINUTES = 60 * 24 * 8


@dataclass(frozen=True)
class SourceScheduleDecision:
    sync_name: str
    is_enabled: bool
    is_due: bool
    reason: str
    slot_start: str | None
    next_due_at: str | None
    idempotency_key: str | None
    batch_limit: int | None
    health: SourceScheduleHealth | None = None

    def as_dict(self) -> dict[str, object]:
        health = self.health.as_dict() if self.health else source_schedule_health({}, []).as_dict()
        return {
            "syncName": self.sync_name,
            "enabled": self.is_enabled,
            "due": self.is_due,
            "reason": self.reason,
            "slotStart": self.slot_start,
            "nextDueAt": self.next_due_at,
            "idempotencyKey": self.idempotency_key,
            "batchLimit": self.batch_limit,
            **health,
        }


def normalized_source_schedule(schedule: Mapping[str, object]) -> dict[str, object]:
    """Validate and reduce a managed-sync schedule to its durable contract."""
    mode = str(schedule.get("mode") or "manual").strip().lower()
    if mode not in {"manual", "disabled", "interval", "cron"}:
        raise ValidationFailed("unsupported managed sync schedule mode", details={"mode": mode})
    normalized: dict[str, object] = {"mode": mode}
    if mode == "interval":
        normalized["everySeconds"] = _positive_int(
            schedule.get("everySeconds") or schedule.get("intervalSeconds"), "everySeconds"
        )
    if mode == "cron":
        expression = str(schedule.get("cron") or "").strip()
        _cron_fields(expression)
        normalized["cron"] = expression
    _copy_optional_schedule_fields(schedule, normalized)
    return normalized


def resumed_source_schedule(schedule: Mapping[str, object], resumed_at: str) -> dict[str, object]:
    """Reset the trigger anchor without discarding the recurring schedule."""
    resumed = dict(schedule)
    current = _parse_datetime(resumed_at)
    if resumed.get("mode") == "interval":
        seconds = _positive_int(resumed.get("everySeconds") or resumed.get("intervalSeconds"), "everySeconds")
        resumed["startAt"] = _iso(current + timedelta(seconds=seconds))
    if resumed.get("mode") == "cron":
        resumed["startAt"] = _iso(_minute_floor(current) + timedelta(minutes=1))
    return normalized_source_schedule(resumed)


def source_schedule_decision(
    sync: Mapping[str, object],
    runs: Sequence[Mapping[str, object]],
    *,
    now: datetime,
) -> SourceScheduleDecision:
    decision = _base_source_schedule_decision(sync, runs, now)
    return replace(decision, health=source_schedule_health(sync, runs))


def _base_source_schedule_decision(
    sync: Mapping[str, object], runs: Sequence[Mapping[str, object]], now: datetime
) -> SourceScheduleDecision:
    schedule = _mapping(sync.get("schedule"))
    sync_name = str(sync["sync_name"])
    status = str(sync.get("status") or "active")
    if status != "active":
        return _inactive(sync_name, status)
    mode = str(schedule.get("mode") or "manual")
    if mode in {"manual", "disabled"}:
        return _disabled(sync_name, mode)
    slot = _scheduled_slot(sync, schedule, now)
    if slot is None:
        return _not_due(sync, sync_name, schedule, now)
    if _has_active_run(runs):
        return _active_run(sync, sync_name, schedule, slot)
    idempotency_key = scheduler_idempotency_key(sync_name, slot)
    if _has_started_slot(runs, idempotency_key):
        return _already_started(sync_name, schedule, idempotency_key, slot)
    return SourceScheduleDecision(
        sync_name=sync_name,
        is_enabled=True,
        is_due=True,
        reason="due",
        slot_start=_iso(slot),
        next_due_at=_next_due(schedule, sync, slot),
        idempotency_key=idempotency_key,
        batch_limit=_optional_int(schedule.get("batchLimit")),
    )


def _copy_optional_schedule_fields(schedule: Mapping[str, object], normalized: dict[str, object]) -> None:
    if schedule.get("startAt") is not None:
        normalized["startAt"] = _iso(_parse_datetime(str(schedule["startAt"])))
    if schedule.get("batchLimit") is not None:
        normalized["batchLimit"] = _positive_int(schedule["batchLimit"], "batchLimit")
    if schedule.get("autoPauseAfterFailures") is not None:
        normalized["autoPauseAfterFailures"] = auto_pause_failure_threshold(schedule)


def scheduler_idempotency_key(sync_name: str, slot_start: datetime) -> str:
    return f"{_SCHEDULER_PREFIX}:{sync_name}:{_iso(slot_start)}"


def _disabled(sync_name: str, mode: str) -> SourceScheduleDecision:
    return SourceScheduleDecision(sync_name, False, False, f"{mode}_schedule", None, None, None, None)


def _inactive(sync_name: str, status: str) -> SourceScheduleDecision:
    reason = "schedule_paused" if status == "paused" else "sync_inactive"
    return SourceScheduleDecision(sync_name, False, False, reason, None, None, None, None)


def _not_due(
    sync: Mapping[str, object],
    sync_name: str,
    schedule: Mapping[str, object],
    now: datetime,
) -> SourceScheduleDecision:
    return SourceScheduleDecision(
        sync_name,
        True,
        False,
        "not_due",
        None,
        _pending_next_due(schedule, sync, now),
        None,
        _optional_int(schedule.get("batchLimit")),
    )


def _pending_next_due(schedule: Mapping[str, object], sync: Mapping[str, object], now: datetime) -> str | None:
    anchor = _anchor(schedule, sync)
    if now < anchor:
        if schedule.get("mode") == "interval":
            return _iso(anchor)
        return _next_cron_due(schedule, sync, _minute_floor(anchor) - timedelta(minutes=1))
    return _next_due(schedule, sync, _minute_floor(now))


def _already_started(
    sync_name: str,
    schedule: Mapping[str, object],
    idempotency_key: str,
    slot_start: datetime,
) -> SourceScheduleDecision:
    return SourceScheduleDecision(
        sync_name,
        True,
        False,
        "slot_already_started",
        _iso(slot_start),
        _next_due(schedule, {}, slot_start),
        idempotency_key,
        _optional_int(schedule.get("batchLimit")),
    )


def _active_run(
    sync: Mapping[str, object],
    sync_name: str,
    schedule: Mapping[str, object],
    slot_start: datetime,
) -> SourceScheduleDecision:
    return SourceScheduleDecision(
        sync_name,
        True,
        False,
        "active_run_in_progress",
        _iso(slot_start),
        _next_due(schedule, sync, slot_start),
        None,
        _optional_int(schedule.get("batchLimit")),
    )


def _scheduled_slot(sync: Mapping[str, object], schedule: Mapping[str, object], now: datetime) -> datetime | None:
    mode = str(schedule.get("mode") or "manual")
    if mode == "interval":
        return _interval_slot(sync, schedule, now)
    if mode == "cron":
        return _cron_slot(sync, schedule, now)
    raise ValidationFailed("unsupported managed sync schedule mode", details={"mode": mode})


def _interval_slot(sync: Mapping[str, object], schedule: Mapping[str, object], now: datetime) -> datetime | None:
    interval = _positive_int(schedule.get("everySeconds") or schedule.get("intervalSeconds"), "everySeconds")
    anchor = _anchor(schedule, sync)
    if now < anchor:
        return None
    elapsed = int((now - anchor).total_seconds())
    return anchor + timedelta(seconds=(elapsed // interval) * interval)


def _cron_slot(sync: Mapping[str, object], schedule: Mapping[str, object], now: datetime) -> datetime | None:
    expression = str(schedule.get("cron") or "").strip()
    if not expression:
        raise ValidationFailed("cron schedule requires cron", details={"field": "cron"})
    anchor = _anchor(schedule, sync)
    candidate = _minute_floor(now)
    fields = _cron_fields(expression)
    for _ in range(_MAX_CRON_LOOKBACK_MINUTES):
        if candidate < anchor:
            return None
        if _cron_matches(candidate, fields):
            return candidate
        candidate -= timedelta(minutes=1)
    raise ValidationFailed("cron schedule has no recent matching slot", details={"cron": expression})


def _next_due(schedule: Mapping[str, object], sync: Mapping[str, object], slot_start: datetime) -> str | None:
    mode = str(schedule.get("mode") or "manual")
    if mode == "interval":
        seconds = _positive_int(schedule.get("everySeconds") or schedule.get("intervalSeconds"), "everySeconds")
        return _iso(slot_start + timedelta(seconds=seconds))
    if mode == "cron":
        return _next_cron_due(schedule, sync, slot_start)
    return None


def _next_cron_due(schedule: Mapping[str, object], sync: Mapping[str, object], slot_start: datetime) -> str:
    fields = _cron_fields(str(schedule.get("cron") or ""))
    anchor = _cron_candidate_at_or_after(_anchor(schedule, sync))
    anchor = max(anchor, _minute_floor(slot_start) + timedelta(minutes=1))
    candidate = anchor
    for _ in range(_MAX_CRON_LOOKBACK_MINUTES):
        if _cron_matches(candidate, fields):
            return _iso(candidate)
        candidate += timedelta(minutes=1)
    raise ValidationFailed("cron schedule has no upcoming matching slot", details={"cron": schedule.get("cron")})


def _cron_candidate_at_or_after(value: datetime) -> datetime:
    candidate = _minute_floor(value)
    return candidate if candidate >= value else candidate + timedelta(minutes=1)


def _anchor(schedule: Mapping[str, object], sync: Mapping[str, object]) -> datetime:
    raw = schedule.get("startAt") or sync.get("created_at")
    return _parse_datetime(raw if isinstance(raw, str) else None)


def _has_started_slot(runs: Sequence[Mapping[str, object]], idempotency_key: str) -> bool:
    return any(row.get("idempotency_key") == idempotency_key for row in runs)


def _has_active_run(runs: Sequence[Mapping[str, object]]) -> bool:
    return any(row.get("status") == "running" for row in runs)


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationFailed("schedule datetime must be ISO-8601", details={"value": value}) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _minute_floor(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(second=0, microsecond=0)


def _positive_int(value: object, field: str) -> int:
    parsed = _optional_int(value)
    if parsed is None or parsed <= 0:
        raise ValidationFailed("schedule field must be a positive integer", details={"field": field})
    return parsed


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValidationFailed("schedule integer field cannot be boolean")
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        raise ValidationFailed("schedule field must be an integer", details={"value": value})
    try:
        return int(value)
    except ValueError as exc:
        raise ValidationFailed("schedule field must be an integer", details={"value": value}) from exc


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _cron_fields(expression: str) -> tuple[set[int], set[int], set[int], set[int], set[int]]:
    parts = expression.split()
    if len(parts) != 5:
        raise ValidationFailed("cron schedule must have five fields", details={"cron": expression})
    return (
        _cron_values(parts[0], 0, 59),
        _cron_values(parts[1], 0, 23),
        _cron_values(parts[2], 1, 31),
        _cron_values(parts[3], 1, 12),
        _cron_values(parts[4], 0, 7),
    )


def _cron_values(field: str, minimum: int, maximum: int) -> set[int]:
    values: set[int] = set()
    for part in field.split(","):
        values.update(_cron_part_values(part, minimum, maximum))
    return values


def _cron_part_values(part: str, minimum: int, maximum: int) -> set[int]:
    if part == "*":
        return set(range(minimum, maximum + 1))
    if part.startswith("*/"):
        step = _positive_int(part[2:], "cronStep")
        return set(range(minimum, maximum + 1, step))
    value = _integer(part, "cronValue")
    if value < minimum or value > maximum:
        raise ValidationFailed("cron value is outside allowed range", details={"value": value})
    return {value}


def _cron_matches(value: datetime, fields: tuple[set[int], set[int], set[int], set[int], set[int]]) -> bool:
    minutes, hours, days, months, weekdays = fields
    cron_weekday = (value.weekday() + 1) % 7
    return (
        value.minute in minutes
        and value.hour in hours
        and value.day in days
        and value.month in months
        and (cron_weekday in weekdays or (cron_weekday == 0 and 7 in weekdays))
    )


def _integer(value: object, field: str) -> int:
    parsed = _optional_int(value)
    if parsed is None:
        raise ValidationFailed("schedule field must be an integer", details={"field": field})
    return parsed
