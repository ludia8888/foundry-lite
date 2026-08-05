"""Bounded Dataset and lineage projections for official-name MCP tools."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from foundry_lite.application.services.aip.fde_tool_result import FdePlatformToolError
from foundry_lite.domain.context import RequestContext


class FdeLineageReader(Protocol):
    def lineage_for_resource(
        self,
        resource_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> Sequence[Mapping[str, object]]: ...


def lineage_graph(
    service: FdeLineageReader,
    ctx: RequestContext,
    resource_id: str,
    max_depth: int,
) -> dict[str, object]:
    queue: list[tuple[str, int]] = [(resource_id, 0)]
    visited: set[str] = set()
    nodes: dict[str, dict[str, object]] = {resource_id: {"resourceId": resource_id, "resourceType": "unknown"}}
    edges: dict[str, dict[str, object]] = {}
    while queue and len(edges) < 200:
        current, depth = queue.pop(0)
        if current in visited or depth >= max_depth:
            continue
        visited.add(current)
        for raw in service.lineage_for_resource(current, ctx=ctx):
            edge = _public_lineage_edge(raw)
            edge_id = str(edge["id"])
            edges[edge_id] = edge
            for node in _lineage_nodes(edge):
                node_id = str(node["resourceId"])
                nodes[node_id] = node
                if node_id not in visited:
                    queue.append((node_id, depth + 1))
            if len(edges) >= 200:
                break
    return {
        "rootResourceId": resource_id,
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
        "maxDepth": max_depth,
        "isTruncated": len(edges) >= 200,
    }


def dataset_tool_result(tool_id: str, inspection: Mapping[str, object]) -> dict[str, object]:
    identity = {
        "datasetRef": inspection.get("dataset"),
        "datasetId": inspection.get("dataset_id"),
        "version": inspection.get("version"),
    }
    manifest = _mapping(inspection.get("manifest"), "manifest")
    if tool_id == "get_foundry_dataset_schema":
        return {**identity, "schema": inspection.get("schema")}
    if tool_id == "list_dataset_files":
        files = _mapping_items(manifest.get("files"))
        return {**identity, "files": files, "count": len(files), "isManifestBounded": True}
    return {**identity, **_manifest_statistics(manifest)}


def _public_lineage_edge(edge: Mapping[str, object]) -> dict[str, object]:
    return {
        "id": edge.get("id"),
        "fromResourceType": edge.get("from_resource_type"),
        "fromResourceId": edge.get("from_resource_id"),
        "toResourceType": edge.get("to_resource_type"),
        "toResourceId": edge.get("to_resource_id"),
        "relation": edge.get("relation"),
        "createdByRunId": edge.get("created_by_run_id"),
        "createdAt": edge.get("created_at"),
    }


def _lineage_nodes(edge: Mapping[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    return (
        {"resourceId": edge.get("fromResourceId"), "resourceType": edge.get("fromResourceType")},
        {"resourceId": edge.get("toResourceId"), "resourceType": edge.get("toResourceType")},
    )


def _manifest_statistics(manifest: Mapping[str, object]) -> dict[str, object]:
    files = _mapping_items(manifest.get("files"))
    partitions = [item.get("partition_values", {}) for item in files if item.get("partition_values")]
    return {
        "rowCount": sum(_non_negative_int(item.get("row_count")) for item in files),
        "byteSize": sum(_non_negative_int(item.get("byte_size")) for item in files),
        "fileCount": len(files),
        "partitions": partitions,
        "columnStats": [item.get("column_stats", {}) for item in files],
        "schemaHash": manifest.get("schema_hash"),
        "storageProfile": manifest.get("storage_profile"),
    }


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise FdePlatformToolError("schema_invalid", f"{field} must be an object")
    return {str(name): item for name, item in value.items()}


def _mapping_items(value: object) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise FdePlatformToolError("schema_invalid", "expected a list of objects")
    if not all(isinstance(item, Mapping) for item in value):
        raise FdePlatformToolError("schema_invalid", "expected a list of objects")
    return [{str(name): field for name, field in item.items()} for item in value if isinstance(item, Mapping)]


def _non_negative_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
