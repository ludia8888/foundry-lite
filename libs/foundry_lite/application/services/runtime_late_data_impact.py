from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Literal, TypedDict

from foundry_lite.application.ports import (
    LineageEdgeRow,
    RuntimeRow,
    RuntimeRunLink,
    RuntimeRunSnapshot,
    RuntimeRunType,
)

LineageLookup = Callable[[str], list[LineageEdgeRow]]
RuntimeRunGroup = Literal["syncRuns", "transformRuns", "indexRuns", "materializationRuns"]


class _GraphParts(TypedDict):
    nodes: list[dict[str, object]]
    edges: list[dict[str, object]]
    affectedRuns: list[RuntimeRunLink]


def object_late_data_badge(
    snapshot: RuntimeRunSnapshot,
    *,
    source_dataset_version_id: str,
    object_type_api_name: str,
) -> dict[str, object] | None:
    event_id = _cdc_event_id(source_dataset_version_id)
    if event_id is None:
        return None
    row = _latest_late_index_run_for_event(snapshot, event_id, object_type_api_name)
    if row is None:
        return None
    cursor = _mapping(row.get("cursor"))
    return {
        "type": "late_data",
        "status": "LATE_REQUIRES_REPROCESS",
        "label": "Late data reprocessed",
        "isImpacted": True,
        "sourceEventId": event_id,
        "sourceRun": _run_link("index", row, "cdc_event_indexed_from", "cdc_event", event_id),
        "lateEventIds": _string_list(cursor.get("lateEventIds")),
        "maxEventTimeLagSeconds": cursor.get("maxEventTimeLagSeconds"),
    }


def materialization_late_data_badge(detail: Mapping[str, object]) -> dict[str, object] | None:
    reopen = _mapping(detail.get("reopen"))
    if reopen.get("isReopened") is not True:
        return None
    return {
        "type": "late_data",
        "status": "LATE_REQUIRES_REPROCESS",
        "label": "Late data reprocessed",
        "isImpacted": True,
        "resourceType": "materialization",
        "apiName": detail.get("apiName"),
        "sourceIndexRunId": reopen.get("sourceIndexRunId"),
        "lateEventIds": _string_list(reopen.get("lateEventIds")),
        "maxEventTimeLagSeconds": reopen.get("maxEventTimeLagSeconds"),
    }


def downstream_impact_graph(
    row: RuntimeRow,
    dataset_transaction: RuntimeRow | None,
    snapshot: RuntimeRunSnapshot,
    lineage_lookup: LineageLookup,
) -> dict[str, object] | None:
    roots = _impact_roots(row, dataset_transaction)
    badges = _impact_badges(row, dataset_transaction)
    graph = _lineage_graph(roots, snapshot, lineage_lookup)
    if not roots and not badges and not graph["edges"]:
        return None
    affected_runs = [*graph["affectedRuns"], *_badge_run_links(row, badges)]
    return {
        "status": "IMPACTED" if roots or badges else "NONE",
        "rootResources": roots,
        "nodes": graph["nodes"],
        "edges": graph["edges"],
        "affectedRuns": _dedupe_run_links(affected_runs),
        "lateDataBadges": badges,
    }


def _impact_roots(row: RuntimeRow, dataset_transaction: RuntimeRow | None) -> list[dict[str, object]]:
    roots = _reprocessing_roots(dataset_transaction)
    materialization = _materialization_detail(row, dataset_transaction)
    reopen = _mapping(materialization.get("reopen"))
    if reopen.get("isReopened") is True:
        roots.extend(_materialization_reopen_roots(reopen))
    return _dedupe_resources(roots)


def _impact_badges(row: RuntimeRow, dataset_transaction: RuntimeRow | None) -> list[dict[str, object]]:
    materialization = _materialization_detail(row, dataset_transaction)
    badge = materialization_late_data_badge(materialization)
    if badge is None:
        return []
    return [{**badge, "runId": row.get("id")}]


def _reprocessing_roots(dataset_transaction: RuntimeRow | None) -> list[dict[str, object]]:
    metadata = _metadata(dataset_transaction)
    plan = _mapping(metadata.get("lateDataReprocessingPlan"))
    roots = [_resource(row, "accepted_late_data") for row in _sequence(plan.get("affectedClosedOutputs"))]
    previous = plan.get("previousCommittedDatasetVersionId")
    if isinstance(previous, str) and previous:
        roots.append({"resourceType": "dataset_version", "resourceId": previous, "reason": "accepted_late_data"})
    return [root for root in roots if root]


def _materialization_reopen_roots(reopen: Mapping[str, object]) -> list[dict[str, object]]:
    roots: list[dict[str, object]] = []
    previous = reopen.get("previousDatasetVersionId")
    source_index = reopen.get("sourceIndexRunId")
    if isinstance(previous, str) and previous:
        roots.append({"resourceType": "dataset_version", "resourceId": previous, "reason": "late_data_reprocess"})
    if isinstance(source_index, str) and source_index:
        roots.append({"resourceType": "index_run", "resourceId": source_index, "reason": "late_data_source"})
    return roots


def _lineage_graph(
    roots: Sequence[Mapping[str, object]],
    snapshot: RuntimeRunSnapshot,
    lineage_lookup: LineageLookup,
) -> _GraphParts:
    nodes = {_node_id(root): _node(root, is_root=True) for root in roots}
    edges: list[dict[str, object]] = []
    runs: list[RuntimeRunLink] = []
    queue = [(root["resourceId"], 0) for root in roots if root.get("resourceType") == "dataset_version"]
    _walk_lineage(queue, nodes, edges, runs, snapshot, lineage_lookup)
    return {"nodes": list(nodes.values()), "edges": edges, "affectedRuns": _dedupe_run_links(runs)}


def _walk_lineage(
    queue: list[tuple[object, int]],
    nodes: dict[str, dict[str, object]],
    edges: list[dict[str, object]],
    runs: list[RuntimeRunLink],
    snapshot: RuntimeRunSnapshot,
    lineage_lookup: LineageLookup,
) -> None:
    visited: set[str] = set()
    while queue:
        resource_id, depth = queue.pop(0)
        if not isinstance(resource_id, str) or resource_id in visited or depth >= 5:
            continue
        visited.add(resource_id)
        _extend_lineage(resource_id, depth, nodes, edges, runs, snapshot, lineage_lookup, queue)


def _extend_lineage(
    resource_id: str,
    depth: int,
    nodes: dict[str, dict[str, object]],
    edges: list[dict[str, object]],
    runs: list[RuntimeRunLink],
    snapshot: RuntimeRunSnapshot,
    lineage_lookup: LineageLookup,
    queue: list[tuple[object, int]],
) -> None:
    for edge in lineage_lookup(resource_id):
        if edge["from_resource_id"] != resource_id:
            continue
        target = {"resourceType": edge["to_resource_type"], "resourceId": edge["to_resource_id"]}
        nodes.setdefault(_node_id(target), _node(target, is_root=False))
        edges.append(_impact_edge(edge))
        _append_run_link(runs, snapshot, edge)
        if edge["to_resource_type"] == "dataset_version":
            queue.append((edge["to_resource_id"], depth + 1))


def _append_run_link(runs: list[RuntimeRunLink], snapshot: RuntimeRunSnapshot, edge: LineageEdgeRow) -> None:
    run_type, row = _run_for_id(snapshot, edge["created_by_run_id"])
    if run_type is None or row is None:
        return
    runs.append(_run_link(run_type, row, edge["relation"], edge["to_resource_type"], edge["to_resource_id"]))


def _badge_run_links(row: RuntimeRow, badges: Sequence[Mapping[str, object]]) -> list[RuntimeRunLink]:
    if not badges or row.get("id") is None:
        return []
    return [_run_link("materialization", row, "late_data_reprocessed", "materialization", str(row.get("api_name")))]


def _materialization_detail(row: RuntimeRow, dataset_transaction: RuntimeRow | None) -> Mapping[str, object]:
    metadata = _metadata(dataset_transaction)
    detail = metadata.get("materializationDetail")
    if isinstance(detail, Mapping):
        return detail
    fallback = row.get("object_store_watermark") or row.get("source_cursor")
    return {"apiName": row.get("api_name"), "watermark": fallback, "reopen": {"isReopened": False}}


def _latest_late_index_run_for_event(
    snapshot: RuntimeRunSnapshot,
    event_id: str,
    object_type_api_name: str,
) -> RuntimeRow | None:
    rows = [
        row
        for row in snapshot["indexRuns"]
        if row.get("object_type_api_name") == object_type_api_name and _row_indexes_late_event(row, event_id)
    ]
    return _latest_runtime_row(rows)


def _row_indexes_late_event(row: RuntimeRow, event_id: str) -> bool:
    cursor = _mapping(row.get("cursor"))
    counts = _mapping(cursor.get("lateDataStatusCounts"))
    return _int_count(counts.get("LATE_REQUIRES_REPROCESS")) > 0 and event_id in _string_list(
        cursor.get("lateEventIds")
    )


def _latest_runtime_row(rows: Sequence[RuntimeRow]) -> RuntimeRow | None:
    if not rows:
        return None
    return sorted(rows, key=lambda row: (str(row.get("completed_at") or ""), str(row.get("id") or "")))[-1]


def _resource(value: object, reason: str) -> dict[str, object]:
    row = _mapping(value)
    resource_type = row.get("resourceType")
    resource_id = row.get("resourceId")
    if not isinstance(resource_type, str) or not isinstance(resource_id, str):
        return {}
    return {"resourceType": resource_type, "resourceId": resource_id, "reason": reason}


def _node(resource: Mapping[str, object], *, is_root: bool) -> dict[str, object]:
    return {
        "id": _node_id(resource),
        "resourceType": resource.get("resourceType"),
        "resourceId": resource.get("resourceId"),
        "isRoot": is_root,
    }


def _impact_edge(edge: LineageEdgeRow) -> dict[str, object]:
    return {
        "from": f"{edge['from_resource_type']}:{edge['from_resource_id']}",
        "to": f"{edge['to_resource_type']}:{edge['to_resource_id']}",
        "relation": edge["relation"],
        "createdByRunId": edge["created_by_run_id"],
    }


def _node_id(resource: Mapping[str, object]) -> str:
    return f"{resource.get('resourceType')}:{resource.get('resourceId')}"


def _run_for_id(snapshot: RuntimeRunSnapshot, run_id: str) -> tuple[RuntimeRunType | None, RuntimeRow | None]:
    for run_type, group in _run_groups():
        row = next((item for item in _rows_for_run_group(snapshot, group) if item.get("id") == run_id), None)
        if row is not None:
            return run_type, row
    return None, None


def _run_groups() -> tuple[tuple[RuntimeRunType, RuntimeRunGroup], ...]:
    return (
        ("sync", "syncRuns"),
        ("transform", "transformRuns"),
        ("index", "indexRuns"),
        ("materialization", "materializationRuns"),
    )


def _run_link(
    run_type: RuntimeRunType,
    row: RuntimeRow,
    relation: str,
    resource_type: str,
    resource_id: str,
) -> RuntimeRunLink:
    run_id = str(row["id"])
    return {
        "runType": run_type,
        "runId": run_id,
        "relation": relation,
        "resourceType": resource_type,
        "resourceId": resource_id,
        "status": _row_status(row),
        "operationPath": f"/api/operations/runs/{run_type}/{run_id}",
    }


def _rows_for_run_group(snapshot: RuntimeRunSnapshot, group: RuntimeRunGroup) -> list[RuntimeRow]:
    if group == "syncRuns":
        return snapshot["syncRuns"]
    if group == "transformRuns":
        return snapshot["transformRuns"]
    if group == "indexRuns":
        return snapshot["indexRuns"]
    return snapshot["materializationRuns"]


def _row_status(row: RuntimeRow) -> str | None:
    value = row.get("status") or row.get("decision")
    return value if isinstance(value, str) else None


def _metadata(dataset_transaction: RuntimeRow | None) -> Mapping[str, object]:
    if dataset_transaction is None:
        return {}
    metadata = dataset_transaction.get("metadata")
    return metadata if isinstance(metadata, Mapping) else {}


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, Sequence) and not isinstance(value, str | bytes) else ()


def _string_list(value: object) -> list[str]:
    return [item for item in _sequence(value) if isinstance(item, str)]


def _int_count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _cdc_event_id(source_dataset_version_id: str) -> str | None:
    event_id = source_dataset_version_id.removeprefix("cdc:")
    return event_id if event_id != source_dataset_version_id and event_id else None


def _dedupe_resources(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    seen: set[tuple[object, object]] = set()
    result: list[dict[str, object]] = []
    for row in rows:
        key = (row.get("resourceType"), row.get("resourceId"))
        if key not in seen:
            seen.add(key)
            result.append(dict(row))
    return result


def _dedupe_run_links(links: Sequence[RuntimeRunLink]) -> list[RuntimeRunLink]:
    seen: set[tuple[str, str, str]] = set()
    result: list[RuntimeRunLink] = []
    for link in links:
        key = (link["runType"], link["runId"], link["relation"])
        if key not in seen:
            seen.add(key)
            result.append(link)
    return result
