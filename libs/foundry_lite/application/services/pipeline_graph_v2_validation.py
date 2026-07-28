"""Descriptor and named-port validation for canonical Pipeline Builder v2 graphs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.services.pipeline_graph_contracts import (
    MAX_PIPELINE_EDGES,
    MAX_PIPELINE_NODES,
    JsonObject,
    PipelineConfigFieldDescriptor,
    PipelineConfigValueKind,
    PipelineGraphV2,
    PipelineNodeDescriptor,
    PipelineNodeKind,
    PipelineV2Edge,
    PipelineV2Node,
    pipeline_node_descriptor,
)
from foundry_lite.application.services.pipeline_graph_normalizer import normalize_pipeline_graph
from foundry_lite.application.services.pipeline_graph_v2_topology import pipeline_topology_errors
from foundry_lite.application.services.pipeline_trained_model_contracts import imported_trained_model_refs
from foundry_lite.domain.errors import ValidationFailed


def validate_pipeline_graph_v2(graph: Mapping[str, object]) -> JsonObject:
    nodes = _object_rows(graph.get("nodes"), "nodes")
    edges = _object_rows(graph.get("edges"), "edges")
    errors = _limit_errors(nodes, edges)
    errors.extend(_node_shape_errors(nodes))
    errors.extend(_edge_shape_errors(edges))
    if errors:
        return _result(errors, _shape_warnings(nodes))
    canonical = normalize_pipeline_graph(graph)
    errors.extend(_descriptor_errors(canonical["nodes"]))
    errors.extend(pipeline_topology_errors(canonical))
    errors.extend(_operation_errors(canonical))
    return _result(errors, _graph_warnings(canonical), canonical)


def _result(
    errors: list[JsonObject],
    warnings: list[JsonObject],
    canonical: PipelineGraphV2 | None = None,
) -> JsonObject:
    result: JsonObject = {"valid": not errors, "errors": errors, "warnings": warnings}
    if canonical is not None:
        result["normalizedGraph"] = canonical
    return result


def _limit_errors(
    nodes: Sequence[Mapping[str, object]],
    edges: Sequence[Mapping[str, object]],
) -> list[JsonObject]:
    errors: list[JsonObject] = []
    if len(nodes) > MAX_PIPELINE_NODES:
        errors.append({"code": "too_many_nodes", "limit": MAX_PIPELINE_NODES, "actual": len(nodes)})
    if len(edges) > MAX_PIPELINE_EDGES:
        errors.append({"code": "too_many_edges", "limit": MAX_PIPELINE_EDGES, "actual": len(edges)})
    output_count = sum(1 for node in nodes if node.get("kind") == PipelineNodeKind.OUTPUT.value)
    if output_count < 1:
        errors.append({"code": "output_node_count", "minimum": 1, "actual": output_count})
    return errors


def _node_shape_errors(nodes: Sequence[Mapping[str, object]]) -> list[JsonObject]:
    seen: set[str] = set()
    errors: list[JsonObject] = []
    for node in nodes:
        node_id = node.get("id")
        errors.extend(_node_identity_errors(node, node_id, seen))
        errors.extend(_node_contract_shape_errors(node, node_id))
        if isinstance(node_id, str) and node_id:
            seen.add(node_id)
    return errors


def _node_identity_errors(
    node: Mapping[str, object],
    node_id: object,
    seen: set[str],
) -> list[JsonObject]:
    if not isinstance(node_id, str) or not node_id:
        return [{"code": "node_id_required", "node": dict(node)}]
    if node_id in seen:
        return [{"code": "duplicate_node_id", "nodeId": node_id}]
    return []


def _node_contract_shape_errors(node: Mapping[str, object], node_id: object) -> list[JsonObject]:
    errors: list[JsonObject] = []
    if node.get("kind") not in {kind.value for kind in PipelineNodeKind}:
        errors.append({"code": "unsupported_node_kind", "nodeId": str(node_id), "kind": node.get("kind")})
    if not _is_non_empty_text(node.get("descriptorId")):
        errors.append({"code": "node_descriptor_required", "nodeId": str(node_id)})
    if not _is_positive_integer(node.get("specVersion")):
        errors.append({"code": "node_spec_version_required", "nodeId": str(node_id)})
    if not isinstance(node.get("config"), Mapping):
        errors.append({"code": "node_config_required", "nodeId": str(node_id)})
    if "data" in node:
        errors.append({"code": "legacy_node_data_not_allowed", "nodeId": str(node_id)})
    return errors


def _edge_shape_errors(edges: Sequence[Mapping[str, object]]) -> list[JsonObject]:
    seen: set[str] = set()
    errors: list[JsonObject] = []
    for edge in edges:
        edge_id = edge.get("id")
        if not _is_non_empty_text(edge_id):
            errors.append({"code": "edge_id_required", "edge": dict(edge)})
        elif str(edge_id) in seen:
            errors.append({"code": "duplicate_edge_id", "edgeId": edge_id})
        else:
            seen.add(str(edge_id))
        errors.extend(_edge_field_errors(edge))
    return errors


def _edge_field_errors(edge: Mapping[str, object]) -> list[JsonObject]:
    errors: list[JsonObject] = []
    for field in ("sourceNodeId", "sourcePortId", "targetNodeId", "targetPortId"):
        if not _is_non_empty_text(edge.get(field)):
            errors.append({"code": "edge_field_required", "field": field, "edge": dict(edge)})
    return errors


def _descriptor_errors(nodes: Sequence[PipelineV2Node]) -> list[JsonObject]:
    errors: list[JsonObject] = []
    for node in nodes:
        descriptor = pipeline_node_descriptor(node["descriptorId"], node["specVersion"])
        if descriptor is None:
            errors.append(_missing_descriptor_error(node))
            continue
        if node["kind"] != descriptor.node_kind.value:
            errors.append(_kind_mismatch_error(node, descriptor))
        errors.extend(_config_errors(node, descriptor))
    return errors


def _missing_descriptor_error(node: PipelineV2Node) -> JsonObject:
    return {
        "code": "node_descriptor_not_found",
        "nodeId": node["id"],
        "descriptorId": node["descriptorId"],
        "specVersion": node["specVersion"],
    }


def _kind_mismatch_error(node: PipelineV2Node, descriptor: PipelineNodeDescriptor) -> JsonObject:
    return {
        "code": "node_kind_mismatch",
        "nodeId": node["id"],
        "expected": descriptor.node_kind.value,
        "actual": node["kind"],
    }


def _config_errors(node: PipelineV2Node, descriptor: PipelineNodeDescriptor) -> list[JsonObject]:
    errors: list[JsonObject] = []
    for field in descriptor.config_fields:
        value = node["config"].get(field.field_name)
        if field.is_required and _is_missing_required_value(value):
            errors.append({"code": "node_config_required", "nodeId": node["id"], "field": field.field_name})
            continue
        if value is not None and not _matches_config_kind(value, field.value_kind):
            errors.append(_config_type_error(node, field))
            continue
        if value is not None and field.allowed_values and value not in field.allowed_values:
            errors.append(_config_value_error(node, field, value))
    return errors


def _config_type_error(node: PipelineV2Node, field: PipelineConfigFieldDescriptor) -> JsonObject:
    return {
        "code": "node_config_type",
        "nodeId": node["id"],
        "field": field.field_name,
        "expected": field.value_kind.value,
    }


def _config_value_error(
    node: PipelineV2Node,
    field: PipelineConfigFieldDescriptor,
    value: object,
) -> JsonObject:
    return {
        "code": "node_config_value",
        "nodeId": node["id"],
        "field": field.field_name,
        "actual": value,
        "allowed": list(field.allowed_values),
    }


def _operation_errors(graph: PipelineGraphV2) -> list[JsonObject]:
    nodes = {node["id"]: node for node in graph["nodes"]}
    errors: list[JsonObject] = []
    for node in graph["nodes"]:
        if node["descriptorId"] == "transform.join":
            errors.extend(_join_schema_errors(node, nodes, graph["edges"]))
        if node["descriptorId"] == "transform.union":
            errors.extend(_minimum_input_errors(node, graph["edges"], minimum=2))
            errors.extend(_union_schema_errors(node, nodes, graph["edges"]))
        if node["descriptorId"] == "transform.trained_model":
            errors.extend(_trained_model_import_errors(node, graph))
    return errors


def _trained_model_import_errors(node: PipelineV2Node, graph: PipelineGraphV2) -> list[JsonObject]:
    model_ref = node["config"].get("modelRef")
    if not isinstance(model_ref, str) or model_ref in imported_trained_model_refs(graph):
        return []
    return [
        {
            "code": "trained_model_not_imported",
            "nodeId": node["id"],
            "modelRef": model_ref,
        }
    ]


def _minimum_input_errors(
    node: PipelineV2Node,
    edges: Sequence[PipelineV2Edge],
    *,
    minimum: int,
) -> list[JsonObject]:
    actual = sum(1 for edge in edges if edge["targetNodeId"] == node["id"])
    if actual >= minimum:
        return []
    return [{"code": "input_count", "nodeId": node["id"], "minimum": minimum, "actual": actual}]


def _join_schema_errors(
    node: PipelineV2Node,
    nodes: Mapping[str, PipelineV2Node],
    edges: Sequence[PipelineV2Edge],
) -> list[JsonObject]:
    errors: list[JsonObject] = []
    for port, key_name in (("left", "leftKey"), ("right", "rightKey")):
        source = _source_node_for_port(node["id"], port, nodes, edges)
        key = node["config"].get(key_name)
        if source is None or not isinstance(key, str):
            continue
        columns = _schema_column_names(source)
        if columns is not None and key not in columns:
            errors.append(_join_key_missing(node["id"], port, source["id"], key))
    return errors


def _source_node_for_port(
    node_id: str,
    port_id: str,
    nodes: Mapping[str, PipelineV2Node],
    edges: Sequence[PipelineV2Edge],
) -> PipelineV2Node | None:
    for edge in edges:
        if edge["targetNodeId"] == node_id and edge["targetPortId"] == port_id:
            return nodes.get(edge["sourceNodeId"])
    return None


def _join_key_missing(node_id: str, port: str, source_id: str, key: str) -> JsonObject:
    return {"code": "join_key_missing", "nodeId": node_id, "role": port, "source": source_id, "column": key}


def _union_schema_errors(
    node: PipelineV2Node,
    nodes: Mapping[str, PipelineV2Node],
    edges: Sequence[PipelineV2Edge],
) -> list[JsonObject]:
    signatures: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    for edge in edges:
        if edge["targetNodeId"] != node["id"] or edge["targetPortId"] != "inputs":
            continue
        source = nodes.get(edge["sourceNodeId"])
        signature = _schema_signature(source) if source is not None else None
        if signature is not None:
            signatures.append((edge["sourceNodeId"], signature))
    if len(signatures) < 2 or all(item[1] == signatures[0][1] for item in signatures[1:]):
        return []
    return [{"code": "union_schema_mismatch", "nodeId": node["id"]}]


def _schema_column_names(node: PipelineV2Node) -> set[str] | None:
    signature = _schema_signature(node)
    if signature is None:
        return None
    return {name for name, _column_type in signature}


def _schema_signature(node: PipelineV2Node) -> tuple[tuple[str, str], ...] | None:
    schema = node["config"].get("schema")
    if not isinstance(schema, list):
        return None
    columns: list[tuple[str, str]] = []
    for column in schema:
        if isinstance(column, Mapping):
            columns.append((str(column.get("name") or ""), str(column.get("type") or "").lower()))
    return tuple(columns)


def _graph_warnings(graph: PipelineGraphV2) -> list[JsonObject]:
    warnings = _shape_warnings(graph["nodes"])
    for node in graph["nodes"]:
        descriptor = pipeline_node_descriptor(node["descriptorId"], node["specVersion"])
        if descriptor is None:
            continue
        warning = _availability_warning(node, descriptor)
        if warning is not None:
            warnings.append(warning)
    return warnings


def _availability_warning(
    node: PipelineV2Node,
    descriptor: PipelineNodeDescriptor,
) -> JsonObject | None:
    if descriptor.availability.value == "validation_only":
        return {
            "code": "node_runtime_unavailable",
            "nodeId": node["id"],
            "runtimeCapability": descriptor.runtime_capability,
        }
    if descriptor.availability.value == "governed_candidate":
        return {
            "code": "node_commits_governed_candidate_only",
            "nodeId": node["id"],
            "runtimeCapability": descriptor.runtime_capability,
            "servingAssetCreated": False,
            "promotionRequired": True,
        }
    return None


def _shape_warnings(nodes: Sequence[Mapping[str, object]]) -> list[JsonObject]:
    return [{"code": "top_level_schema_normalized", "nodeId": node.get("id")} for node in nodes if "schema" in node]


def _is_missing_required_value(value: object) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _matches_config_kind(value: object, expected: PipelineConfigValueKind) -> bool:
    if expected == PipelineConfigValueKind.STRING:
        return _is_non_empty_text(value)
    if expected == PipelineConfigValueKind.INTEGER:
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == PipelineConfigValueKind.BOOLEAN:
        return isinstance(value, bool)
    if expected == PipelineConfigValueKind.ARRAY:
        return isinstance(value, list)
    if expected == PipelineConfigValueKind.OBJECT:
        return isinstance(value, Mapping)
    return False


def _is_non_empty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_positive_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _object_rows(value: object, field: str) -> list[JsonObject]:
    if not isinstance(value, list):
        raise ValidationFailed(f"pipeline graph {field} must be a list", details={"field": field})
    rows: list[JsonObject] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValidationFailed(f"pipeline graph {field} item must be an object", details={"index": index})
        rows.append({str(key): row_value for key, row_value in item.items()})
    return rows
