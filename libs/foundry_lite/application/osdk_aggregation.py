"""Application-layer models and helpers for osdk aggregation."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, TypedDict, cast

from foundry_lite.domain.errors import ValidationFailed


class OsdkAggregateMetric(TypedDict):
    name: str
    value: int


class OsdkAggregateBucket(TypedDict):
    group: dict[str, object]
    metrics: list[OsdkAggregateMetric]


class OsdkAggregateResult(TypedDict):
    excludedItems: int
    data: list[OsdkAggregateBucket]


class OsdkAggregateObject(Protocol):
    @property
    def properties(self) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class OsdkAggregatePlan:
    metric_names: tuple[str, ...]
    count_order: str
    group_by: tuple[str, ...]


@dataclass
class _AggregateState:
    group: dict[str, object]
    count: int = 0


def build_aggregate_plan(
    object_type_api_name: str,
    property_names: Sequence[str],
    request: Mapping[str, object],
) -> OsdkAggregatePlan:
    metric_names, count_order = _select_count_metrics(object_type_api_name, request)
    group_by = _group_by_properties(object_type_api_name, property_names, request)
    return OsdkAggregatePlan(metric_names, count_order, group_by)


def aggregate_query_order(
    existing: Sequence[Mapping[str, str]],
    group_by: Sequence[str],
) -> tuple[Mapping[str, str], ...]:
    items = [dict(item) for item in existing]
    existing_props = {str(item.get("property")) for item in items}
    for prop in group_by:
        if prop not in existing_props:
            items.append({"property": prop, "direction": "asc"})
    return tuple(items)


def aggregate_items(
    plan: OsdkAggregatePlan,
    items: Iterable[OsdkAggregateObject],
) -> OsdkAggregateResult:
    states: dict[str, _AggregateState] = {}
    for item in items:
        _add_item(plan, states, item)
    return _result_from_states(plan, states)


def _select_count_metrics(object_type_api_name: str, request: Mapping[str, object]) -> tuple[tuple[str, ...], str]:
    select = _request_section(request, ("$select", "select"), "select")
    metrics = [_count_metric(object_type_api_name, name, value) for name, value in select.items()]
    if not metrics:
        raise ValidationFailed("Python OSDK aggregate select must not be empty")
    orders = {order for _, order in metrics}
    if len(orders) != 1:
        raise ValidationFailed("Python OSDK count metrics must use one ordering")
    return tuple(name for name, _ in metrics), metrics[0][1]


def _count_metric(object_type_api_name: str, name: str, value: object) -> tuple[str, str]:
    if name == "$count":
        return "count", _aggregate_order(object_type_api_name, value)
    if isinstance(value, Mapping) and tuple(value) == ("$count",):
        return name, _aggregate_order(object_type_api_name, value["$count"])
    raise ValidationFailed(
        "Python OSDK aggregate currently supports only $count",
        details={"objectType": object_type_api_name, "metric": name},
    )


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


def _add_item(plan: OsdkAggregatePlan, states: dict[str, _AggregateState], item: OsdkAggregateObject) -> None:
    group = {prop: item.properties.get(prop) for prop in plan.group_by}
    key = json.dumps(tuple(group.items()), default=str)
    state = states.setdefault(key, _AggregateState(group=group))
    state.count += 1


def _result_from_states(plan: OsdkAggregatePlan, states: dict[str, _AggregateState]) -> OsdkAggregateResult:
    if not states and not plan.group_by:
        states["[]"] = _AggregateState(group={}, count=0)
    rows = list(states.values())
    if plan.count_order != "unordered":
        rows.sort(key=lambda state: state.count, reverse=plan.count_order == "desc")
    return {"excludedItems": 0, "data": [_bucket_from_state(plan, state) for state in rows]}


def _bucket_from_state(plan: OsdkAggregatePlan, state: _AggregateState) -> OsdkAggregateBucket:
    metrics: list[OsdkAggregateMetric] = []
    for name in plan.metric_names:
        metrics.append({"name": name, "value": state.count})
    return {"group": state.group, "metrics": metrics}
