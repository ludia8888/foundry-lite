"""Infrastructure adapter implementation for dataset manifest metadata."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, cast

import pyarrow.parquet as pq
from pyarrow.lib import ArrowException

from foundry_lite.application.ports import DatasetManifestColumnStats, DatasetManifestFile
from foundry_lite.application.primitives import _json_ready


def parquet_manifest_file_metadata(
    manifest_file: DatasetManifestFile,
    parquet_path: Path,
    *,
    sort_order: Sequence[str] | None = None,
) -> DatasetManifestFile:
    enriched = dict(manifest_file)
    column_stats = parquet_column_stats(parquet_path)
    if column_stats:
        enriched["column_stats"] = column_stats
    sort_bounds = _sort_bounds(column_stats, sort_order or ())
    if sort_bounds:
        enriched["sort_bounds"] = sort_bounds
    return cast(DatasetManifestFile, enriched)


def parquet_column_stats(parquet_path: Path) -> dict[str, DatasetManifestColumnStats]:
    try:
        metadata = pq.ParquetFile(parquet_path).metadata
    except (ArrowException, OSError, ValueError):
        return {}
    merged: dict[str, DatasetManifestColumnStats] = {}
    for row_group_index in range(metadata.num_row_groups):
        row_group = metadata.row_group(row_group_index)
        for column_index in range(row_group.num_columns):
            column = row_group.column(column_index)
            stats = column.statistics
            if stats is None:
                continue
            column_name = str(column.path_in_schema)
            current = merged.setdefault(column_name, {"null_count": 0})
            current["null_count"] = int(current.get("null_count", 0)) + int(stats.null_count or 0)
            if stats.has_min_max:
                _merge_bound(current, "min", _json_scalar(stats.min), is_min=True)
                _merge_bound(current, "max", _json_scalar(stats.max), is_min=False)
    return merged


def _sort_bounds(
    column_stats: Mapping[str, DatasetManifestColumnStats],
    sort_order: Sequence[str],
) -> dict[str, DatasetManifestColumnStats]:
    bounds: dict[str, DatasetManifestColumnStats] = {}
    for column in sort_order:
        stats = column_stats.get(column)
        if stats is not None and ("min" in stats or "max" in stats):
            bounds[column] = cast(DatasetManifestColumnStats, dict(stats))
    return bounds


def _merge_bound(
    current: DatasetManifestColumnStats,
    key: Literal["min", "max"],
    value: object,
    *,
    is_min: bool,
) -> None:
    if value is None:
        return
    existing = current.get(key)
    if existing is None or _is_better_bound(value, existing, is_min=is_min):
        if key == "min":
            current["min"] = value
        else:
            current["max"] = value


def _is_better_bound(candidate: object, existing: object, *, is_min: bool) -> bool:
    try:
        left = cast(Any, candidate)
        right = cast(Any, existing)
        return bool(left < right) if is_min else bool(left > right)
    except TypeError:
        return False


def _json_scalar(value: object) -> object:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    ready = _json_ready(value)
    if isinstance(ready, float) and not math.isfinite(ready):
        return None
    if ready is None or isinstance(ready, str | int | float | bool):
        return ready
    return str(ready)
