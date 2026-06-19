"""Skew measurement helpers for proactive observability detectors."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median

from foundry_lite.application.ports import RuntimeRow


@dataclass(frozen=True)
class SkewMeasurement:
    """Largest row and ratio after expected seasonal partitions are ignored."""

    largest_row: RuntimeRow
    largest_partition: str | None
    largest_size_bytes: float
    median_size_bytes: float
    skew_ratio: float


def skew_measurement(rows: Sequence[RuntimeRow], seasonal_partitions: set[str]) -> SkewMeasurement | None:
    measured = _measured_rows(rows, seasonal_partitions)
    if len(measured) < 2:
        return None
    largest_row, largest = max(measured, key=lambda item: item[1])
    middle = median(value for _, value in measured)
    return SkewMeasurement(
        largest_row=largest_row,
        largest_partition=partition_key(largest_row),
        largest_size_bytes=largest,
        median_size_bytes=middle,
        skew_ratio=largest / middle if middle else 0,
    )


def partition_key(row: RuntimeRow) -> str | None:
    value = row.get("partition_key") or row.get("partition") or row.get("object_key")
    return str(value) if value is not None else None


def _measured_rows(rows: Sequence[RuntimeRow], seasonal_partitions: set[str]) -> list[tuple[RuntimeRow, float]]:
    return [
        (row, value)
        for row in rows
        if partition_key(row) not in seasonal_partitions
        if (value := _row_size(row)) is not None and value > 0
    ]


def _row_size(row: RuntimeRow) -> float | None:
    value = row.get("partition_size_bytes") or row.get("file_size_bytes")
    return float(value) if isinstance(value, int | float) else None
