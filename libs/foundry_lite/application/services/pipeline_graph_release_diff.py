"""Deterministic, reviewer-facing Pipeline graph change evidence."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence, Sized
from typing import cast

from foundry_lite.application.primitives import _json_hash
from foundry_lite.application.services.pipeline_graph_model import pipeline_graph_fingerprint
from foundry_lite.application.services.pipeline_graph_normalizer import normalize_pipeline_graph

JsonObject = dict[str, object]


def pipeline_graph_release_diff(
    base: Mapping[str, object],
    candidate: Mapping[str, object],
) -> dict[str, object]:
    """Return complete resource-level graph changes without exposing arbitrary config."""

    base_graph = normalize_pipeline_graph(base)
    candidate_graph = normalize_pipeline_graph(candidate)
    items = [
        *_row_changes("pipeline_node", base_graph["nodes"], candidate_graph["nodes"], _node_key),
        *_row_changes("pipeline_edge", base_graph["edges"], candidate_graph["edges"], _edge_key),
        *_section_changes(base_graph, candidate_graph),
    ]
    return {
        "changed": bool(items),
        "baseFingerprint": pipeline_graph_fingerprint(base_graph),
        "graphFingerprint": pipeline_graph_fingerprint(candidate_graph),
        "summary": _summary(base_graph, candidate_graph, items),
        "items": items,
    }


def _row_changes(
    resource_type: str,
    before_rows: Sequence[Mapping[str, object]],
    after_rows: Sequence[Mapping[str, object]],
    key_reader: Callable[[Mapping[str, object]], str],
) -> list[dict[str, object]]:
    before = {key_reader(row): row for row in before_rows}
    after = {key_reader(row): row for row in after_rows}
    items: list[dict[str, object]] = []
    for resource_id in sorted(before.keys() | after.keys()):
        change_type = _change_type(before.get(resource_id), after.get(resource_id))
        if change_type is not None:
            items.append(
                _change_item(
                    change_type,
                    resource_type,
                    resource_id,
                    before.get(resource_id),
                    after.get(resource_id),
                )
            )
    return items


def _section_changes(base: Mapping[str, object], candidate: Mapping[str, object]) -> list[dict[str, object]]:
    sections = (
        ("pipeline_output_contract", "outputContract"),
        ("pipeline_tests", "tests"),
        ("pipeline_schedule", "schedule"),
        ("pipeline_layout", "layout"),
    )
    return [
        _change_item("modified", resource_type, key, base.get(key), candidate.get(key))
        for resource_type, key in sections
        if base.get(key) != candidate.get(key)
    ]


def _change_type(before: object, after: object) -> str | None:
    if before is None:
        return "added"
    if after is None:
        return "removed"
    return "modified" if before != after else None


def _change_item(
    change_type: str,
    resource_type: str,
    resource_id: str,
    before: object,
    after: object,
) -> dict[str, object]:
    return {
        "changeType": change_type,
        "resourceType": resource_type,
        "resourceId": resource_id,
        "summary": f"{resource_type} {resource_id} {change_type}",
        "beforeFingerprint": _json_hash({"value": before}),
        "afterFingerprint": _json_hash({"value": after}),
    }


def _summary(
    base: Mapping[str, object],
    candidate: Mapping[str, object],
    items: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    return {
        "nodeCount": len(cast(Sized, base["nodes"])),
        "newNodeCount": len(cast(Sized, candidate["nodes"])),
        "edgeCount": len(cast(Sized, base["edges"])),
        "newEdgeCount": len(cast(Sized, candidate["edges"])),
        "addedCount": sum(item.get("changeType") == "added" for item in items),
        "modifiedCount": sum(item.get("changeType") == "modified" for item in items),
        "removedCount": sum(item.get("changeType") == "removed" for item in items),
        "totalChangeCount": len(items),
    }


def _node_key(row: Mapping[str, object]) -> str:
    return str(row.get("id") or "unknown-node")


def _edge_key(row: Mapping[str, object]) -> str:
    return str(
        row.get("id")
        or ":".join(str(row.get(key) or "") for key in ("sourceNodeId", "sourcePortId", "targetNodeId", "targetPortId"))
    )


__all__ = ["pipeline_graph_release_diff"]
