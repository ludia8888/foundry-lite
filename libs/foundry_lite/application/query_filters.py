from __future__ import annotations

from collections.abc import Callable
from typing import Any

from foundry_lite.domain.errors import ValidationFailed

FilterEvaluator = Callable[[Any, Any], bool]


def _filter_eq(current: Any, value: Any) -> bool:
    return current == value


def _filter_in(current: Any, value: Any) -> bool:
    return current in value


def _filter_gte(current: Any, value: Any) -> bool:
    return current is not None and current >= value


def _filter_lte(current: Any, value: Any) -> bool:
    return current is not None and current <= value


def _filter_contains(current: Any, value: Any) -> bool:
    return current is not None and str(value).lower() in str(current).lower()


FILTER_OPERATIONS: dict[str, FilterEvaluator] = {
    "eq": _filter_eq,
    "in": _filter_in,
    "gte": _filter_gte,
    "lte": _filter_lte,
    "contains": _filter_contains,
}


def matches_filter(properties: dict[str, Any], filter_ast: dict[str, Any]) -> bool:
    logical_result = _matches_logical_filter(properties, filter_ast)
    if logical_result is not None:
        return logical_result
    return _matches_property_filter(properties, filter_ast)


def _matches_logical_filter(properties: dict[str, Any], filter_ast: dict[str, Any]) -> bool | None:
    if "and" in filter_ast:
        return all(matches_filter(properties, item) for item in filter_ast["and"])
    if "or" in filter_ast:
        return any(matches_filter(properties, item) for item in filter_ast["or"])
    return None


def _matches_property_filter(properties: dict[str, Any], filter_ast: dict[str, Any]) -> bool:
    prop = filter_ast["property"]
    op = filter_ast["op"]
    evaluator = FILTER_OPERATIONS.get(op)
    if evaluator is None:
        raise ValidationFailed("unsupported filter operation", details={"op": op})
    return evaluator(properties.get(prop), filter_ast["value"])
