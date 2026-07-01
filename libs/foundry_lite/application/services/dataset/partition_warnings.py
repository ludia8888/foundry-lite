"""Application service helpers for partition warnings workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import DatasetStagedFile
from foundry_lite.application.services.dataset.transaction_models import (
    DatasetFinalizationCheck,
    DatasetFinalizationRequest,
)

MIN_HIGH_CARDINALITY_VALUES = 5
HIGH_CARDINALITY_RATIO = 0.8
ABSOLUTE_HIGH_CARDINALITY_VALUES = 1_000


def partition_layout_warnings(
    request: DatasetFinalizationRequest,
    validation: DatasetFinalizationCheck,
) -> list[dict[str, object]]:
    """Return non-blocking partition layout warnings for committed transaction evidence."""
    partition_spec = list(request.target.row["partition_spec"])
    if not partition_spec or validation.stats.row_count <= 0:
        return []
    warnings = [
        _warning_for_column(request, validation, column)
        for column in partition_spec
        if _is_high_cardinality(_distinct_partition_values(request.staged_files, column), validation.stats.row_count)
    ]
    return [warning for warning in warnings if warning is not None]


def _warning_for_column(
    request: DatasetFinalizationRequest,
    validation: DatasetFinalizationCheck,
    column: str,
) -> dict[str, object] | None:
    values = _distinct_partition_values(request.staged_files, column)
    if not values:
        return None
    ratio = round(len(values) / validation.stats.row_count, 4)
    return {
        "code": "high_cardinality_partition",
        "severity": "warning",
        "datasetRef": request.target.dataset_ref,
        "column": column,
        "partitionSpec": list(request.target.row["partition_spec"]),
        "uniquePartitionValueCount": len(values),
        "rowCount": validation.stats.row_count,
        "fileCount": len(request.staged_files),
        "uniqueToRowRatio": ratio,
        "recommendation": "Use lower-cardinality partition columns or rely on sort/order bounds for pruning.",
    }


def _is_high_cardinality(values: set[object], row_count: int) -> bool:
    value_count = len(values)
    if value_count >= ABSOLUTE_HIGH_CARDINALITY_VALUES:
        return True
    return value_count >= MIN_HIGH_CARDINALITY_VALUES and value_count / row_count >= HIGH_CARDINALITY_RATIO


def _distinct_partition_values(staged_files: Sequence[DatasetStagedFile], column: str) -> set[object]:
    values: set[object] = set()
    for staged_file in staged_files:
        value = _partition_value(staged_file.partition_values, column)
        if value is not None:
            values.add(value)
    return values


def _partition_value(partition_values: Mapping[str, object] | None, column: str) -> object | None:
    if partition_values is None or column not in partition_values:
        return None
    value = partition_values[column]
    return _hashable_partition_value(value)


def _hashable_partition_value(value: object) -> object:
    if isinstance(value, list):
        return tuple(_hashable_partition_value(item) for item in value)
    if isinstance(value, dict):
        return tuple(sorted((str(key), _hashable_partition_value(item)) for key, item in value.items()))
    return value
