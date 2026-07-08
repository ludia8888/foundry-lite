"""Dataset aggregation payload contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict

DatasetAggregationFunction = Literal["count", "sum", "avg", "min", "max"]
DatasetAggregationOperator = Literal["eq", "neq", "contains", "gt", "gte", "lt", "lte"]


class DatasetAggregationMetric(TypedDict):
    """One requested dataset aggregate output column."""

    name: str
    function: DatasetAggregationFunction
    property: str | None


class DatasetAggregationFilter(TypedDict):
    """One AND-ed keep-rows predicate for dataset analytics."""

    column: str
    operator: DatasetAggregationOperator
    value: str


@dataclass(frozen=True)
class DatasetAggregationPlan:
    """Validated dataset aggregate request ready for a compute adapter."""

    group_by: tuple[str, ...]
    metrics: tuple[DatasetAggregationMetric, ...]
    filters: tuple[DatasetAggregationFilter, ...]
    group_limit: int


class DatasetAggregationGroup(TypedDict):
    """One aggregate group with key columns and metric values."""

    key: dict[str, object]
    metrics: dict[str, float | int | None]


class DatasetAggregationComputeResult(TypedDict):
    """Compute-layer aggregate result before API-facing dataset evidence is attached."""

    rowCount: int
    filteredRowCount: int
    groups: list[DatasetAggregationGroup]
    totalGroups: int


class DatasetAggregationResult(TypedDict):
    """Server-side dataset aggregate result and source-version evidence."""

    datasetRef: str
    versionId: str
    rowCount: int
    filteredRowCount: int
    groups: list[DatasetAggregationGroup]
    totalGroups: int
