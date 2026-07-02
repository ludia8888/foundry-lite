"""Manifest IO, verification, filter, and snapshot-property helpers for the Iceberg adapter."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from foundry_lite.application.ports import (
    DatasetManifest,
    DatasetManifestColumnStats,
    DatasetManifestFile,
    DatasetStagedFile,
)

_VERSION_PROP = "foundry.version_id"


_DATASET_PROP = "foundry.dataset_ref"


_BRANCH_PROP = "foundry.branch"


_SCHEMA_PROP = "foundry.schema_hash"


_CREATED_AT_PROP = "foundry.created_at"


def _expected_files(sidecar: Mapping[str, object]) -> Mapping[str, DatasetManifestFile]:
    """Expected files."""
    files = sidecar.get("files")
    if not isinstance(files, list):
        raise ValueError("Iceberg Foundry sidecar manifest does not contain a file list")
    expected: dict[str, DatasetManifestFile] = {}
    for raw in files:
        if not isinstance(raw, dict):
            raise ValueError("Iceberg Foundry sidecar manifest contains a malformed file entry")
        raw_file = cast(Mapping[str, object], raw)
        entry: DatasetManifestFile = {
            "uri": str(raw_file["uri"]),
            "format": str(raw_file["format"]),
            "row_count": int(str(raw_file["row_count"])),
            "byte_size": int(str(raw_file["byte_size"])),
            "content_hash": str(raw_file["content_hash"]),
        }
        _copy_raw_manifest_metadata(entry, raw_file)
        expected[entry["uri"]] = entry
    return expected


def _matching_expected_files(
    expected_files: Mapping[str, DatasetManifestFile],
    partition_filter: Mapping[str, object] | None,
) -> list[DatasetManifestFile]:
    files = list(expected_files.values())
    if partition_filter is None:
        return files
    return [file for file in files if _matches_manifest_filter(file, partition_filter)]


def _copy_raw_manifest_metadata(entry: DatasetManifestFile, raw_file: Mapping[str, object]) -> None:
    partition_values = raw_file.get("partition_values")
    if isinstance(partition_values, dict):
        entry["partition_values"] = dict(partition_values)
    column_stats = _raw_column_stats(raw_file.get("column_stats"))
    if column_stats:
        entry["column_stats"] = column_stats
    sort_bounds = _raw_column_stats(raw_file.get("sort_bounds"))
    if sort_bounds:
        entry["sort_bounds"] = sort_bounds


def _raw_column_stats(raw: object) -> dict[str, DatasetManifestColumnStats]:
    if not isinstance(raw, dict):
        return {}
    parsed: dict[str, DatasetManifestColumnStats] = {}
    for column, stats in raw.items():
        if isinstance(stats, dict):
            parsed[str(column)] = _raw_stats(stats)
    return parsed


def _raw_stats(raw: Mapping[str, object]) -> DatasetManifestColumnStats:
    stats: DatasetManifestColumnStats = {}
    if "min" in raw:
        stats["min"] = raw["min"]
    if "max" in raw:
        stats["max"] = raw["max"]
    if "null_count" in raw:
        stats["null_count"] = int(str(raw["null_count"]))
    return stats


def _copy_expected_manifest_metadata(
    entry: DatasetManifestFile,
    expected: DatasetManifestFile | None,
) -> None:
    if expected is None:
        return
    if "partition_values" in expected:
        entry["partition_values"] = expected["partition_values"]
    if "column_stats" in expected:
        entry["column_stats"] = expected["column_stats"]
    if "sort_bounds" in expected:
        entry["sort_bounds"] = expected["sort_bounds"]


def _verify_downloaded_snapshot_file(target: Path, manifest_file: DatasetManifestFile) -> None:
    if target.stat().st_size != manifest_file["byte_size"]:
        raise ValueError(f"Iceberg data file byte size mismatch: {manifest_file['uri']}")
    if _path_sha256(target) != manifest_file["content_hash"]:
        raise ValueError(f"Iceberg data file content hash mismatch: {manifest_file['uri']}")


def _path_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _matches_manifest_filter(
    manifest_file: DatasetManifestFile,
    partition_filter: Mapping[str, object],
) -> bool:
    return all(_file_might_match_value(manifest_file, key, value) for key, value in partition_filter.items())


def _file_might_match_value(manifest_file: DatasetManifestFile, key: str, value: object) -> bool:
    partition_values = manifest_file.get("partition_values") or {}
    if key in partition_values:
        return partition_values.get(key) == value
    stats = _stats_for_column(manifest_file, key)
    if stats is None:
        return True
    if value is None:
        return int(stats.get("null_count", 0)) > 0
    if int(stats.get("null_count", 0)) >= int(manifest_file.get("row_count", 0)):
        return False
    return _value_within_bounds(value, stats)


def _stats_for_column(manifest_file: DatasetManifestFile, key: str) -> DatasetManifestColumnStats | None:
    column_stats = manifest_file.get("column_stats") or {}
    sort_bounds = manifest_file.get("sort_bounds") or {}
    return column_stats.get(key) or sort_bounds.get(key)


def _value_within_bounds(value: object, stats: DatasetManifestColumnStats) -> bool:
    lower = stats.get("min")
    upper = stats.get("max")
    if lower is not None and _safe_less(value, lower):
        return False
    if upper is not None and _safe_greater(value, upper):
        return False
    return True


def _safe_less(left: object, right: object) -> bool:
    try:
        return bool(cast(Any, left) < cast(Any, right))
    except TypeError:
        return False


def _safe_greater(left: object, right: object) -> bool:
    try:
        return bool(cast(Any, left) > cast(Any, right))
    except TypeError:
        return False


def _part_row_count(staged_file: DatasetStagedFile, total_row_count: int) -> int:
    return total_row_count if staged_file.row_count is None else staged_file.row_count


def _staged_files_row_count(staged_files: Sequence[DatasetStagedFile], total_row_count: int) -> int:
    return sum(_part_row_count(staged_file, total_row_count) for staged_file in staged_files)


def _uploaded_row_count(staged_file: DatasetStagedFile, actual_row_count: int) -> int:
    if staged_file.row_count is not None and staged_file.row_count != actual_row_count:
        raise ValueError("dataset staged file row count does not match parquet content")
    return actual_row_count


def _files_by_uri(files: Sequence[DatasetManifestFile]) -> Mapping[str, DatasetManifestFile]:
    return {file["uri"]: file for file in files}


def _snapshot_properties(
    version_id: str,
    dataset_ref: str,
    branch: str,
    schema_hash: str,
    created_at: str,
) -> dict[str, str]:
    return {
        _VERSION_PROP: version_id,
        _DATASET_PROP: dataset_ref,
        _BRANCH_PROP: branch,
        _SCHEMA_PROP: schema_hash,
        _CREATED_AT_PROP: created_at,
    }


def _dataset_manifest(
    version_id: str,
    dataset_ref: str,
    branch: str,
    schema_hash: str,
    files: list[DatasetManifestFile],
    created_at: str,
    profile_name: str,
) -> DatasetManifest:
    return {
        "version_id": version_id,
        "dataset": dataset_ref,
        "branch": branch,
        "schema_hash": schema_hash,
        "files": files,
        "created_at": created_at,
        "storage_profile": profile_name,
    }


def _snapshot_token(files: list[DatasetManifestFile]) -> str:
    """Snapshot token."""
    if len(files) == 1:
        return files[0]["content_hash"]
    parts = "|".join(sorted(f"{f['content_hash']}:{f['byte_size']}:{f['row_count']}" for f in files))
    return hashlib.sha256(parts.encode()).hexdigest()
