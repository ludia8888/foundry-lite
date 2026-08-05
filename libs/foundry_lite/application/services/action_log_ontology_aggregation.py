"""Bounded server-side Action Log object aggregation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.action_log_types import ACTION_LOG_PROPERTY_TYPES
from foundry_lite.application.ports import ObjectAggregationGroup, ObjectAggregationResult, ObjectQueryItem
from foundry_lite.application.services.object_store.aggregation import (
    ObjectAggregationPlan,
    build_aggregation_plan,
    finalize_aggregation_result,
)


def aggregate_action_log_items(
    object_type: str,
    items: Sequence[ObjectQueryItem],
    group_by: Sequence[str] | None,
    select: Sequence[Mapping[str, object]] | None,
) -> ObjectAggregationResult:
    plan = build_aggregation_plan(object_type, ACTION_LOG_PROPERTY_TYPES, set(), group_by=group_by, select=select)
    grouped: dict[tuple[object, ...], list[ObjectQueryItem]] = {}
    for item in items:
        key = tuple(item["properties"].get(name) for name in plan.group_by)
        grouped.setdefault(key, []).append(item)
    rows = [_aggregation_group(plan, key, values) for key, values in grouped.items()]
    return finalize_aggregation_result(plan, rows)


def _aggregation_group(
    plan: ObjectAggregationPlan, key: tuple[object, ...], items: Sequence[ObjectQueryItem]
) -> ObjectAggregationGroup:
    return {
        "key": dict(zip(plan.group_by, key, strict=True)),
        "metrics": {metric["name"]: _metric_value(metric, items) for metric in plan.metrics},
    }


def _metric_value(metric: Mapping[str, object], items: Sequence[ObjectQueryItem]) -> float | int | None:
    if metric["function"] == "count":
        return len(items)
    prop = str(metric["property"])
    values = [
        value
        for item in items
        if isinstance((value := item["properties"].get(prop)), (int, float)) and not isinstance(value, bool)
    ]
    if not values:
        return None
    if metric["function"] == "sum":
        return sum(values)
    if metric["function"] == "avg":
        return sum(values) / len(values)
    return min(values) if metric["function"] == "min" else max(values)
