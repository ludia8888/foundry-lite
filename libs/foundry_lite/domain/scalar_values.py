"""Canonical validation for scalar values crossing Ontology runtime boundaries."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from math import isfinite

_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_TIMESTAMP_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z")
DECIMAL_NUMBER_SQL_PATTERN = r"^[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?$"
INTEGER_SQL_PATTERN = r"^[+-]?[0-9]+$"
_DECIMAL_NUMBER_PATTERN = re.compile(DECIMAL_NUMBER_SQL_PATTERN)
_INTEGER_PATTERN = re.compile(INTEGER_SQL_PATTERN)
_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
INTEGER_MIN_VALUE = -(2**31)
INTEGER_MAX_VALUE = 2**31 - 1
LONG_MIN_VALUE = -(2**53 - 1)
LONG_MAX_VALUE = 2**53 - 1


def is_finite_number(value: object) -> bool:
    """Return whether a value is a non-boolean, finite JSON-style number."""

    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        return False
    if isinstance(value, float):
        return isfinite(value)
    if isinstance(value, Decimal):
        return value.is_finite()
    return True


def finite_number_value(value: object) -> float | None:
    """Normalize a query/storage number using one backend-independent grammar."""

    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    if isinstance(value, str) and _DECIMAL_NUMBER_PATTERN.fullmatch(value) is None:
        return None
    try:
        parsed = float(value)
    except (OverflowError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def integer_value(value: object) -> int | None:
    """Normalize a 32-bit Ontology Integer without Python-only syntax."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and _INTEGER_PATTERN.fullmatch(value) is not None:
        try:
            parsed = int(value)
        except ValueError:
            return None
    else:
        return None
    return parsed if INTEGER_MIN_VALUE <= parsed <= INTEGER_MAX_VALUE else None


def is_finite_decimal(value: object) -> bool:
    """Accept the finite JSON-string representation used by Ontology Decimal values."""

    if not isinstance(value, str) or _DECIMAL_NUMBER_PATTERN.fullmatch(value) is None:
        return False
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        return False
    return parsed.is_finite()


def is_iso_date(value: object) -> bool:
    """Accept a calendar date only in the unambiguous ISO ``YYYY-MM-DD`` shape."""

    if not isinstance(value, str) or _DATE_PATTERN.fullmatch(value) is None:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def parse_aware_timestamp(value: object) -> datetime | None:
    """Parse a timezone-qualified ISO timestamp, returning ``None`` for ambiguous input."""

    if not isinstance(value, str) or _TIMESTAMP_PATTERN.fullmatch(value) is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def is_iso_timestamp(value: object) -> bool:
    """Return whether a value is an ISO timestamp with an explicit UTC offset."""

    return parse_aware_timestamp(value) is not None


def timestamp_microseconds(value: object) -> int | None:
    """Return an exact UTC microsecond key for one valid timestamp."""

    parsed = parse_aware_timestamp(value)
    if parsed is None:
        return None
    delta = parsed.astimezone(UTC) - _UNIX_EPOCH
    return ((delta.days * 86_400) + delta.seconds) * 1_000_000 + delta.microseconds


def timestamp_from_microseconds(value: object) -> str | None:
    """Return canonical UTC text without floating-point loss at datetime boundaries."""

    if isinstance(value, bool) or not isinstance(value, int):
        return None
    try:
        parsed = _UNIX_EPOCH + timedelta(microseconds=value)
    except (OverflowError, ValueError):
        return None
    return parsed.isoformat().replace("+00:00", "Z")


def _is_string_scalar(value: object) -> bool:
    return isinstance(value, str)


def _is_integer_scalar(value: object) -> bool:
    return integer_value(value) is not None and not isinstance(value, str)


def _is_long_scalar(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and LONG_MIN_VALUE <= value <= LONG_MAX_VALUE


def _is_boolean_scalar(value: object) -> bool:
    return isinstance(value, bool)


_SCALAR_TYPE_MATCHERS: dict[str, Callable[[object], bool]] = {
    "string": _is_string_scalar,
    "integer": _is_integer_scalar,
    "long": _is_long_scalar,
    "float": is_finite_number,
    "decimal": is_finite_decimal,
    "boolean": _is_boolean_scalar,
    "date": is_iso_date,
    "timestamp": is_iso_timestamp,
}


def matches_scalar_type(data_type: str, value: object) -> bool:
    """Validate one non-null scalar against an Action/Ontology scalar type."""

    matcher = _SCALAR_TYPE_MATCHERS.get(data_type)
    return matcher(value) if matcher is not None else False


__all__ = [
    "DECIMAL_NUMBER_SQL_PATTERN",
    "INTEGER_MAX_VALUE",
    "INTEGER_MIN_VALUE",
    "INTEGER_SQL_PATTERN",
    "LONG_MAX_VALUE",
    "LONG_MIN_VALUE",
    "finite_number_value",
    "integer_value",
    "is_finite_decimal",
    "is_finite_number",
    "is_iso_date",
    "is_iso_timestamp",
    "matches_scalar_type",
    "parse_aware_timestamp",
    "timestamp_microseconds",
    "timestamp_from_microseconds",
]
