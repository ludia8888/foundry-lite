from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest
from foundry_lite.application.ports import DatasetAggregationPlan, DatasetSchemaJson, TabularRow
from foundry_lite.application.services.dataset.aggregation import (
    DATASET_AGGREGATION_GROUP_LIMIT,
    aggregate_dataset_rows,
    build_dataset_aggregation_plan,
)
from foundry_lite.domain.errors import PermissionDenied, ValidationFailed

DATASET_REF = "clean.orders"
VERSION_ID = "dsv-analytics"


def test_dataset_aggregation_groups_rows_and_computes_all_metric_functions() -> None:
    plan = build_dataset_aggregation_plan(
        DATASET_REF,
        _schema(),
        set(),
        group_by=("region",),
        filters=(
            {"column": "status", "operator": "eq", "value": "PENDING"},
            {"column": "amount", "operator": "gte", "value": "50"},
        ),
        select=(
            {"function": "count"},
            {"function": "sum", "property": "amount"},
            {"function": "avg", "property": "amount", "name": "averageAmount"},
            {"function": "min", "property": "amount"},
            {"function": "max", "property": "amount"},
        ),
    )

    result = aggregate_dataset_rows(
        DATASET_REF,
        VERSION_ID,
        [
            {"order_id": "O-1001", "region": "APAC", "status": "PENDING", "amount": 100},
            {"order_id": "O-1002", "region": "APAC", "status": "PENDING", "amount": "150"},
            {"order_id": "O-1003", "region": "EMEA", "status": "PENDING", "amount": 200.0},
            {"order_id": "O-1004", "region": "EMEA", "status": "APPROVED", "amount": 300},
            {"order_id": "O-1005", "region": "NA", "status": "PENDING", "amount": True},
            {"order_id": "O-1006", "region": "NA", "status": "PENDING", "amount": "not-a-number"},
        ],
        plan,
    )

    assert plan.metrics == (
        {"name": "count", "function": "count", "property": None},
        {"name": "sum_amount", "function": "sum", "property": "amount"},
        {"name": "averageAmount", "function": "avg", "property": "amount"},
        {"name": "min_amount", "function": "min", "property": "amount"},
        {"name": "max_amount", "function": "max", "property": "amount"},
    )
    assert result == {
        "datasetRef": DATASET_REF,
        "versionId": VERSION_ID,
        "rowCount": 6,
        "filteredRowCount": 3,
        "groups": [
            {
                "key": {"region": "APAC"},
                "metrics": {
                    "count": 2,
                    "sum_amount": 250.0,
                    "averageAmount": 125.0,
                    "min_amount": 100.0,
                    "max_amount": 150.0,
                },
            },
            {
                "key": {"region": "EMEA"},
                "metrics": {
                    "count": 1,
                    "sum_amount": 200.0,
                    "averageAmount": 200.0,
                    "min_amount": 200.0,
                    "max_amount": 200.0,
                },
            },
        ],
        "totalGroups": 2,
    }


@pytest.mark.parametrize(
    ("filter_item", "expected_order_ids"),
    [
        ({"column": "status", "operator": "neq", "value": "APPROVED"}, ["O-1", "O-3", "O-4", "O-5"]),
        ({"column": "tags", "operator": "contains", "value": "fragile"}, ["O-1"]),
        ({"column": "metadata", "operator": "contains", "value": "priority"}, ["O-2"]),
        ({"column": "notes", "operator": "contains", "value": "nul"}, ["O-3"]),
        ({"column": "amount", "operator": "gt", "value": "10"}, ["O-2", "O-3"]),
        ({"column": "amount", "operator": "lt", "value": "20"}, ["O-1"]),
        ({"column": "amount", "operator": "lte", "value": "20"}, ["O-1", "O-2"]),
        ({"column": "amount", "operator": "gte", "value": "not-a-number"}, []),
    ],
)
def test_dataset_aggregation_filters_cover_text_json_null_and_numeric_operators(
    filter_item: Mapping[str, object],
    expected_order_ids: list[str],
) -> None:
    result = aggregate_dataset_rows(
        DATASET_REF,
        VERSION_ID,
        _filter_rows(),
        _count_plan(group_by=("order_id",), filters=(filter_item,)),
    )

    assert [group["key"]["order_id"] for group in result["groups"]] == expected_order_ids
    assert [group["metrics"]["count"] for group in result["groups"]] == [1] * len(expected_order_ids)


def test_dataset_aggregation_returns_null_numeric_metric_when_group_has_no_numeric_values() -> None:
    result = aggregate_dataset_rows(
        DATASET_REF,
        VERSION_ID,
        [{"order_id": "O-1", "region": "APAC", "amount": "NaN"}],
        _metric_plan(group_by=("region",), select=({"function": "sum", "property": "amount"},)),
    )

    assert result["groups"] == [{"key": {"region": "APAC"}, "metrics": {"sum_amount": None}}]


def test_dataset_aggregation_enforces_group_limit() -> None:
    with pytest.raises(ValidationFailed, match="exceeds the group limit") as exc_info:
        aggregate_dataset_rows(
            DATASET_REF,
            VERSION_ID,
            [{"order_id": f"O-{index}", "region": "APAC"} for index in range(DATASET_AGGREGATION_GROUP_LIMIT + 1)],
            _count_plan(group_by=("order_id",)),
        )

    assert exc_info.value.details == {"datasetRef": DATASET_REF, "groupLimit": DATASET_AGGREGATION_GROUP_LIMIT}


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"group_by": ("region", "status", "order_id"), "select": ({"function": "count"},)}, "at most two"),
        ({"group_by": ("region", "region"), "select": ({"function": "count"},)}, "must be unique"),
        ({"group_by": (), "select": ()}, "at least one metric"),
        (
            {
                "group_by": (),
                "select": (
                    {"function": "count", "name": "value"},
                    {"function": "sum", "property": "amount", "name": "value"},
                ),
            },
            "metric names must be unique",
        ),
        ({"group_by": (), "select": ({"function": "median", "property": "amount"},)}, "metric function"),
        ({"group_by": (), "select": ({"function": "count", "property": "amount"},)}, "count metric"),
        ({"group_by": (), "select": ({"function": "sum"},)}, "requires a property"),
        ({"group_by": (), "select": ({"function": "sum", "property": "status"},)}, "numeric column"),
    ],
)
def test_dataset_aggregation_rejects_invalid_group_and_metric_requests(
    kwargs: Mapping[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationFailed, match=message):
        build_dataset_aggregation_plan(DATASET_REF, _schema(), set(), filters=(), **kwargs)


@pytest.mark.parametrize(
    ("filters", "message"),
    [
        (({"column": "status", "operator": "eq", "value": "PENDING"},) * 11, "at most ten filters"),
        (({"operator": "eq", "value": "PENDING"},), "filter requires a column"),
        (({"column": "status", "operator": "starts_with", "value": "P"},), "operator is not supported"),
        (({"column": "status", "operator": "gt", "value": "PENDING"},), "numeric column"),
        (({"column": "missing", "operator": "eq", "value": "PENDING"},), "missing column"),
    ],
)
def test_dataset_aggregation_rejects_invalid_filters(
    filters: Sequence[Mapping[str, object]],
    message: str,
) -> None:
    with pytest.raises(ValidationFailed, match=message):
        build_dataset_aggregation_plan(
            DATASET_REF,
            _schema(),
            set(),
            group_by=(),
            filters=filters,
            select=({"function": "count"},),
        )


def test_dataset_aggregation_rejects_masked_columns_in_group_filter_and_metric() -> None:
    for kwargs in (
        {"group_by": ("amount",), "filters": (), "select": ({"function": "count"},)},
        {
            "group_by": (),
            "filters": ({"column": "amount", "operator": "eq", "value": "100"},),
            "select": ({"function": "count"},),
        },
        {"group_by": (), "filters": (), "select": ({"function": "sum", "property": "amount"},)},
    ):
        with pytest.raises(PermissionDenied, match="masked column") as exc_info:
            build_dataset_aggregation_plan(DATASET_REF, _schema(), {"amount"}, **kwargs)
        assert exc_info.value.details == {"datasetRef": DATASET_REF, "column": "amount", "source": _source(kwargs)}


def _source(kwargs: Mapping[str, object]) -> str:
    if kwargs["group_by"]:
        return "groupBy"
    if kwargs["filters"]:
        return "filter"
    return "sum"


def _count_plan(
    *,
    group_by: Sequence[str] = (),
    filters: Sequence[Mapping[str, object]] = (),
) -> DatasetAggregationPlan:
    return build_dataset_aggregation_plan(
        DATASET_REF,
        _schema(),
        set(),
        group_by=group_by,
        filters=filters,
        select=({"function": "count"},),
    )


def _metric_plan(
    *,
    group_by: Sequence[str] = (),
    select: Sequence[Mapping[str, object]],
) -> DatasetAggregationPlan:
    return build_dataset_aggregation_plan(
        DATASET_REF,
        _schema(),
        set(),
        group_by=group_by,
        filters=(),
        select=select,
    )


def _schema() -> DatasetSchemaJson:
    return {
        "columns": [
            {"name": "order_id", "type": "string"},
            {"name": "region", "type": "string"},
            {"name": "status", "type": "string"},
            {"name": "amount", "type": "number"},
            {"name": "tags", "type": "string"},
            {"name": "metadata", "type": "string"},
            {"name": "notes", "type": "string"},
        ]
    }


def _filter_rows() -> list[TabularRow]:
    return [
        {"order_id": "O-1", "status": "PENDING", "amount": 10, "tags": ["fragile"], "metadata": {}, "notes": "ready"},
        {
            "order_id": "O-2",
            "status": "APPROVED",
            "amount": 20,
            "tags": [],
            "metadata": {"priority": "high"},
            "notes": "ready",
        },
        {"order_id": "O-3", "status": "PENDING", "amount": 30, "tags": [], "metadata": {}, "notes": None},
        {"order_id": "O-4", "status": "PENDING", "amount": True, "tags": [], "metadata": {}, "notes": "bool amount"},
        {"order_id": "O-5", "status": "PENDING", "amount": "", "tags": [], "metadata": {}, "notes": "blank amount"},
    ]
