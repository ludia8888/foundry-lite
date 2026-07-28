"""Pure graph helpers shared by Pipeline Builder v1 and canonical v2."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

from foundry_lite.application.primitives import _json_ready
from foundry_lite.application.services.pipeline_graph_contracts import (
    DEFAULT_PIPELINE_PREVIEW_ROWS,
    MAX_PIPELINE_EDGES,
    MAX_PIPELINE_NODES,
    MAX_PIPELINE_PREVIEW_ROWS,
    MAX_PIPELINE_TESTS,
    PipelineArtifactKind,
    PipelineGraphV2,
    PipelineNodeDescriptor,
    PipelineNodeKind,
    pipeline_node_descriptor_payloads,
    pipeline_node_descriptors,
)
from foundry_lite.application.services.pipeline_graph_normalizer import (
    canonical_node_config,
    empty_pipeline_graph_v2,
    normalize_join_type,
    normalize_pipeline_graph,
    pipeline_graph_schema_version,
)
from foundry_lite.application.services.pipeline_graph_v2_validation import validate_pipeline_graph_v2
from foundry_lite.domain.errors import ValidationFailed

JsonObject = dict[str, object]

__all__ = [
    "DEFAULT_PIPELINE_PREVIEW_ROWS",
    "MAX_PIPELINE_EDGES",
    "MAX_PIPELINE_NODES",
    "MAX_PIPELINE_PREVIEW_ROWS",
    "MAX_PIPELINE_TESTS",
    "PipelineArtifactKind",
    "PipelineGraphV2",
    "PipelineNodeDescriptor",
    "PipelineNodeKind",
    "empty_pipeline_graph",
    "empty_pipeline_graph_v2",
    "normalize_pipeline_graph",
    "pipeline_graph_fingerprint",
    "pipeline_node_descriptor_payloads",
    "pipeline_node_descriptors",
    "validate_pipeline_graph",
]

PIPELINE_NODE_TYPES = frozenset(
    {
        "dataset",
        "sql",
        "python",
        "join",
        "union",
        "select_cast",
        "output_dataset",
    }
)


def empty_pipeline_graph() -> JsonObject:
    return {"nodes": [], "edges": [], "layout": {}, "outputContract": {"columns": []}, "tests": [], "schedule": None}


def pipeline_graph_fingerprint(graph: Mapping[str, object]) -> str:
    payload = json.dumps(_json_ready(dict(graph)), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_pipeline_graph(graph: Mapping[str, object]) -> JsonObject:
    if pipeline_graph_schema_version(graph) == 2:
        v2_result = validate_pipeline_graph_v2(graph)
        v2_result["fingerprint"] = pipeline_graph_fingerprint(graph)
        _add_normalized_fingerprint(v2_result)
        return v2_result
    nodes = graph_nodes(graph)
    edges = graph_edges(graph)
    errors = _basic_graph_errors(nodes, edges)
    errors.extend(_node_shape_errors(nodes))
    errors.extend(_edge_shape_errors(nodes, edges))
    errors.extend(_topology_errors(nodes, edges))
    errors.extend(_operation_config_errors(nodes, edges))
    errors.extend(_output_contract_errors(graph))
    result: JsonObject = {
        "valid": not errors,
        "errors": errors,
        "warnings": _graph_warnings(graph),
        "fingerprint": pipeline_graph_fingerprint(graph),
    }
    if not errors:
        result["normalizedGraph"] = normalize_pipeline_graph(graph)
        _add_normalized_fingerprint(result)
    return result


def _add_normalized_fingerprint(result: JsonObject) -> None:
    normalized = result.get("normalizedGraph")
    if isinstance(normalized, Mapping):
        result["normalizedFingerprint"] = pipeline_graph_fingerprint(normalized)


def graph_nodes(graph: Mapping[str, object]) -> list[JsonObject]:
    return _object_list(graph.get("nodes"), "nodes")


def graph_edges(graph: Mapping[str, object]) -> list[JsonObject]:
    return _object_list(graph.get("edges"), "edges")


def node_by_id(graph: Mapping[str, object], node_id: str) -> JsonObject:
    for node in graph_nodes(graph):
        if node.get("id") == node_id:
            return node
    raise ValidationFailed("pipeline graph node not found", details={"node_id": node_id})


def source_dataset_refs(graph: Mapping[str, object], node_id: str) -> list[str]:
    node_ref_by_id = _node_ref_by_id(graph_nodes(graph))
    refs: list[str] = []
    for edge in graph_edges(graph):
        if _edge_target_id(edge) != node_id:
            continue
        source_id = _edge_source_id(edge)
        ref = node_ref_by_id.get(source_id)
        if ref is not None:
            refs.append(ref)
    return sorted(refs)


def topological_node_ids(graph: Mapping[str, object]) -> list[str]:
    nodes = graph_nodes(graph)
    edges = graph_edges(graph)
    ids = [str(node["id"]) for node in nodes]
    incoming, outgoing = _topology_maps(ids, edges)
    ordered = _consume_topology(ids, incoming, outgoing)
    if len(ordered) != len(ids):
        raise ValidationFailed("pipeline graph contains a cycle", details={"node_count": len(ids)})
    return ordered


def _topology_maps(
    node_ids: Sequence[str],
    edges: Sequence[Mapping[str, object]],
) -> tuple[dict[str, int], dict[str, list[str]]]:
    incoming = {node_id: 0 for node_id in node_ids}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for edge in edges:
        source = _edge_source_id(edge)
        target = _edge_target_id(edge)
        incoming[target] += 1
        outgoing[source].append(target)
    return incoming, outgoing


def _consume_topology(
    node_ids: Sequence[str],
    incoming: dict[str, int],
    outgoing: Mapping[str, Sequence[str]],
) -> list[str]:
    ready = sorted(node_id for node_id in node_ids if incoming[node_id] == 0)
    ordered: list[str] = []
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        for target in sorted(outgoing[current]):
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
    return ordered


def node_data(node: Mapping[str, object]) -> JsonObject:
    return canonical_node_config(node)


def output_dataset_ref(graph: Mapping[str, object]) -> str | None:
    refs = output_dataset_refs(graph)
    return refs[0] if refs else None


def output_dataset_refs(graph: Mapping[str, object]) -> list[str]:
    refs: list[str] = []
    for node in graph_nodes(graph):
        is_output = node.get("type") == "output_dataset" or node.get("descriptorId") == "output.dataset"
        if not is_output:
            continue
        data = node_data(node)
        ref = data.get("datasetRef") or data.get("outputDatasetRef")
        if isinstance(ref, str) and ref:
            refs.append(ref)
    return refs


def output_contract_columns(graph: Mapping[str, object]) -> list[JsonObject]:
    contract = graph.get("outputContract", {})
    if not isinstance(contract, dict):
        return []
    return _object_list(contract.get("columns"), "outputContract.columns", required=False)


def bounded_preview_limit(limit: object | None) -> int:
    if limit is None:
        return DEFAULT_PIPELINE_PREVIEW_ROWS
    if not isinstance(limit, int):
        raise ValidationFailed("preview limit must be an integer", details={"limit": str(limit)})
    return max(1, min(limit, MAX_PIPELINE_PREVIEW_ROWS))


def _object_list(value: object, field: str, *, required: bool = True) -> list[JsonObject]:
    if value is None and not required:
        return []
    if not isinstance(value, list):
        raise ValidationFailed(f"pipeline graph {field} must be a list", details={"field": field})
    rows: list[JsonObject] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValidationFailed(f"pipeline graph {field} item must be an object", details={"index": index})
        rows.append({str(key): row_value for key, row_value in item.items()})
    return rows


def _basic_graph_errors(
    nodes: Sequence[Mapping[str, object]], edges: Sequence[Mapping[str, object]]
) -> list[JsonObject]:
    errors: list[JsonObject] = []
    if len(nodes) > MAX_PIPELINE_NODES:
        errors.append({"code": "too_many_nodes", "limit": MAX_PIPELINE_NODES, "actual": len(nodes)})
    if len(edges) > MAX_PIPELINE_EDGES:
        errors.append({"code": "too_many_edges", "limit": MAX_PIPELINE_EDGES, "actual": len(edges)})
    output_count = sum(1 for node in nodes if node.get("type") == "output_dataset")
    if output_count != 1:
        errors.append({"code": "output_dataset_count", "expected": 1, "actual": output_count})
    return errors


def _node_shape_errors(nodes: Sequence[Mapping[str, object]]) -> list[JsonObject]:
    seen: set[str] = set()
    errors: list[JsonObject] = []
    for node in nodes:
        node_id = node.get("id")
        node_type = node.get("type")
        if not isinstance(node_id, str) or not node_id:
            errors.append({"code": "node_id_required", "node": dict(node)})
            continue
        if node_id in seen:
            errors.append({"code": "duplicate_node_id", "nodeId": node_id})
        seen.add(node_id)
        if node_type not in PIPELINE_NODE_TYPES:
            errors.append({"code": "unsupported_node_type", "nodeId": node_id, "nodeType": str(node_type)})
    return errors


def _edge_shape_errors(
    nodes: Sequence[Mapping[str, object]], edges: Sequence[Mapping[str, object]]
) -> list[JsonObject]:
    node_ids = {str(node["id"]) for node in nodes if isinstance(node.get("id"), str)}
    errors: list[JsonObject] = []
    for edge in edges:
        source = edge.get("source")
        target = edge.get("target")
        if source not in node_ids:
            errors.append({"code": "dangling_edge_source", "edge": dict(edge)})
        if target not in node_ids:
            errors.append({"code": "dangling_edge_target", "edge": dict(edge)})
    return errors


def _topology_errors(nodes: Sequence[Mapping[str, object]], edges: Sequence[Mapping[str, object]]) -> list[JsonObject]:
    if _edge_shape_errors(nodes, edges):
        return []
    graph = {"nodes": list(nodes), "edges": list(edges)}
    try:
        topological_node_ids(graph)
    except ValidationFailed as exc:
        return [{"code": "cycle_detected", **exc.details}]
    return []


def _operation_config_errors(
    nodes: Sequence[Mapping[str, object]], edges: Sequence[Mapping[str, object]]
) -> list[JsonObject]:
    node_by_id_map = {str(node["id"]): node for node in nodes if isinstance(node.get("id"), str)}
    incoming = _incoming_edges_by_target(edges)
    errors: list[JsonObject] = []
    for node in nodes:
        if node.get("type") == "join":
            errors.extend(_join_node_errors(node, node_by_id_map, incoming.get(str(node["id"]), [])))
        if node.get("type") == "union":
            errors.extend(_union_node_errors(node, node_by_id_map, incoming.get(str(node["id"]), [])))
    return errors


def _join_node_errors(
    node: Mapping[str, object],
    nodes: Mapping[str, Mapping[str, object]],
    incoming_edges: Sequence[Mapping[str, object]],
) -> list[JsonObject]:
    node_id = str(node.get("id"))
    errors: list[JsonObject] = []
    if len(incoming_edges) != 2:
        errors.append({"code": "join_input_count", "nodeId": node_id, "expected": 2, "actual": len(incoming_edges)})
        return errors
    data = node_data(node)
    left_key = _text_config(data, "leftKey", "leftColumn", "leftOn")
    right_key = _text_config(data, "rightKey", "rightColumn", "rightOn")
    if left_key is None or right_key is None:
        return [{"code": "join_key_required", "nodeId": node_id}]
    errors.extend(_join_type_errors(node_id, data))
    errors.extend(_join_key_errors(node_id, "left", left_key, data, incoming_edges, nodes))
    errors.extend(_join_key_errors(node_id, "right", right_key, data, incoming_edges, nodes))
    return errors


def _union_node_errors(
    node: Mapping[str, object],
    nodes: Mapping[str, Mapping[str, object]],
    incoming_edges: Sequence[Mapping[str, object]],
) -> list[JsonObject]:
    node_id = str(node.get("id"))
    if len(incoming_edges) < 2:
        return [{"code": "union_input_count", "nodeId": node_id, "minimum": 2, "actual": len(incoming_edges)}]
    schemas = _known_input_schemas(nodes, incoming_edges)
    if len(schemas) < 2:
        return []
    expected_source, expected_schema = schemas[0]
    for source_id, schema in schemas[1:]:
        if schema != expected_schema:
            return [
                {
                    "code": "union_schema_mismatch",
                    "nodeId": node_id,
                    "expectedSource": expected_source,
                    "actualSource": source_id,
                }
            ]
    return []


def _output_contract_errors(graph: Mapping[str, object]) -> list[JsonObject]:
    columns = output_contract_columns(graph)
    seen: set[str] = set()
    errors: list[JsonObject] = []
    for column in columns:
        name = column.get("name")
        if not isinstance(name, str) or not name:
            errors.append({"code": "output_column_name_required", "column": dict(column)})
            continue
        if name in seen:
            errors.append({"code": "duplicate_output_column", "column": name})
        seen.add(name)
        if not column.get("type"):
            errors.append({"code": "output_column_type_required", "column": name})
    return errors


def _graph_warnings(graph: Mapping[str, object]) -> list[JsonObject]:
    warnings: list[JsonObject] = []
    if not output_contract_columns(graph):
        warnings.append({"code": "output_contract_empty"})
    if output_dataset_ref(graph) is None:
        warnings.append({"code": "output_dataset_ref_missing"})
    return warnings


def _edge_source_id(edge: Mapping[str, object]) -> str:
    return str(edge.get("sourceNodeId") or edge.get("source") or "")


def _edge_target_id(edge: Mapping[str, object]) -> str:
    return str(edge.get("targetNodeId") or edge.get("target") or "")


def _node_ref_by_id(nodes: Sequence[Mapping[str, object]]) -> dict[str, str]:
    refs: dict[str, str] = {}
    for node in nodes:
        data = node_data(node)
        ref = data.get("datasetRef") or data.get("outputDatasetRef")
        if isinstance(node.get("id"), str) and ref is not None:
            refs[str(node["id"])] = str(ref)
    return refs


def _incoming_edges_by_target(edges: Sequence[Mapping[str, object]]) -> dict[str, list[Mapping[str, object]]]:
    incoming: dict[str, list[Mapping[str, object]]] = {}
    for edge in edges:
        target = edge.get("target")
        if isinstance(target, str):
            incoming.setdefault(target, []).append(edge)
    return incoming


def _text_config(data: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _join_key_errors(
    node_id: str,
    role: str,
    key: str,
    data: Mapping[str, object],
    incoming_edges: Sequence[Mapping[str, object]],
    nodes: Mapping[str, Mapping[str, object]],
) -> list[JsonObject]:
    source_id = _input_source_id(data, incoming_edges, role, 0 if role == "left" else 1)
    if source_id is None or source_id not in nodes:
        return [{"code": "join_input_source_missing", "nodeId": node_id, "role": role}]
    columns = _schema_column_names(nodes[source_id])
    if columns is not None and key not in columns:
        return [{"code": "join_key_missing", "nodeId": node_id, "role": role, "source": source_id, "column": key}]
    return []


def _input_source_id(
    data: Mapping[str, object],
    incoming_edges: Sequence[Mapping[str, object]],
    role: str,
    fallback_index: int,
) -> str | None:
    configured = _text_config(data, f"{role}NodeId", f"{role}Source", f"{role}Input")
    if configured is not None:
        return configured
    for edge in incoming_edges:
        handle = str(edge.get("targetHandle") or edge.get("input") or edge.get("role") or "").lower()
        if role in handle and isinstance(edge.get("source"), str):
            return str(edge["source"])
    if fallback_index < len(incoming_edges) and isinstance(incoming_edges[fallback_index].get("source"), str):
        return str(incoming_edges[fallback_index]["source"])
    return None


def _join_type_errors(node_id: str, data: Mapping[str, object]) -> list[JsonObject]:
    value = data.get("joinType", "inner")
    if not isinstance(value, str):
        return [{"code": "join_type_invalid", "nodeId": node_id, "joinType": value}]
    normalized = normalize_join_type(value)
    if normalized in {"inner", "left", "right", "full outer"}:
        return []
    return [{"code": "join_type_invalid", "nodeId": node_id, "joinType": value}]


def _known_input_schemas(
    nodes: Mapping[str, Mapping[str, object]],
    incoming_edges: Sequence[Mapping[str, object]],
) -> list[tuple[str, tuple[tuple[str, str], ...]]]:
    schemas: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    for edge in incoming_edges:
        source = edge.get("source")
        if not isinstance(source, str) or source not in nodes:
            continue
        schema = _schema_signature(nodes[source])
        if schema is not None:
            schemas.append((source, schema))
    return schemas


def _schema_column_names(node: Mapping[str, object]) -> set[str] | None:
    schema = _schema_columns(node)
    if schema is None:
        return None
    return {str(column["name"]) for column in schema if isinstance(column.get("name"), str)}


def _schema_signature(node: Mapping[str, object]) -> tuple[tuple[str, str], ...] | None:
    schema = _schema_columns(node)
    if schema is None:
        return None
    return tuple((str(column.get("name")), str(column.get("type", "")).lower()) for column in schema)


def _schema_columns(node: Mapping[str, object]) -> list[JsonObject] | None:
    schema = node_data(node).get("schema")
    if not isinstance(schema, list):
        return None
    return [{str(key): value for key, value in column.items()} for column in schema if isinstance(column, dict)]
