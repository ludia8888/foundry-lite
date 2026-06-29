from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime


def seconds_between(started: datetime, finished: datetime) -> float:
    return max(0.0, raw_seconds_between(started, finished))


def raw_seconds_between(started: datetime, finished: datetime) -> float:
    return (finished - started).total_seconds()


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def parse_optional_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    return parse_time(value)


def skewed_measurement[T](measured: Sequence[tuple[T, float]]) -> tuple[T, float] | None:
    skewed = [(item, value) for item, value in measured if value < 0]
    return min(skewed, key=lambda item: item[1]) if skewed else None
