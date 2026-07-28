"""Deterministic three-way merge for canonical Pipeline Graph v2 documents."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import TypedDict, cast

from foundry_lite.application.services.pipeline_graph_contracts import (
    PipelineGraphV2,
)

_MISSING = object()


class PipelineGraphMergeConflict(TypedDict):
    path: str
    kind: str


@dataclass(frozen=True)
class PipelineGraphMergeResult:
    graph: PipelineGraphV2
    conflicts: tuple[PipelineGraphMergeConflict, ...]


def merge_pipeline_graphs(
    base: PipelineGraphV2,
    ours: PipelineGraphV2,
    theirs: PipelineGraphV2,
) -> PipelineGraphMergeResult:
    conflicts: list[PipelineGraphMergeConflict] = []
    merged = _merge_value(base, ours, theirs, "$", conflicts)
    if not isinstance(merged, dict):
        conflicts.append({"path": "$", "kind": "document_shape"})
        merged = deepcopy(ours)
    return PipelineGraphMergeResult(
        graph=cast(PipelineGraphV2, merged),
        conflicts=tuple(_dedupe_conflicts(conflicts)),
    )


def _merge_value(
    base: object,
    ours: object,
    theirs: object,
    path: str,
    conflicts: list[PipelineGraphMergeConflict],
) -> object:
    if ours == theirs:
        return _copy_merge_value(ours)
    if ours == base:
        return _copy_merge_value(theirs)
    if theirs == base:
        return _copy_merge_value(ours)
    if _are_mappings(base, ours, theirs):
        return _merge_mapping(base, ours, theirs, path, conflicts)
    if _is_keyed_list_path(path) and _are_lists(base, ours, theirs):
        return _merge_keyed_list(base, ours, theirs, path, conflicts)
    conflicts.append({"path": path, "kind": "concurrent_change"})
    return _copy_merge_value(ours)


def _copy_merge_value(value: object) -> object:
    """Preserve the identity-based deletion sentinel while isolating real values."""
    return _MISSING if value is _MISSING else deepcopy(value)


def _merge_mapping(
    base: object,
    ours: object,
    theirs: object,
    path: str,
    conflicts: list[PipelineGraphMergeConflict],
) -> dict[str, object]:
    base_map = _mapping_or_empty(base)
    ours_map = _mapping_or_empty(ours)
    theirs_map = _mapping_or_empty(theirs)
    result: dict[str, object] = {}
    for key in sorted(set(base_map) | set(ours_map) | set(theirs_map)):
        value = _merge_value(
            base_map.get(key, _MISSING),
            ours_map.get(key, _MISSING),
            theirs_map.get(key, _MISSING),
            f"{path}.{key}",
            conflicts,
        )
        if value is not _MISSING:
            result[key] = value
    return result


def _merge_keyed_list(
    base: object,
    ours: object,
    theirs: object,
    path: str,
    conflicts: list[PipelineGraphMergeConflict],
) -> list[object]:
    base_items = _keyed_items(base)
    ours_items = _keyed_items(ours)
    theirs_items = _keyed_items(theirs)
    if base_items is None or ours_items is None or theirs_items is None:
        conflicts.append({"path": path, "kind": "missing_item_identity"})
        return deepcopy(cast(list[object], ours))
    result: list[object] = []
    for key in sorted(set(base_items) | set(ours_items) | set(theirs_items)):
        value = _merge_value(
            base_items.get(key, _MISSING),
            ours_items.get(key, _MISSING),
            theirs_items.get(key, _MISSING),
            f"{path}[{key}]",
            conflicts,
        )
        if value is not _MISSING:
            result.append(value)
    return result


def _keyed_items(value: object) -> dict[str, object] | None:
    if value is _MISSING:
        return {}
    if not isinstance(value, list):
        return None
    result: dict[str, object] = {}
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            return None
        result[str(item["id"])] = item
    return result


def _mapping_or_empty(value: object) -> dict[str, object]:
    if value is _MISSING:
        return {}
    return cast(dict[str, object], value)


def _are_mappings(*values: object) -> bool:
    base, ours, theirs = values
    return (base is _MISSING or isinstance(base, dict)) and isinstance(ours, dict) and isinstance(theirs, dict)


def _are_lists(*values: object) -> bool:
    base, ours, theirs = values
    return (base is _MISSING or isinstance(base, list)) and isinstance(ours, list) and isinstance(theirs, list)


def _is_keyed_list_path(path: str) -> bool:
    return path in {"$.nodes", "$.edges"}


def _dedupe_conflicts(
    conflicts: list[PipelineGraphMergeConflict],
) -> list[PipelineGraphMergeConflict]:
    seen: set[tuple[str, str]] = set()
    result: list[PipelineGraphMergeConflict] = []
    for conflict in conflicts:
        key = (conflict["path"], conflict["kind"])
        if key not in seen:
            seen.add(key)
            result.append(conflict)
    return result
