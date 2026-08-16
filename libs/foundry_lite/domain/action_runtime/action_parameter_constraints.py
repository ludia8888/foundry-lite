"""Compile and evaluate fail-closed Action parameter constraints."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from decimal import Decimal
from math import isfinite
from typing import cast

from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.domain.json_values import is_bounded_json_value
from foundry_lite.domain.scalar_values import is_finite_decimal, matches_scalar_type

_NUMERIC_TYPES = frozenset({"integer", "long", "float", "decimal"})
_SCALAR_TYPES = frozenset({"string", "integer", "long", "float", "decimal", "boolean", "date", "timestamp"})


def validate_action_parameter_constraints(data_type: str, constraints: Mapping[str, object]) -> None:
    """Reject unknown, type-incompatible, or contradictory constraint declarations."""

    allowed = _allowed_keys(data_type)
    if unexpected := sorted(set(constraints) - allowed):
        raise ValidationFailed("unsupported action parameter constraints", details={"fields": unexpected})
    if "enum" in constraints:
        _validate_enum(data_type, constraints["enum"])
    if data_type == "string":
        _validate_integer_bounds(constraints, "minLength", "maxLength")
    elif data_type in {"array", "objectSet"}:
        _validate_integer_bounds(constraints, "minItems", "maxItems")
    elif data_type in _NUMERIC_TYPES:
        _validate_numeric_bounds(data_type, constraints)


def action_parameter_schema_constraints(data_type: str, constraints: Mapping[str, object]) -> dict[str, object]:
    """Return only valid JSON Schema keywords for the parameter wire type."""

    if data_type != "decimal":
        return dict(constraints)
    result = {"enum": constraints["enum"]} if "enum" in constraints else {}
    if "minimum" in constraints:
        result["x-foundry-decimal-minimum"] = constraints["minimum"]
    if "maximum" in constraints:
        result["x-foundry-decimal-maximum"] = constraints["maximum"]
    return result


def validate_action_parameter_constraints_for_value(
    name: str, data_type: str, value: object, constraints: Mapping[str, object]
) -> None:
    """Apply a previously compiled constraint set without precision-losing coercion."""

    if "enum" in constraints and not _enum_contains(data_type, constraints["enum"], value):
        raise _constraint_error(name, "enum")
    if data_type == "string":
        _validate_length(name, cast(str, value), constraints)
    elif data_type in {"array", "objectSet"}:
        _validate_item_count(name, cast(Sequence[object], value), constraints)
    elif data_type in _NUMERIC_TYPES:
        _validate_numeric_value(name, data_type, value, constraints)


def _allowed_keys(data_type: str) -> frozenset[str]:
    if data_type == "string":
        return frozenset({"enum", "minLength", "maxLength"})
    if data_type in _NUMERIC_TYPES:
        return frozenset({"enum", "minimum", "maximum"})
    if data_type in {"array", "objectSet"}:
        return frozenset({"enum", "minItems", "maxItems"})
    return frozenset({"enum"})


def _validate_enum(data_type: str, raw: object) -> None:
    values = _enum_values(raw)
    _validate_enum_values(data_type, values)
    canonical = [_constraint_identity(data_type, value) for value in values]
    if len(canonical) != len(set(canonical)):
        raise ValidationFailed("action parameter enum constraint contains duplicate values")


def _enum_values(raw: object) -> Sequence[object]:
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes) or not raw:
        raise ValidationFailed("action parameter enum constraint must be a non-empty list")
    return cast(Sequence[object], raw)


def _validate_enum_values(data_type: str, values: Sequence[object]) -> None:
    if not all(is_bounded_json_value(value) for value in values):
        raise ValidationFailed("action parameter enum constraint must contain bounded JSON values")
    if data_type in _SCALAR_TYPES and not all(matches_scalar_type(data_type, value) for value in values):
        raise ValidationFailed("action parameter enum constraint has a value of the wrong type")


def _validate_integer_bounds(constraints: Mapping[str, object], low_key: str, high_key: str) -> None:
    for key in (low_key, high_key):
        value = constraints.get(key)
        if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
            raise ValidationFailed(
                "action parameter size constraint must be a non-negative integer", details={"field": key}
            )
    _reject_reversed_bounds(constraints, low_key, high_key)


def _validate_numeric_bounds(data_type: str, constraints: Mapping[str, object]) -> None:
    for key in ("minimum", "maximum"):
        value = constraints.get(key)
        if value is not None and not _matches_numeric_bound(data_type, value):
            raise ValidationFailed("action parameter numeric constraint has the wrong type", details={"field": key})
    low = constraints.get("minimum")
    high = constraints.get("maximum")
    if low is not None and high is not None and _numeric_value(data_type, low) > _numeric_value(data_type, high):
        raise ValidationFailed("action parameter minimum cannot exceed maximum")


def _matches_numeric_bound(data_type: str, value: object) -> bool:
    if data_type == "decimal":
        return is_finite_decimal(value)
    if data_type == "float":
        if isinstance(value, bool) or not isinstance(value, int | float):
            return False
        return True if isinstance(value, int) else isfinite(value)
    return matches_scalar_type(data_type, value)


def _reject_reversed_bounds(constraints: Mapping[str, object], low_key: str, high_key: str) -> None:
    low = constraints.get(low_key)
    high = constraints.get(high_key)
    if isinstance(low, int) and isinstance(high, int) and low > high:
        raise ValidationFailed("action parameter minimum cannot exceed maximum")


def _validate_length(name: str, value: str, constraints: Mapping[str, object]) -> None:
    if isinstance(constraints.get("minLength"), int) and len(value) < cast(int, constraints["minLength"]):
        raise _constraint_error(name, "minLength")
    if isinstance(constraints.get("maxLength"), int) and len(value) > cast(int, constraints["maxLength"]):
        raise _constraint_error(name, "maxLength")


def _validate_item_count(name: str, value: Sequence[object], constraints: Mapping[str, object]) -> None:
    if isinstance(constraints.get("minItems"), int) and len(value) < cast(int, constraints["minItems"]):
        raise _constraint_error(name, "minItems")
    if isinstance(constraints.get("maxItems"), int) and len(value) > cast(int, constraints["maxItems"]):
        raise _constraint_error(name, "maxItems")


def _validate_numeric_value(name: str, data_type: str, value: object, constraints: Mapping[str, object]) -> None:
    normalized = _numeric_value(data_type, value)
    minimum = constraints.get("minimum")
    maximum = constraints.get("maximum")
    if minimum is not None and normalized < _numeric_value(data_type, minimum):
        raise _constraint_error(name, "minimum")
    if maximum is not None and normalized > _numeric_value(data_type, maximum):
        raise _constraint_error(name, "maximum")


def _numeric_value(data_type: str, value: object) -> Decimal | int | float:
    if data_type == "decimal":
        return Decimal(cast(str, value))
    return cast(int | float, value)


def _enum_contains(data_type: str, raw: object, value: object) -> bool:
    values = cast(Sequence[object], raw)
    identity = _constraint_identity(data_type, value)
    return any(_constraint_identity(data_type, candidate) == identity for candidate in values)


def _constraint_identity(data_type: str, value: object) -> str:
    if data_type == "decimal" and is_finite_decimal(value):
        return _decimal_identity(Decimal(cast(str, value)))
    if data_type in {"integer", "long", "float"} and not isinstance(value, bool) and isinstance(value, int | float):
        return _decimal_identity(Decimal(str(value)))
    return _json_identity(value)


def _decimal_identity(value: Decimal) -> str:
    parts = value.as_tuple()
    digits = list(parts.digits)
    exponent = cast(int, parts.exponent)
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exponent += 1
    if all(digit == 0 for digit in digits):
        return "decimal:0"
    return f"decimal:{parts.sign}:{''.join(str(digit) for digit in digits)}:{exponent}"


def _json_identity(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _constraint_error(name: str, constraint: str) -> ValidationFailed:
    return ValidationFailed("action parameter constraint failed", details={"invalid": [name], "constraint": constraint})


__all__ = [
    "action_parameter_schema_constraints",
    "validate_action_parameter_constraints",
    "validate_action_parameter_constraints_for_value",
]
