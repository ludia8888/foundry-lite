"""Fail-closed validation for trained-model output values."""

from __future__ import annotations

from collections.abc import Mapping, Set
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from math import isfinite

from foundry_lite.application.ports.trained_model_inference import TrainedModelField
from foundry_lite.application.services.pipeline_media_reference import (
    validated_model_media_reference,
)
from foundry_lite.domain.errors import ValidationFailed

_INTEGER_RANGES = {
    "byte": (-128, 127),
    "short": (-32_768, 32_767),
    "integer": (-2_147_483_648, 2_147_483_647),
    "long": (-9_223_372_036_854_775_808, 9_223_372_036_854_775_807),
}
_FLOAT32_MAX = 3.4028235e38
_MAX_JSON_DEPTH = 64


def require_trained_model_output_value(
    field: TrainedModelField,
    value: object,
    source: Mapping[str, object],
    trusted_media_coordinates: Set[tuple[str, str, str]] | None,
) -> None:
    if _is_trained_model_value(field, value) and _is_bound_media_reference(
        field,
        value,
        source,
        trusted_media_coordinates,
    ):
        return
    raise ValidationFailed(
        "trained model returned a value that contradicts its pinned output type",
        details={
            "field": field.name,
            "expectedType": field.data_type,
            "actualType": type(value).__name__,
        },
    )


def _is_trained_model_value(field: TrainedModelField, value: object) -> bool:
    if value is None:
        return not field.is_required
    if field.data_type in _INTEGER_RANGES:
        minimum, maximum = _INTEGER_RANGES[field.data_type]
        return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum
    return _is_non_integer_trained_model_value(field.data_type, value)


def _is_non_integer_trained_model_value(data_type: str, value: object) -> bool:
    if data_type in {"float", "double"}:
        return _is_finite_float_value(data_type, value)
    if data_type == "decimal":
        return _is_decimal_value(value)
    return _is_scalar_or_structured_value(data_type, value)


def _is_scalar_or_structured_value(data_type: str, value: object) -> bool:
    if data_type == "boolean":
        return isinstance(value, bool)
    if data_type in {"string", "binary"}:
        return isinstance(value, str)
    if data_type in {"date", "timestamp"}:
        return _is_temporal_value(data_type, value)
    if data_type == "array":
        return isinstance(value, list) and _is_json_safe_value(value)
    if data_type == "mediaReference":
        return _is_media_reference_value(value)
    return isinstance(value, dict) and _is_json_safe_value(value)


def _is_temporal_value(data_type: str, value: object) -> bool:
    return _is_iso_date(value) if data_type == "date" else _is_iso_timestamp(value)


def _is_finite_float_value(data_type: str, value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    try:
        numeric = float(value)
    except (OverflowError, ValueError):
        return False
    return isfinite(numeric) and (data_type == "double" or abs(numeric) <= _FLOAT32_MAX)


def _is_decimal_value(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, str | int | float | Decimal):
        return False
    try:
        return Decimal(str(value)).is_finite()
    except InvalidOperation:
        return False


def _is_iso_date(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_iso_timestamp(value: object) -> bool:
    if not isinstance(value, str) or "T" not in value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _is_media_reference_value(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    try:
        validated_model_media_reference(value)
    except ValidationFailed:
        return False
    return True


def _is_bound_media_reference(
    field: TrainedModelField,
    value: object,
    source: Mapping[str, object],
    trusted_media_coordinates: Set[tuple[str, str, str]] | None,
) -> bool:
    if field.data_type != "mediaReference" or value is None:
        return True
    if not isinstance(value, Mapping) or not _contains_media_reference(source, value):
        return False
    return trusted_media_coordinates is None or _media_coordinates(value) in trusted_media_coordinates


def _contains_media_reference(container: object, expected: Mapping[str, object]) -> bool:
    if isinstance(container, Mapping):
        if _media_coordinates(container) == _media_coordinates(expected):
            return True
        return any(_contains_media_reference(value, expected) for value in container.values())
    if isinstance(container, list | tuple):
        return any(_contains_media_reference(value, expected) for value in container)
    return False


def _media_coordinates(value: Mapping[str, object]) -> tuple[str, str, str]:
    return (
        str(value.get("mediaItemVersionId") or ""),
        str(value.get("mimeType") or ""),
        str(value.get("contentHash") or "").removeprefix("sha256:"),
    )


def _is_json_safe_value(value: object, *, depth: int = 0) -> bool:
    if depth > _MAX_JSON_DEPTH:
        return False
    if value is None or isinstance(value, str | bool | int):
        return True
    if isinstance(value, float):
        return isfinite(value)
    if isinstance(value, list | tuple):
        return all(_is_json_safe_value(item, depth=depth + 1) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_safe_value(item, depth=depth + 1) for key, item in value.items())
    return False
