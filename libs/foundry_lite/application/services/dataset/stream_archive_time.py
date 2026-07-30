"""Application service helpers for stream archive time workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import TypeGuard
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from foundry_lite.application.ports import LateDataStatus, StreamArchiveConfig
from foundry_lite.domain.errors import ValidationFailed


def payload_named_time(payload: Mapping[str, object], field: str, stream: StreamArchiveConfig) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValidationFailed("stream archive time field must be ISO-8601 text", details={"field": field})
    parsed, is_naive = _parse_stream_datetime(value, field, stream)
    if is_naive and stream.time_zone is not None:
        return parsed.isoformat()
    return value


def late_data_status(
    event_time: str | None,
    processed_at: str,
    previous_metadata: Mapping[str, object] | None,
    stream: StreamArchiveConfig,
) -> LateDataStatus:
    if event_time is None:
        return "ON_TIME"
    event_at = parse_iso_datetime(event_time, stream.event_time_field)
    processed = parse_iso_datetime(processed_at, "processed_at")
    lag_seconds = (processed - event_at).total_seconds()
    if lag_seconds < -stream.clock_skew_seconds:
        return "FUTURE_CLOCK_SKEW"
    if lag_seconds > stream.too_late_after_seconds:
        return "TOO_LATE"
    previous_watermark = previous_watermark_datetime(previous_metadata, stream)
    if previous_watermark is not None and event_at < previous_watermark:
        return "LATE_REQUIRES_REPROCESS"
    if lag_seconds > stream.allowed_lateness_seconds:
        return "LATE_REQUIRES_REPROCESS"
    if lag_seconds > stream.clock_skew_seconds:
        return "LATE_ACCEPTED"
    return "ON_TIME"


def event_time_lag_seconds(event_time: str | None, processed_at: str) -> int | None:
    if event_time is None:
        return None
    event_at = parse_iso_datetime(event_time, "event_time")
    processed = parse_iso_datetime(processed_at, "processed_at")
    return int((processed - event_at).total_seconds())


def next_late_data_watermark(
    previous_metadata: Mapping[str, object] | None,
    rows: Sequence[Mapping[str, object]],
    stream: StreamArchiveConfig,
) -> datetime | None:
    previous = previous_watermark_datetime(previous_metadata, stream)
    current = max(_row_event_datetimes(rows), default=None)
    if previous is None:
        return current
    if current is None or current < previous:
        return previous
    return current


def late_data_watermark_metadata(
    stream: StreamArchiveConfig,
    watermark: datetime,
    rows: Sequence[Mapping[str, object]],
) -> Mapping[str, object]:
    current = max(_row_event_datetimes(rows), default=None)
    return {
        "streamName": stream.stream_name,
        "topic": stream.topic,
        "partition": stream.partition,
        "consumerGroup": stream.consumer_group,
        "eventTimeField": stream.event_time_field,
        "sourceTimeField": stream.source_time_field,
        "timeZone": stream.time_zone,
        "watermarkEventTime": watermark.isoformat(),
        "maxAcceptedEventTime": current.isoformat() if current is not None else None,
        "allowedLatenessSeconds": stream.allowed_lateness_seconds,
        "tooLateAfterSeconds": stream.too_late_after_seconds,
    }


def stream_watermark_key(stream: StreamArchiveConfig) -> str:
    return f"{stream.topic}:{stream.consumer_group}:{stream.partition}"


def previous_stream_watermarks(metadata: Mapping[str, object] | None) -> dict[str, object]:
    """Return the per-stream watermark map, absorbing any legacy single-slot entry.

    A dataset can be fed by several stream partitions, so watermarks — like
    cursors — must be kept per stream. Older transactions stored the watermark in
    a single ``lateDataWatermark`` slot; fold that into the keyed map so its
    stream's watermark survives a commit from a different partition.
    """
    if not isinstance(metadata, Mapping):
        return {}
    raw = metadata.get("lateDataWatermarks")
    watermarks = dict(raw) if isinstance(raw, Mapping) else {}
    legacy = metadata.get("lateDataWatermark")
    if isinstance(legacy, Mapping):
        key = _watermark_key_from_payload(legacy)
        if key is not None:
            watermarks.setdefault(key, legacy)
    return watermarks


def previous_watermark_datetime(metadata: Mapping[str, object] | None, stream: StreamArchiveConfig) -> datetime | None:
    if metadata is None:
        return None
    entry = _stream_watermark_entry(metadata, stream)
    if entry is None:
        return None
    value = entry.get("watermarkEventTime")
    return parse_iso_datetime(value, "watermarkEventTime") if isinstance(value, str) else None


def _stream_watermark_entry(metadata: Mapping[str, object], stream: StreamArchiveConfig) -> Mapping[str, object] | None:
    watermarks = metadata.get("lateDataWatermarks")
    if isinstance(watermarks, Mapping):
        candidate = watermarks.get(stream_watermark_key(stream))
        if _watermark_entry_matches(candidate, stream):
            return candidate
    legacy = metadata.get("lateDataWatermark")
    if _watermark_entry_matches(legacy, stream):
        return legacy
    return None


def _watermark_entry_matches(entry: object, stream: StreamArchiveConfig) -> TypeGuard[Mapping[str, object]]:
    return (
        isinstance(entry, Mapping)
        and entry.get("streamName") == stream.stream_name
        and entry.get("topic") == stream.topic
        and entry.get("partition") == stream.partition
        and entry.get("consumerGroup") == stream.consumer_group
    )


def _watermark_key_from_payload(payload: Mapping[str, object]) -> str | None:
    topic = payload.get("topic")
    group = payload.get("consumerGroup")
    partition = payload.get("partition")
    if not isinstance(topic, str) or not isinstance(group, str):
        return None
    if not isinstance(partition, int) or isinstance(partition, bool):
        return None
    return f"{topic}:{group}:{partition}"


def parse_iso_datetime(value: str, field: str) -> datetime:
    parsed, _ = _parse_stream_datetime(value, field, None)
    return parsed


def validate_late_data_policy(stream: StreamArchiveConfig) -> None:
    if not stream.event_time_field:
        raise ValidationFailed("stream archive event time field must be configured")
    if not stream.source_time_field:
        raise ValidationFailed("stream archive source time field must be configured")
    if stream.time_zone is not None:
        _source_zone(stream.time_zone)
    if stream.allowed_lateness_seconds < 0 or stream.too_late_after_seconds < 0 or stream.clock_skew_seconds < 0:
        raise ValidationFailed("stream archive late-data durations must be non-negative")
    if stream.too_late_after_seconds < stream.allowed_lateness_seconds:
        raise ValidationFailed("stream archive too-late threshold must be at least allowed lateness")


def _row_event_datetimes(rows: Sequence[Mapping[str, object]]) -> list[datetime]:
    values: list[datetime] = []
    for row in rows:
        value = row.get("event_time")
        if isinstance(value, str) and value:
            values.append(parse_iso_datetime(value, "event_time"))
    return values


def _parse_stream_datetime(
    value: str,
    field: str,
    stream: StreamArchiveConfig | None,
) -> tuple[datetime, bool]:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationFailed("stream archive time field must be ISO-8601 text", details={"field": field}) from exc
    if parsed.tzinfo is None:
        source_zone = _source_zone(stream.time_zone if stream is not None else None)
        return parsed.replace(tzinfo=source_zone).astimezone(UTC), True
    return parsed.astimezone(UTC), False


def _source_zone(time_zone: str | None) -> ZoneInfo:
    if time_zone is None:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(time_zone)
    except ZoneInfoNotFoundError as exc:
        raise ValidationFailed(
            "stream archive time zone must be a named IANA zone",
            details={"time_zone": time_zone},
        ) from exc
