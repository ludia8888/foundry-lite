"""Server-side aggregation helpers for committed dataset versions."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from foundry_lite.application.ports import (
    DatasetAggregationComputeResult,
    DatasetAggregationFilter,
    DatasetAggregationFunction,
    DatasetAggregationGroup,
    DatasetAggregationMetric,
    DatasetAggregationOperator,
    DatasetAggregationPlan,
    DatasetAggregationResult,
    DatasetSchemaJson,
    TabularRow,
)
from foundry_lite.application.services.dataset.schema_evolution import schema_columns_by_name
from foundry_lite.domain.errors import PermissionDenied, ValidationFailed

DATASET_AGGREGATION_GROUP_LIMIT = 1_000
DATASET_AGGREGATION_MAX_GROUP_BY = 2
DATASET_AGGREGATION_MAX_FILTERS = 10
DATASET_AGGREGATION_FUNCTIONS = frozenset({"count", "sum", "avg", "min", "max"})
DATASET_AGGREGATION_NUMERIC_TYPES = frozenset({"integer", "long", "float", "number"})
DATASET_AGGREGATION_NUMERIC_OPERATORS = frozenset({"gt", "gte", "lt", "lte"})
DATASET_AGGREGATION_OPERATORS = frozenset({"eq", "neq", "contains", "gt", "gte", "lt", "lte"})


@dataclass
class _MetricState:
    count: int = 0
    total: float = 0.0
    min_value: float | None = None
    max_value: float | None = None


@dataclass
class _GroupState:
    key: dict[str, object]
    metrics: dict[str, _MetricState]


def build_dataset_aggregation_plan(
    dataset_ref: str,
    schema: DatasetSchemaJson,
    masked_columns: set[str],
    *,
    filters: Sequence[Mapping[str, object]] | None,
    group_by: Sequence[str] | None,
    select: Sequence[Mapping[str, object]] | None,
) -> DatasetAggregationPlan:
    column_types = _schema_column_types(schema)
    return DatasetAggregationPlan(
        group_by=_validated_group_by(dataset_ref, column_types, masked_columns, group_by),
        metrics=_validated_metrics(dataset_ref, column_types, masked_columns, select),
        filters=_validated_filters(dataset_ref, column_types, masked_columns, filters),
        group_limit=DATASET_AGGREGATION_GROUP_LIMIT,
    )


def dataset_aggregation_result(
    dataset_ref: str,
    version_id: str,
    compute_result: DatasetAggregationComputeResult,
) -> DatasetAggregationResult:
    return {
        "datasetRef": dataset_ref,
        "versionId": version_id,
        "rowCount": compute_result["rowCount"],
        "filteredRowCount": compute_result["filteredRowCount"],
        "groups": compute_result["groups"],
        "totalGroups": compute_result["totalGroups"],
    }


def aggregate_dataset_rows(
    dataset_ref: str,
    version_id: str,
    rows: Sequence[TabularRow],
    plan: DatasetAggregationPlan,
) -> DatasetAggregationResult:
    filtered_rows = [row for row in rows if _matches_filters(row, plan.filters)]
    groups = _group_rows(filtered_rows, plan)
    if len(groups) > DATASET_AGGREGATION_GROUP_LIMIT:
        raise ValidationFailed(
            "dataset aggregation exceeds the group limit; add a filter or group by a lower-cardinality column",
            details={"datasetRef": dataset_ref, "groupLimit": DATASET_AGGREGATION_GROUP_LIMIT},
        )
    shaped = [_shaped_group(group, plan.metrics) for group in _ordered_groups(list(groups.values()))]
    compute_result: DatasetAggregationComputeResult = {
        "rowCount": len(rows),
        "filteredRowCount": len(filtered_rows),
        "groups": shaped,
        "totalGroups": len(shaped),
    }
    return dataset_aggregation_result(dataset_ref, version_id, compute_result)


def _schema_column_types(schema: DatasetSchemaJson) -> dict[str, str]:
    columns = schema_columns_by_name(schema)
    return {name: str(column.get("type", "string")) for name, column in columns.items()}


def _validated_group_by(
    dataset_ref: str,
    column_types: Mapping[str, str],
    masked_columns: set[str],
    group_by: Sequence[str] | None,
) -> tuple[str, ...]:
    names = tuple(str(name) for name in group_by or [])
    if len(names) > DATASET_AGGREGATION_MAX_GROUP_BY:
        raise ValidationFailed("dataset aggregation supports at most two groupBy columns")
    if len(set(names)) != len(names):
        raise ValidationFailed("dataset aggregation groupBy columns must be unique")
    for name in names:
        _require_column(dataset_ref, column_types, masked_columns, name, "groupBy")
    return names


def _validated_metrics(
    dataset_ref: str,
    column_types: Mapping[str, str],
    masked_columns: set[str],
    select: Sequence[Mapping[str, object]] | None,
) -> tuple[DatasetAggregationMetric, ...]:
    metrics = [_metric_from_request(dataset_ref, column_types, masked_columns, item) for item in select or []]
    if not metrics:
        raise ValidationFailed("dataset aggregation select must contain at least one metric")
    names = [metric["name"] for metric in metrics]
    if len(set(names)) != len(names):
        raise ValidationFailed("dataset aggregation metric names must be unique")
    return tuple(metrics)


def _validated_filters(
    dataset_ref: str,
    column_types: Mapping[str, str],
    masked_columns: set[str],
    filters: Sequence[Mapping[str, object]] | None,
) -> tuple[DatasetAggregationFilter, ...]:
    items = list(filters or [])
    if len(items) > DATASET_AGGREGATION_MAX_FILTERS:
        raise ValidationFailed("dataset aggregation supports at most ten filters")
    return tuple(_filter_from_request(dataset_ref, column_types, masked_columns, item) for item in items)


def _metric_from_request(
    dataset_ref: str,
    column_types: Mapping[str, str],
    masked_columns: set[str],
    item: Mapping[str, object],
) -> DatasetAggregationMetric:
    function = _metric_function(dataset_ref, item)
    if function == "count":
        return _count_metric(item)
    property_name = _metric_property(dataset_ref, column_types, masked_columns, item, function)
    return {"name": _metric_name(item, f"{function}_{property_name}"), "function": function, "property": property_name}


def _metric_function(dataset_ref: str, item: Mapping[str, object]) -> DatasetAggregationFunction:
    function = item.get("function")
    if not isinstance(function, str) or function not in DATASET_AGGREGATION_FUNCTIONS:
        raise ValidationFailed(
            "dataset aggregation metric function must be count, sum, avg, min, or max",
            details={"datasetRef": dataset_ref, "function": function},
        )
    return cast(DatasetAggregationFunction, function)


def _count_metric(item: Mapping[str, object]) -> DatasetAggregationMetric:
    if item.get("property") is not None:
        raise ValidationFailed("dataset aggregation count metric must not name a property")
    return {"name": _metric_name(item, "count"), "function": "count", "property": None}


def _metric_property(
    dataset_ref: str,
    column_types: Mapping[str, str],
    masked_columns: set[str],
    item: Mapping[str, object],
    function: str,
) -> str:
    property_name = item.get("property")
    if not isinstance(property_name, str) or not property_name:
        raise ValidationFailed("dataset aggregation metric requires a property")
    _require_numeric_column(dataset_ref, column_types, masked_columns, property_name, function)
    return property_name


def _filter_from_request(
    dataset_ref: str,
    column_types: Mapping[str, str],
    masked_columns: set[str],
    item: Mapping[str, object],
) -> DatasetAggregationFilter:
    column = _filter_column(dataset_ref, column_types, masked_columns, item)
    operator = _filter_operator(dataset_ref, item)
    if operator in DATASET_AGGREGATION_NUMERIC_OPERATORS:
        _require_numeric_column(dataset_ref, column_types, masked_columns, column, "filter")
    return {"column": column, "operator": operator, "value": str(item.get("value", ""))}


def _filter_column(
    dataset_ref: str,
    column_types: Mapping[str, str],
    masked_columns: set[str],
    item: Mapping[str, object],
) -> str:
    column = item.get("column")
    if not isinstance(column, str) or not column:
        raise ValidationFailed("dataset aggregation filter requires a column")
    _require_column(dataset_ref, column_types, masked_columns, column, "filter")
    return column


def _filter_operator(dataset_ref: str, item: Mapping[str, object]) -> DatasetAggregationOperator:
    operator = item.get("operator")
    if not isinstance(operator, str) or operator not in DATASET_AGGREGATION_OPERATORS:
        raise ValidationFailed(
            "dataset aggregation filter operator is not supported",
            details={"datasetRef": dataset_ref, "operator": operator},
        )
    return cast(DatasetAggregationOperator, operator)


def _require_numeric_column(
    dataset_ref: str,
    column_types: Mapping[str, str],
    masked_columns: set[str],
    column: str,
    source: str,
) -> None:
    _require_column(dataset_ref, column_types, masked_columns, column, source)
    if column_types[column] not in DATASET_AGGREGATION_NUMERIC_TYPES:
        raise ValidationFailed(
            "dataset aggregation requires a numeric column",
            details={"datasetRef": dataset_ref, "column": column, "source": source},
        )


def _require_column(
    dataset_ref: str,
    column_types: Mapping[str, str],
    masked_columns: set[str],
    column: str,
    source: str,
) -> None:
    if column not in column_types:
        raise ValidationFailed(
            "dataset aggregation references missing column",
            details={"datasetRef": dataset_ref, "column": column, "source": source},
        )
    if column in masked_columns:
        raise PermissionDenied(
            "dataset aggregation references masked column",
            details={"datasetRef": dataset_ref, "column": column, "source": source},
        )


def _metric_name(item: Mapping[str, object], default: str) -> str:
    name = item.get("name")
    return name if isinstance(name, str) and name else default


def _matches_filters(row: TabularRow, filters: Sequence[DatasetAggregationFilter]) -> bool:
    return all(_matches_filter(row, item) for item in filters)


def _matches_filter(row: TabularRow, item: DatasetAggregationFilter) -> bool:
    raw = row.get(item["column"])
    if item["operator"] in DATASET_AGGREGATION_NUMERIC_OPERATORS:
        return _matches_numeric(raw, item["operator"], item["value"])
    value = _cell_text(raw)
    target = item["value"]
    if item["operator"] == "eq":
        return value == target
    if item["operator"] == "neq":
        return value != target
    return target.lower() in value.lower()


def _matches_numeric(raw: object, operator: str, target: str) -> bool:
    left = _numeric_value(raw)
    right = _numeric_value(target)
    if left is None or right is None:
        return False
    return _compare_numeric(left, operator, right)


def _compare_numeric(left: float, operator: str, right: float) -> bool:
    if operator == "gt":
        return left > right
    if operator == "gte":
        return left >= right
    if operator == "lt":
        return left < right
    return left <= right


def _group_rows(rows: Sequence[TabularRow], plan: DatasetAggregationPlan) -> dict[str, _GroupState]:
    groups: dict[str, _GroupState] = {}
    for row in rows:
        group_key = _group_key(row, plan.group_by)
        group = groups.setdefault(group_key, _new_group(row, plan))
        _add_group_metrics(group, row, plan.metrics)
    return groups


def _group_key(row: TabularRow, group_by: Sequence[str]) -> str:
    key = {column: row.get(column) for column in group_by}
    return json.dumps(key, sort_keys=True, default=str)


def _new_group(row: TabularRow, plan: DatasetAggregationPlan) -> _GroupState:
    metrics = {metric["name"]: _MetricState() for metric in plan.metrics}
    return _GroupState(key={column: row.get(column) for column in plan.group_by}, metrics=metrics)


def _add_group_metrics(
    group: _GroupState,
    row: TabularRow,
    metrics: Sequence[DatasetAggregationMetric],
) -> None:
    for metric in metrics:
        state = group.metrics[metric["name"]]
        if metric["function"] == "count":
            state.count += 1
            continue
        _add_numeric_metric(state, row.get(str(metric["property"])))


def _add_numeric_metric(state: _MetricState, value: object) -> None:
    numeric = _numeric_value(value)
    if numeric is None:
        return
    state.count += 1
    state.total += numeric
    state.min_value = numeric if state.min_value is None else min(state.min_value, numeric)
    state.max_value = numeric if state.max_value is None else max(state.max_value, numeric)


def _ordered_groups(groups: Sequence[_GroupState]) -> list[_GroupState]:
    return sorted(groups, key=lambda group: json.dumps(group.key, sort_keys=True, default=str))


def _shaped_group(
    group: _GroupState,
    metrics: Sequence[DatasetAggregationMetric],
) -> DatasetAggregationGroup:
    return {
        "key": group.key,
        "metrics": {metric["name"]: _metric_value(group.metrics[metric["name"]], metric) for metric in metrics},
    }


def _metric_value(state: _MetricState, metric: DatasetAggregationMetric) -> float | int | None:
    if metric["function"] == "count":
        return state.count
    if state.count == 0:
        return None
    if metric["function"] == "sum":
        return state.total
    if metric["function"] == "avg":
        return state.total / state.count
    return state.min_value if metric["function"] == "min" else state.max_value


def _numeric_value(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and value.strip() != "":
        try:
            parsed = float(value)
        except ValueError:
            return None
        return parsed if parsed == parsed else None
    return None


def _cell_text(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, dict | list):
        return json.dumps(value, sort_keys=True, default=str)
    return str(value)
