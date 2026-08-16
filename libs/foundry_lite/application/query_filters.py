"""Application-layer models and helpers for query filters."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TypeGuard, cast

from foundry_lite.domain.action_runtime.action_conditions import action_condition_values_equal
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.domain.scalar_values import finite_number_value, integer_value, is_iso_date, timestamp_microseconds

FilterEvaluator = Callable[[object, object], bool]
_INVALID_FILTER_VALUE = object()


def _filter_eq(current: object, value: object) -> bool:
    return action_condition_values_equal(current, value)


def _filter_in(current: object, value: object) -> bool:
    return _is_filter_sequence(value) and any(action_condition_values_equal(current, item) for item in value)


def _filter_gte(current: object, value: object) -> bool:
    if isinstance(current, bool) or isinstance(value, bool):
        return False
    if isinstance(current, (int, float)) and isinstance(value, (int, float)):
        return current >= value
    if isinstance(current, str) and isinstance(value, str):
        return current >= value
    return False


def _filter_gt(current: object, value: object) -> bool:
    if isinstance(current, bool) or isinstance(value, bool):
        return False
    if isinstance(current, (int, float)) and isinstance(value, (int, float)):
        return current > value
    if isinstance(current, str) and isinstance(value, str):
        return current > value
    return False


def _filter_lte(current: object, value: object) -> bool:
    if isinstance(current, bool) or isinstance(value, bool):
        return False
    if isinstance(current, (int, float)) and isinstance(value, (int, float)):
        return current <= value
    if isinstance(current, str) and isinstance(value, str):
        return current <= value
    return False


def _filter_lt(current: object, value: object) -> bool:
    if isinstance(current, bool) or isinstance(value, bool):
        return False
    if isinstance(current, (int, float)) and isinstance(value, (int, float)):
        return current < value
    if isinstance(current, str) and isinstance(value, str):
        return current < value
    return False


def _filter_contains(current: object, value: object) -> bool:
    return isinstance(current, str) and isinstance(value, str) and value.lower() in current.lower()


def _filter_group(value: object) -> Sequence[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise ValidationFailed("filter logical group must be a list")
    if not value:
        raise ValidationFailed("filter logical group must be a non-empty list")
    items: list[Mapping[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValidationFailed("filter logical group items must be objects")
        items.append(cast(Mapping[str, object], item))
    return items


def _required_string(filter_ast: Mapping[str, object], key: str) -> str:
    value = filter_ast.get(key)
    if isinstance(value, str):
        return value
    raise ValidationFailed("filter field must be a string", details={"field": key})


def _required_value(filter_ast: Mapping[str, object], key: str) -> object:
    if key in filter_ast:
        return filter_ast[key]
    raise ValidationFailed("filter field is required", details={"field": key})


FILTER_OPERATIONS: dict[str, FilterEvaluator] = {
    "eq": _filter_eq,
    "in": _filter_in,
    "gte": _filter_gte,
    "gt": _filter_gt,
    "lte": _filter_lte,
    "lt": _filter_lt,
    "contains": _filter_contains,
}


def matches_filter(
    properties: Mapping[str, object],
    filter_ast: Mapping[str, object],
    *,
    property_data_types: Mapping[str, str] | None = None,
) -> bool:
    logical_result = _matches_logical_filter(properties, filter_ast, property_data_types)
    if logical_result is not None:
        return logical_result
    return _matches_property_filter(properties, filter_ast, property_data_types)


def validate_filter_ast(
    filter_ast: Mapping[str, object],
    *,
    property_data_types: Mapping[str, str] | None = None,
) -> None:
    node = _filter_node(filter_ast)
    if node in {"and", "or"}:
        for item in _filter_group(filter_ast[node]):
            validate_filter_ast(item, property_data_types=property_data_types)
        return
    op = _required_string(filter_ast, "op")
    prop = _required_string(filter_ast, "property")
    value = _required_value(filter_ast, "value")
    if op not in FILTER_OPERATIONS:
        raise ValidationFailed("unsupported filter operation", details={"op": op})
    if property_data_types is not None:
        _validate_typed_filter(prop, op, value, property_data_types)


def _matches_logical_filter(
    properties: Mapping[str, object],
    filter_ast: Mapping[str, object],
    property_data_types: Mapping[str, str] | None,
) -> bool | None:
    node = _filter_node(filter_ast)
    if node == "and":
        return all(
            matches_filter(properties, item, property_data_types=property_data_types)
            for item in _filter_group(filter_ast[node])
        )
    if node == "or":
        return any(
            matches_filter(properties, item, property_data_types=property_data_types)
            for item in _filter_group(filter_ast[node])
        )
    return None


def _matches_property_filter(
    properties: Mapping[str, object],
    filter_ast: Mapping[str, object],
    property_data_types: Mapping[str, str] | None,
) -> bool:
    prop = _required_string(filter_ast, "property")
    op = _required_string(filter_ast, "op")
    evaluator = FILTER_OPERATIONS.get(op)
    if evaluator is None:
        raise ValidationFailed("unsupported filter operation", details={"op": op})
    expected = _required_value(filter_ast, "value")
    if property_data_types is None:
        return evaluator(properties.get(prop), expected)
    data_type = _required_property_data_type(prop, property_data_types)
    return _matches_typed_filter(properties.get(prop), op, expected, data_type)


def _matches_typed_filter(current: object, op: str, expected: object, data_type: str) -> bool:
    if op == "in":
        return _matches_typed_in(current, expected, data_type)
    if op == "contains":
        return data_type == "string" and _filter_contains(current, expected)
    normalized_current = _typed_filter_value(current, data_type)
    normalized_expected = _typed_filter_value(expected, data_type)
    if _INVALID_FILTER_VALUE in (normalized_current, normalized_expected):
        return False
    return FILTER_OPERATIONS[op](normalized_current, normalized_expected)


def _matches_typed_in(current: object, expected: object, data_type: str) -> bool:
    if not _is_filter_sequence(expected):
        return False
    normalized_current = _typed_filter_value(current, data_type)
    if normalized_current is _INVALID_FILTER_VALUE:
        return False
    candidates = [_typed_filter_value(item, data_type) for item in expected]
    return any(
        candidate is not _INVALID_FILTER_VALUE and action_condition_values_equal(normalized_current, candidate)
        for candidate in candidates
    )


def _typed_filter_value(value: object, data_type: str) -> object:
    if value is None:
        return None
    normalizer = _FILTER_VALUE_NORMALIZERS.get(data_type)
    return normalizer(value) if normalizer is not None else _INVALID_FILTER_VALUE


def _typed_number(value: object) -> float | object:
    parsed = finite_number_value(value)
    return parsed if parsed is not None else _INVALID_FILTER_VALUE


def _typed_integer(value: object) -> int | object:
    parsed = integer_value(value)
    return parsed if parsed is not None else _INVALID_FILTER_VALUE


def _typed_boolean(value: object) -> object:
    return value if isinstance(value, bool) else _INVALID_FILTER_VALUE


def _typed_date(value: object) -> object:
    return value if is_iso_date(value) else _INVALID_FILTER_VALUE


def _typed_timestamp(value: object) -> object:
    parsed = timestamp_microseconds(value)
    return parsed if parsed is not None else _INVALID_FILTER_VALUE


def _typed_string(value: object) -> object:
    return value if isinstance(value, str) else _INVALID_FILTER_VALUE


_FILTER_VALUE_NORMALIZERS: dict[str, Callable[[object], object]] = {
    "integer": _typed_integer,
    "float": _typed_number,
    "number": _typed_number,
    "boolean": _typed_boolean,
    "date": _typed_date,
    "timestamp": _typed_timestamp,
    "string": _typed_string,
}


def _validate_typed_filter(
    prop: str,
    op: str,
    value: object,
    property_data_types: Mapping[str, str],
) -> None:
    data_type = _required_property_data_type(prop, property_data_types)
    _validate_filter_operator_type(prop, op, data_type)
    values = _typed_filter_candidates(prop, op, value)
    for item in values:
        _validate_filter_candidate(prop, op, item, data_type)


def _validate_filter_operator_type(prop: str, op: str, data_type: str) -> None:
    if op == "contains" and data_type != "string":
        raise ValidationFailed("contains filter requires a string property", details={"property": prop})
    if op in {"gt", "gte", "lt", "lte"} and data_type == "boolean":
        raise ValidationFailed("range filter does not support boolean properties", details={"property": prop})


def _typed_filter_candidates(prop: str, op: str, value: object) -> Sequence[object]:
    if op == "in" and not _is_filter_sequence(value):
        raise ValidationFailed("in filter value must be a list", details={"property": prop})
    return value if op == "in" and _is_filter_sequence(value) else (value,)


def _validate_filter_candidate(prop: str, op: str, item: object, data_type: str) -> None:
    if item is None and op in {"eq", "in"}:
        return
    if _typed_filter_value(item, data_type) is _INVALID_FILTER_VALUE:
        raise ValidationFailed(
            "filter value does not match property type",
            details={"property": prop, "dataType": data_type, "operation": op},
        )


def _required_property_data_type(prop: str, property_data_types: Mapping[str, str]) -> str:
    data_type = property_data_types.get(prop)
    if data_type is None:
        raise ValidationFailed("filter references unknown property", details={"property": prop})
    return data_type


def _filter_node(filter_ast: Mapping[str, object]) -> str:
    nodes = [name for name in ("and", "or", "op") if name in filter_ast]
    if len(nodes) != 1:
        raise ValidationFailed("filter must contain exactly one logical or property node", details={"nodes": nodes})
    node = nodes[0]
    allowed = {node} if node in {"and", "or"} else {"property", "op", "value"}
    unexpected = sorted(str(name) for name in filter_ast if name not in allowed)
    if unexpected:
        raise ValidationFailed("filter contains unsupported fields", details={"fields": unexpected})
    return node


def _is_filter_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray)
