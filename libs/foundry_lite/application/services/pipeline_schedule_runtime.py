"""Typed cron and interval timing rules for durable Pipeline schedules."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from foundry_lite.application.services.pipeline_execution_contracts import PipelineScheduleSpec
from foundry_lite.domain.errors import ValidationFailed

_MAX_CRON_LOOKAHEAD_MINUTES = 60 * 24 * 366 * 5
_MAX_INTERVAL_SECONDS = 60 * 60 * 24 * 366 * 5
_LEGACY_TICK_INTERVAL_SECONDS = 60


@dataclass(frozen=True, slots=True)
class CronField:
    values: frozenset[int]
    is_star_based: bool


def normalize_pipeline_schedule(
    schedule: Mapping[str, object],
    *,
    is_enabled: bool,
    now: datetime,
) -> PipelineScheduleSpec:
    trigger_kind = _trigger_kind(schedule)
    if trigger_kind not in {"cron", "interval"}:
        raise ValidationFailed(
            "bounded pipeline scheduler supports cron and interval triggers",
            details={"triggerType": trigger_kind, "unsupportedCurrentTriggers": ["data", "logic"]},
        )
    timezone = str(schedule.get("timezone") or "UTC").strip()
    _timezone(timezone)
    return _time_schedule_spec(schedule, trigger_kind, timezone, is_enabled, now)


def normalize_legacy_pipeline_schedule(
    schedule: Mapping[str, object],
    *,
    is_enabled: bool,
    now: datetime,
) -> tuple[PipelineScheduleSpec, bool]:
    try:
        return normalize_pipeline_schedule(schedule, is_enabled=is_enabled, now=now), False
    except ValidationFailed:
        fallback = {
            "triggerType": "interval",
            "timezone": "UTC",
            "intervalSeconds": _LEGACY_TICK_INTERVAL_SECONDS,
        }
        return normalize_pipeline_schedule(fallback, is_enabled=is_enabled, now=now), True


def schedule_spec_payload(spec: PipelineScheduleSpec) -> dict[str, object]:
    payload: dict[str, object] = {
        "triggerType": spec.trigger_kind,
        "timezone": spec.timezone,
    }
    if spec.cron_expression is not None:
        payload["cronExpression"] = spec.cron_expression
    if spec.interval_seconds is not None:
        payload["intervalSeconds"] = spec.interval_seconds
    if spec.start_at is not None:
        payload["startAt"] = spec.start_at
    if spec.auto_pause_after_failures is not None:
        payload["autoPauseAfterFailures"] = spec.auto_pause_after_failures
    return payload


def initial_next_due_at(spec: PipelineScheduleSpec, now: datetime) -> str | None:
    if not spec.is_enabled:
        return None
    anchor = _start_anchor(spec, now)
    if spec.trigger_kind == "interval":
        return _iso(_first_interval_slot(anchor, now, _required_interval(spec)))
    first_candidate = max(anchor, _utc(now))
    search_after = (
        first_candidate - timedelta(minutes=1) if first_candidate == _minute_floor(first_candidate) else first_candidate
    )
    return _iso(_next_cron_match(spec, search_after))


def resumed_next_due_at(spec: PipelineScheduleSpec, now: datetime) -> str:
    if spec.trigger_kind == "interval":
        return _iso(_utc(now) + timedelta(seconds=_required_interval(spec)))
    return _iso(_next_cron_match(spec, _utc(now)))


def next_due_after_slot(spec: PipelineScheduleSpec, slot_start: str) -> str:
    slot = parse_schedule_timestamp(slot_start, field="slotStart")
    if spec.trigger_kind == "interval":
        return _iso(slot + timedelta(seconds=_required_interval(spec)))
    return _iso(_next_cron_match(spec, slot))


def pipeline_schedule_spec_from_row(schedule: Mapping[str, object], *, is_enabled: bool) -> PipelineScheduleSpec:
    return normalize_pipeline_schedule(schedule, is_enabled=is_enabled, now=datetime.now(UTC))


def scheduled_run_idempotency_key(schedule_id: str, slot_start: str) -> str:
    return f"pipeline-schedule:{schedule_id}:{slot_start}"


def schedule_operation_fingerprint(
    pipeline_id: str,
    operation: str,
    payload: Mapping[str, object],
) -> str:
    body = {"operation": operation, "pipelineId": pipeline_id, "payload": dict(payload)}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parse_schedule_timestamp(value: str, *, field: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValidationFailed("pipeline schedule timestamp is invalid", details={"field": field}) from exc
    if parsed.tzinfo is None:
        raise ValidationFailed("pipeline schedule timestamp requires timezone", details={"field": field})
    return parsed.astimezone(UTC)


def scheduler_now(value: datetime | None = None) -> datetime:
    return _utc(value or datetime.now(UTC)).replace(microsecond=0)


def schedule_iso(value: datetime) -> str:
    return _iso(value)


def _time_schedule_spec(
    schedule: Mapping[str, object],
    trigger_kind: str,
    timezone: str,
    is_enabled: bool,
    now: datetime,
) -> PipelineScheduleSpec:
    start_at = _optional_start_at(schedule, now)
    auto_pause = _optional_positive_int(schedule.get("autoPauseAfterFailures"), "autoPauseAfterFailures")
    if trigger_kind == "cron":
        expression = str(schedule.get("cronExpression") or schedule.get("cron") or "").strip()
        _cron_fields(expression)
        return PipelineScheduleSpec(
            trigger_kind="cron",
            timezone=timezone,
            cron_expression=expression,
            start_at=start_at,
            auto_pause_after_failures=auto_pause,
            is_enabled=is_enabled,
        )
    return PipelineScheduleSpec(
        trigger_kind="interval",
        timezone=timezone,
        interval_seconds=_interval_seconds(schedule),
        start_at=start_at,
        auto_pause_after_failures=auto_pause,
        is_enabled=is_enabled,
    )


def _trigger_kind(schedule: Mapping[str, object]) -> str:
    return str(schedule.get("triggerType") or schedule.get("type") or schedule.get("kind") or "").strip().lower()


def _interval_seconds(schedule: Mapping[str, object]) -> int:
    seconds = schedule.get("intervalSeconds")
    if seconds is None:
        seconds = schedule.get("everySeconds")
    if seconds is not None:
        return _bounded_interval_seconds(_positive_int(seconds, "intervalSeconds"))
    minutes = schedule.get("intervalMinutes")
    if minutes is None:
        minutes = schedule.get("everyMinutes")
    if minutes is None:
        raise ValidationFailed("interval pipeline schedule requires intervalSeconds")
    return _bounded_interval_seconds(_positive_int(minutes, "intervalMinutes") * 60)


def _optional_start_at(schedule: Mapping[str, object], now: datetime) -> str:
    value = schedule.get("startAt")
    if value is None:
        return _iso(_utc(now))
    return _iso(parse_schedule_timestamp(str(value), field="startAt"))


def _start_anchor(spec: PipelineScheduleSpec, now: datetime) -> datetime:
    if spec.start_at is None:
        return _utc(now)
    return parse_schedule_timestamp(spec.start_at, field="startAt")


def _required_interval(spec: PipelineScheduleSpec) -> int:
    if spec.interval_seconds is None:
        raise ValidationFailed("interval pipeline schedule requires intervalSeconds")
    return spec.interval_seconds


def _first_interval_slot(anchor: datetime, now: datetime, seconds: int) -> datetime:
    current = _utc(now)
    if anchor >= current:
        return anchor
    elapsed_seconds = (current - anchor).total_seconds()
    elapsed_slots = int(elapsed_seconds // seconds)
    candidate = anchor + timedelta(seconds=elapsed_slots * seconds)
    return candidate if candidate >= current else candidate + timedelta(seconds=seconds)


def _next_cron_match(spec: PipelineScheduleSpec, after: datetime) -> datetime:
    fields = _cron_fields(spec.cron_expression or "")
    zone = _timezone(spec.timezone)
    candidate = _minute_floor(after) + timedelta(minutes=1)
    for _ in range(_MAX_CRON_LOOKAHEAD_MINUTES):
        if _cron_matches(candidate.astimezone(zone), fields):
            return candidate
        candidate += timedelta(minutes=1)
    raise ValidationFailed("cron pipeline schedule has no match within five years")


def _cron_fields(expression: str) -> tuple[CronField, CronField, CronField, CronField, CronField]:
    parts = expression.split()
    if len(parts) != 5:
        raise ValidationFailed("cron pipeline schedule must have five fields", details={"cronExpression": expression})
    return (
        _cron_field(parts[0], 0, 59),
        _cron_field(parts[1], 0, 23),
        _cron_field(parts[2], 1, 31),
        _cron_field(parts[3], 1, 12),
        _cron_field(parts[4], 0, 7),
    )


def _cron_field(value: str, minimum: int, maximum: int) -> CronField:
    values: set[int] = set()
    for part in value.split(","):
        values.update(_cron_part_values(part, minimum, maximum))
    return CronField(frozenset(values), value.startswith("*"))


def _cron_part_values(part: str, minimum: int, maximum: int) -> set[int]:
    base, separator, step_text = part.partition("/")
    step = _positive_int(step_text, "cronStep") if separator else 1
    if separator and base != "*" and "-" not in base:
        start = _bounded_int(base, minimum, maximum)
        return set(range(start, maximum + 1, step))
    start, end = _cron_range(base, minimum, maximum)
    return set(range(start, end + 1, step))


def _cron_range(value: str, minimum: int, maximum: int) -> tuple[int, int]:
    if value == "*":
        return minimum, maximum
    if "-" in value:
        start_text, end_text = value.split("-", 1)
        start = _bounded_int(start_text, minimum, maximum)
        end = _bounded_int(end_text, minimum, maximum)
        if end < start:
            raise ValidationFailed("cron range end precedes start", details={"value": value})
        return start, end
    item = _bounded_int(value, minimum, maximum)
    return item, item


def _cron_matches(
    value: datetime,
    fields: tuple[CronField, CronField, CronField, CronField, CronField],
) -> bool:
    minute, hour, day, month, weekday = fields
    base_matches = value.minute in minute.values and value.hour in hour.values and value.month in month.values
    if not base_matches:
        return False
    is_day_match = value.day in day.values
    cron_weekday = (value.weekday() + 1) % 7
    is_weekday_match = cron_weekday in weekday.values or (cron_weekday == 0 and 7 in weekday.values)
    return _day_matches(day, weekday, is_day_match, is_weekday_match)


def _day_matches(day: CronField, weekday: CronField, is_day_match: bool, is_weekday_match: bool) -> bool:
    if not day.is_star_based and not weekday.is_star_based:
        return is_day_match or is_weekday_match
    return is_day_match and is_weekday_match


def _timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValidationFailed("pipeline schedule timezone is unknown", details={"timezone": value}) from exc


def _positive_int(value: object, field: str) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ValidationFailed("pipeline schedule field must be an integer", details={"field": field}) from exc
    if parsed < 1:
        raise ValidationFailed("pipeline schedule field must be positive", details={"field": field})
    return parsed


def _optional_positive_int(value: object, field: str) -> int | None:
    return None if value is None else _positive_int(value, field)


def _bounded_interval_seconds(seconds: int) -> int:
    if seconds > _MAX_INTERVAL_SECONDS:
        raise ValidationFailed(
            "pipeline schedule interval exceeds the five-year bound",
            details={"intervalSeconds": seconds, "maxIntervalSeconds": _MAX_INTERVAL_SECONDS},
        )
    return seconds


def _bounded_int(value: str, minimum: int, maximum: int) -> int:
    parsed = _positive_or_zero_int(value, "cronValue")
    if parsed < minimum or parsed > maximum:
        raise ValidationFailed("cron value is outside allowed range", details={"value": parsed})
    return parsed


def _positive_or_zero_int(value: object, field: str) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ValidationFailed("pipeline schedule field must be an integer", details={"field": field}) from exc
    if parsed < 0:
        raise ValidationFailed("pipeline schedule field cannot be negative", details={"field": field})
    return parsed


def _minute_floor(value: datetime) -> datetime:
    return _utc(value).replace(second=0, microsecond=0)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValidationFailed("pipeline scheduler datetime requires timezone")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")
