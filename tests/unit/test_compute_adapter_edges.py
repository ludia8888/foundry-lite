from __future__ import annotations

from typing import cast

import pytest
from foundry_lite.application.ports.compute_adapter import ParquetFieldType
from foundry_lite.application.ports.dataset_aggregation import (
    DatasetAggregationFilter,
    DatasetAggregationMetric,
    DatasetAggregationPlan,
)
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure.adapters import compute as compute_module
from foundry_lite.infrastructure.adapters.compute import (
    DuckDBComputeAdapter,
    _dataset_aggregation_filter_sql,
    _dataset_aggregation_group_by_sql,
    _dataset_aggregation_metric_value,
    _numeric_operator_sql,
    _tabular_row,
    _validated_parquet_field_types,
)


def test_compute_aggregate_returns_an_empty_result_without_input_files() -> None:
    plan = DatasetAggregationPlan(
        group_by=(),
        metrics=({"name": "count", "function": "count", "property": None},),
        filters=(),
        group_limit=100,
    )

    assert DuckDBComputeAdapter().aggregate_parquet([], plan) == {
        "rowCount": 0,
        "filteredRowCount": 0,
        "groups": [],
        "totalGroups": 0,
    }


@pytest.mark.parametrize(
    "field_types",
    [
        {"missing": "string"},
        {"id": "decimal"},
    ],
)
def test_compute_typed_parquet_rejects_unknown_fields_and_types(
    field_types: dict[str, str],
) -> None:
    with pytest.raises(ValidationFailed) as captured:
        _validated_parquet_field_types(
            ["id"],
            cast(dict[str, ParquetFieldType], field_types),
        )

    assert captured.value.details["reason"] == "invalid_field_types"


def test_compute_tabular_row_rejects_non_string_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(compute_module, "_json_ready", lambda _value: {1: "value"})

    with pytest.raises(ValidationFailed, match="keys must be strings"):
        _tabular_row({1: "value"})


def test_compute_aggregate_metric_values_fail_closed_on_invalid_driver_results() -> None:
    count = cast(DatasetAggregationMetric, {"name": "count", "function": "count", "property": None})
    total = cast(DatasetAggregationMetric, {"name": "total", "function": "sum", "property": "amount"})

    assert _dataset_aggregation_metric_value(total, None) is None
    with pytest.raises(ValidationFailed, match="non-integer"):
        _dataset_aggregation_metric_value(count, True)
    with pytest.raises(ValidationFailed, match="non-numeric"):
        _dataset_aggregation_metric_value(total, "invalid")


@pytest.mark.parametrize(
    ("operator", "expected"),
    [
        ("gt", ">"),
        ("gte", ">="),
        ("lt", "<"),
        ("lte", "<="),
    ],
)
def test_compute_numeric_filter_operators_are_explicit_and_parameterized(
    operator: str,
    expected: str,
) -> None:
    typed_operator = cast(
        DatasetAggregationFilter,
        {"column": "amount", "operator": operator, "value": "10"},
    )
    sql, params = _dataset_aggregation_filter_sql(typed_operator)

    assert f" {expected} " in sql
    assert params == ["10"]
    assert _numeric_operator_sql(operator) == expected


@pytest.mark.parametrize(
    ("operator", "fragment"),
    [
        ("eq", " = ?"),
        ("neq", " != ?"),
        ("contains", "contains("),
    ],
)
def test_compute_text_filter_operators_are_parameterized(operator: str, fragment: str) -> None:
    filter_item = cast(
        DatasetAggregationFilter,
        {"column": "status", "operator": operator, "value": "open"},
    )

    sql, params = _dataset_aggregation_filter_sql(filter_item)

    assert fragment in sql
    assert params == ["open"]


def test_compute_aggregate_without_grouping_omits_group_by_clause() -> None:
    assert _dataset_aggregation_group_by_sql(()) == ""
