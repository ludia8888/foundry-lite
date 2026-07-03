"""Application-layer models and helpers for osdk aggregation.

The Python OSDK compiles an aggregate request into a plan for ONE server call
(the object aggregate endpoint) instead of paging objects through the SDK and
counting client-side. WHY: the server owns masking/tenancy enforcement and the
database owns the group math, so client-side aggregation both leaked work and
could never scale past a few pages.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TypedDict, cast

from foundry_lite.application.ports import ObjectAggregationGroup, ObjectAggregationResult
from foundry_lite.domain.errors import ValidationFailed

_METRIC_FUNCTION_KEYS: Mapping[str, str] = {
    "$sum": "sum",
    "$avg": "avg",
    "$min": "min",
    "$max": "max",
}


class OsdkAggregateMetric(TypedDict):
    name: str
    value: float | int | None


class OsdkAggregateBucket(TypedDict):
    group: dict[str, object]
    metrics: list[OsdkAggregateMetric]


class OsdkAggregateResult(TypedDict):
    excludedItems: int
    data: list[OsdkAggregateBucket]


@dataclass(frozen=True)
class OsdkAggregatePlanMetric:
    name: str
    function: str
    property_name: str | None


@dataclass(frozen=True)
class OsdkAggregatePlan:
    metric_names: tuple[str, ...]
    count_order: str
    group_by: tuple[str, ...]
    metrics: tuple[OsdkAggregatePlanMetric, ...]


def build_aggregate_plan(
    object_type_api_name: str,
    property_names: Sequence[str],
    request: Mapping[str, object],
) -> OsdkAggregatePlan:
    metrics, order = _select_metrics(object_type_api_name, property_names, request)
    group_by = _group_by_properties(object_type_api_name, property_names, request)
    return OsdkAggregatePlan(tuple(metric.name for metric in metrics), order, group_by, metrics)


def aggregate_request_select(plan: OsdkAggregatePlan) -> list[dict[str, object]]:
    """Serialize the plan's metrics into the aggregate endpoint's select payload."""
    return [
        {"name": metric.name, "function": metric.function, "property": metric.property_name} for metric in plan.metrics
    ]


def aggregate_result_from_groups(plan: OsdkAggregatePlan, result: ObjectAggregationResult) -> OsdkAggregateResult:
    """Convert the server aggregation response into the OSDK bucket shape.

    The server orders groups by group key; the OSDK order option instead sorts
    buckets by the first metric's value, matching the pre-server behaviour.
    """
    buckets = [_bucket_from_group(plan, group) for group in result["groups"]]
    if plan.count_order != "unordered":
        buckets.sort(key=_bucket_order_value, reverse=plan.count_order == "desc")
    return {"excludedItems": 0, "data": buckets}


def _bucket_from_group(plan: OsdkAggregatePlan, group: ObjectAggregationGroup) -> OsdkAggregateBucket:
    metrics: list[OsdkAggregateMetric] = [
        {"name": name, "value": group["metrics"].get(name)} for name in plan.metric_names
    ]
    return {"group": dict(group["key"]), "metrics": metrics}


def _bucket_order_value(bucket: OsdkAggregateBucket) -> float:
    value = bucket["metrics"][0]["value"] if bucket["metrics"] else None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return float("-inf")
    return float(value)


def _select_metrics(
    object_type_api_name: str,
    property_names: Sequence[str],
    request: Mapping[str, object],
) -> tuple[tuple[OsdkAggregatePlanMetric, ...], str]:
    select = _request_section(request, ("$select", "select"), "select")
    metrics = [_metric(object_type_api_name, property_names, name, value) for name, value in select.items()]
    if not metrics:
        raise ValidationFailed("Python OSDK aggregate select must not be empty")
    orders = {order for _, order in metrics}
    if len(orders) != 1:
        raise ValidationFailed("Python OSDK aggregate metrics must use one ordering")
    return tuple(metric for metric, _ in metrics), metrics[0][1]


def _metric(
    object_type_api_name: str,
    property_names: Sequence[str],
    name: str,
    value: object,
) -> tuple[OsdkAggregatePlanMetric, str]:
    if name == "$count":
        return OsdkAggregatePlanMetric("count", "count", None), _aggregate_order(object_type_api_name, value)
    if isinstance(value, Mapping) and tuple(value) == ("$count",):
        return OsdkAggregatePlanMetric(name, "count", None), _aggregate_order(object_type_api_name, value["$count"])
    property_metric = _property_metric(object_type_api_name, property_names, name, value)
    if property_metric is not None:
        return property_metric
    raise ValidationFailed(
        "Python OSDK aggregate metric must be $count, $sum, $avg, $min, or $max",
        details={"objectType": object_type_api_name, "metric": name},
    )


def _property_metric(
    object_type_api_name: str,
    property_names: Sequence[str],
    name: str,
    value: object,
) -> tuple[OsdkAggregatePlanMetric, str] | None:
    """Parse the ``{"sumAmount": {"amount": {"$sum": "unordered"}}}`` metric shape."""
    if not isinstance(value, Mapping) or len(value) != 1:
        return None
    property_name, spec = next(iter(value.items()))
    if not isinstance(spec, Mapping) or len(spec) != 1:
        return None
    function_key, order = next(iter(spec.items()))
    function = _METRIC_FUNCTION_KEYS.get(str(function_key))
    if function is None:
        return None
    if property_names and str(property_name) not in property_names:
        raise ValidationFailed(
            "Python OSDK aggregate references unknown metric property",
            details={"objectType": object_type_api_name, "metric": name, "property": property_name},
        )
    metric = OsdkAggregatePlanMetric(name, function, str(property_name))
    return metric, _aggregate_order(object_type_api_name, order)


def _aggregate_order(object_type_api_name: str, value: object) -> str:
    if isinstance(value, str) and value in {"unordered", "asc", "desc"}:
        return value
    raise ValidationFailed(
        "Python OSDK aggregate order must be unordered, asc, or desc",
        details={"objectType": object_type_api_name, "order": value},
    )


def _group_by_properties(
    object_type_api_name: str,
    property_names: Sequence[str],
    request: Mapping[str, object],
) -> tuple[str, ...]:
    group_by = _optional_request_section(request, ("$groupBy", "groupBy", "group_by"), "groupBy")
    if group_by is None:
        return ()
    return tuple(
        _group_by_property(object_type_api_name, property_names, prop, mode) for prop, mode in group_by.items()
    )


def _group_by_property(
    object_type_api_name: str,
    property_names: Sequence[str],
    prop: str,
    mode: object,
) -> str:
    if property_names and prop not in property_names:
        raise ValidationFailed(
            "Python OSDK aggregate references unknown groupBy property",
            details={"objectType": object_type_api_name, "property": prop},
        )
    if mode != "exact":
        raise ValidationFailed("Python OSDK aggregate currently supports only exact groupBy")
    return prop


def _request_section(
    request: Mapping[str, object],
    names: Sequence[str],
    label: str,
) -> Mapping[str, object]:
    section = _optional_request_section(request, names, label)
    if section is None:
        raise ValidationFailed(f"Python OSDK aggregate requires {label}")
    return section


def _optional_request_section(
    request: Mapping[str, object],
    names: Sequence[str],
    label: str,
) -> Mapping[str, object] | None:
    present = [name for name in names if name in request]
    if len(present) > 1:
        raise ValidationFailed(f"Python OSDK aggregate accepts one {label} field")
    if not present:
        return None
    section = request[present[0]]
    if isinstance(section, Mapping):
        return cast(Mapping[str, object], section)
    raise ValidationFailed(f"Python OSDK aggregate {label} must be an object")
