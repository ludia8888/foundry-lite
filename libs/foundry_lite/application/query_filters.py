"""Application-layer models and helpers for query filters."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from typing import cast

from foundry_lite.domain.errors import ValidationFailed

FilterEvaluator = Callable[[object, object], bool]


def _filter_eq(current: object, value: object) -> bool:
    return current == value


def _filter_in(current: object, value: object) -> bool:
    if isinstance(value, Collection) and not isinstance(value, str):
        return current in value
    return False


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
    if current is None:
        return False
    return str(value).lower() in str(current).lower()


def _filter_group(value: object) -> Sequence[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise ValidationFailed("filter logical group must be a list")
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


def matches_filter(properties: Mapping[str, object], filter_ast: Mapping[str, object]) -> bool:
    logical_result = _matches_logical_filter(properties, filter_ast)
    if logical_result is not None:
        return logical_result
    return _matches_property_filter(properties, filter_ast)


def validate_filter_ast(filter_ast: Mapping[str, object]) -> None:
    if "and" in filter_ast:
        for item in _filter_group(filter_ast["and"]):
            validate_filter_ast(item)
        return
    if "or" in filter_ast:
        for item in _filter_group(filter_ast["or"]):
            validate_filter_ast(item)
        return
    op = _required_string(filter_ast, "op")
    _required_string(filter_ast, "property")
    _required_value(filter_ast, "value")
    if op not in FILTER_OPERATIONS:
        raise ValidationFailed("unsupported filter operation", details={"op": op})


def _matches_logical_filter(properties: Mapping[str, object], filter_ast: Mapping[str, object]) -> bool | None:
    if "and" in filter_ast:
        return all(matches_filter(properties, item) for item in _filter_group(filter_ast["and"]))
    if "or" in filter_ast:
        return any(matches_filter(properties, item) for item in _filter_group(filter_ast["or"]))
    return None


def _matches_property_filter(properties: Mapping[str, object], filter_ast: Mapping[str, object]) -> bool:
    prop = _required_string(filter_ast, "property")
    op = _required_string(filter_ast, "op")
    evaluator = FILTER_OPERATIONS.get(op)
    if evaluator is None:
        raise ValidationFailed("unsupported filter operation", details={"op": op})
    return evaluator(properties.get(prop), _required_value(filter_ast, "value"))
