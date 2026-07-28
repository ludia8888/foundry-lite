"""Pure v1/v2 normalization into the canonical Pipeline Builder v2 IR."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal, cast

from foundry_lite.application.services.pipeline_graph_contracts import (
    JsonObject,
    PipelineGraphV2,
    PipelineNodeDescriptor,
    PipelineNodeKind,
    PipelineV2Edge,
    PipelineV2Node,
    pipeline_node_descriptor,
)
from foundry_lite.domain.errors import ValidationFailed

LEGACY_DESCRIPTOR_BY_TYPE = {
    "dataset": "source.dataset",
    "sql": "transform.sql",
    "python": "transform.python",
    "join": "transform.join",
    "union": "transform.union",
    "select_cast": "transform.select_cast",
    "output_dataset": "output.dataset",
}


def empty_pipeline_graph_v2() -> PipelineGraphV2:
    return {
        "schemaVersion": 2,
        "nodes": [],
        "edges": [],
        "layout": {},
        "outputContract": {"columns": []},
        "tests": [],
        "schedule": None,
    }


def pipeline_graph_schema_version(graph: Mapping[str, object]) -> int:
    value = graph.get("schemaVersion", 1)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationFailed("pipeline graph schemaVersion must be an integer")
    if value not in {1, 2}:
        raise ValidationFailed("unsupported pipeline graph schemaVersion", details={"schemaVersion": value})
    return value


def normalize_pipeline_graph(graph: Mapping[str, object]) -> PipelineGraphV2:
    """Return a new canonical v2 graph without changing the persisted input."""

    version = pipeline_graph_schema_version(graph)
    raw_nodes = _object_rows(graph.get("nodes"), "nodes")
    raw_edges = _object_rows(graph.get("edges"), "edges")
    nodes = sorted((_normalize_node(node, version) for node in raw_nodes), key=lambda node: node["id"])
    descriptors = _descriptors_by_node_id(nodes)
    edge_drafts = _edge_drafts(raw_edges, raw_nodes, descriptors, version)
    return _normalized_graph(graph, nodes, _finalize_edges(edge_drafts), version)


def canonical_node_config(node: Mapping[str, object]) -> JsonObject:
    """Read legacy data/config while making top-level schema authoritative."""

    merged = _optional_node_object(node, "config")
    merged.update(_optional_node_object(node, "data"))
    if isinstance(node.get("schema"), list):
        merged["schema"] = _clone_value(node["schema"])
    join_type = merged.get("joinType")
    if isinstance(join_type, str):
        merged["joinType"] = normalize_join_type(join_type)
    return merged


def normalize_join_type(value: str) -> str:
    normalized = value.strip().lower().replace("_", " ").replace("-", " ")
    if normalized in {"full", "full outer", "fullouter"}:
        return "full outer"
    return normalized


def _normalize_node(node: Mapping[str, object], version: int) -> PipelineV2Node:
    node_id = _required_text(node.get("id"), "node id")
    descriptor_id = _descriptor_id(node, version)
    spec_version = _spec_version(node, version)
    descriptor = pipeline_node_descriptor(descriptor_id, spec_version)
    node_kind = _node_kind(node, descriptor, version)
    normalized = _clone_object(node) if version == 2 else {}
    normalized.update(
        {
            "id": node_id,
            "kind": node_kind,
            "descriptorId": descriptor_id,
            "specVersion": spec_version,
            "config": _canonical_descriptor_config(descriptor_id, canonical_node_config(node)),
        }
    )
    return cast(PipelineV2Node, normalized)


def _canonical_descriptor_config(descriptor_id: str, config: JsonObject) -> JsonObject:
    normalized = dict(config)
    if descriptor_id == "output.dataset" and not normalized.get("outputDatasetRef"):
        dataset_ref = normalized.get("datasetRef")
        if isinstance(dataset_ref, str) and dataset_ref.strip():
            normalized["outputDatasetRef"] = dataset_ref.strip()
    return normalized


def _descriptor_id(node: Mapping[str, object], version: int) -> str:
    if version == 2:
        return _required_text(node.get("descriptorId"), "node descriptorId")
    node_type = _required_text(node.get("type"), "node type")
    descriptor_id = LEGACY_DESCRIPTOR_BY_TYPE.get(node_type)
    if descriptor_id is None:
        raise ValidationFailed("unsupported legacy pipeline node type", details={"nodeType": node_type})
    return descriptor_id


def _spec_version(node: Mapping[str, object], version: int) -> int:
    value = node.get("specVersion", 1) if version == 2 else 1
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValidationFailed("pipeline node specVersion must be a positive integer")
    return value


def _node_kind(
    node: Mapping[str, object],
    descriptor: PipelineNodeDescriptor | None,
    version: int,
) -> Literal["source", "transform", "output"]:
    if version == 2:
        return _literal_node_kind(_required_text(node.get("kind"), "node kind"))
    if descriptor is None:
        raise ValidationFailed("pipeline node descriptor not found")
    return _literal_node_kind(descriptor.node_kind.value)


def _literal_node_kind(value: str) -> Literal["source", "transform", "output"]:
    if value == PipelineNodeKind.SOURCE.value:
        return "source"
    if value == PipelineNodeKind.TRANSFORM.value:
        return "transform"
    if value == PipelineNodeKind.OUTPUT.value:
        return "output"
    raise ValidationFailed("unsupported pipeline node kind", details={"kind": value})


def _descriptors_by_node_id(nodes: Sequence[PipelineV2Node]) -> dict[str, PipelineNodeDescriptor | None]:
    return {node["id"]: pipeline_node_descriptor(node["descriptorId"], node["specVersion"]) for node in nodes}


def _edge_drafts(
    edges: Sequence[Mapping[str, object]],
    raw_nodes: Sequence[Mapping[str, object]],
    descriptors: Mapping[str, PipelineNodeDescriptor | None],
    version: int,
) -> list[PipelineV2Edge]:
    join_ports = _legacy_join_ports(edges, raw_nodes) if version == 1 else {}
    drafts: list[PipelineV2Edge] = []
    for index, edge in enumerate(edges):
        source = _edge_node_id(edge, "sourceNodeId", "source", version)
        target = _edge_node_id(edge, "targetNodeId", "target", version)
        drafts.append(_normalize_edge(edge, source, target, descriptors, version, join_ports.get(index)))
    return drafts


def _normalize_edge(
    edge: Mapping[str, object],
    source: str,
    target: str,
    descriptors: Mapping[str, PipelineNodeDescriptor | None],
    version: int,
    legacy_join_port: str | None,
) -> PipelineV2Edge:
    source_port = _edge_port(edge, "sourcePortId", "sourceHandle", version)
    target_port = _edge_port(edge, "targetPortId", "targetHandle", version)
    source_port = source_port or _default_output_port(descriptors.get(source))
    target_port = target_port or legacy_join_port or _default_input_port(descriptors.get(target))
    normalized = _clone_object(edge) if version == 2 else {}
    normalized.update(
        {
            "id": _optional_text(edge.get("id")),
            "sourceNodeId": source,
            "sourcePortId": source_port,
            "targetNodeId": target,
            "targetPortId": target_port,
        }
    )
    return cast(PipelineV2Edge, normalized)


def _legacy_join_ports(
    edges: Sequence[Mapping[str, object]],
    nodes: Sequence[Mapping[str, object]],
) -> dict[int, str]:
    join_nodes = {str(node.get("id")): node for node in nodes if node.get("type") == "join"}
    ports: dict[int, str] = {}
    for target_id, node in join_nodes.items():
        incoming = [(index, edge) for index, edge in enumerate(edges) if edge.get("target") == target_id]
        ports.update(_join_ports_for_node(node, incoming))
    return ports


def _join_ports_for_node(
    node: Mapping[str, object],
    incoming: Sequence[tuple[int, Mapping[str, object]]],
) -> dict[int, str]:
    assigned = _explicit_join_ports(incoming)
    config = canonical_node_config(node)
    _assign_configured_join_port(assigned, incoming, config.get("leftNodeId"), "left")
    _assign_configured_join_port(assigned, incoming, config.get("rightNodeId"), "right")
    remaining = sorted((item for item in incoming if item[0] not in assigned), key=_edge_sort_key)
    available = [port for port in ("left", "right") if port not in assigned.values()]
    for item, port in zip(remaining, available, strict=False):
        assigned[item[0]] = port
    return assigned


def _explicit_join_ports(incoming: Sequence[tuple[int, Mapping[str, object]]]) -> dict[int, str]:
    assigned: dict[int, str] = {}
    for index, edge in incoming:
        value = edge.get("targetHandle") or edge.get("input") or edge.get("role")
        if isinstance(value, str) and value.lower() in {"left", "right"}:
            assigned[index] = value.lower()
    return assigned


def _assign_configured_join_port(
    assigned: dict[int, str],
    incoming: Sequence[tuple[int, Mapping[str, object]]],
    source_value: object,
    port: str,
) -> None:
    if not isinstance(source_value, str) or not source_value:
        return
    for index, edge in incoming:
        if index not in assigned and edge.get("source") == source_value:
            assigned[index] = port
            return


def _edge_sort_key(item: tuple[int, Mapping[str, object]]) -> tuple[str, str, int]:
    index, edge = item
    return (str(edge.get("source") or ""), str(edge.get("id") or ""), index)


def _finalize_edges(edges: Sequence[PipelineV2Edge]) -> list[PipelineV2Edge]:
    ordered = sorted(edges, key=_normalized_edge_sort_key)
    counts: dict[str, int] = {}
    finalized: list[PipelineV2Edge] = []
    for edge in ordered:
        edge_id = edge["id"]
        if not edge_id:
            base = _generated_edge_id(edge)
            counts[base] = counts.get(base, 0) + 1
            edge_id = f"{base}_{counts[base]}"
        finalized.append(_edge_with_id(edge, edge_id))
    return finalized


def _edge_with_id(edge: PipelineV2Edge, edge_id: str) -> PipelineV2Edge:
    normalized = cast(JsonObject, dict(edge))
    normalized["id"] = edge_id
    return cast(PipelineV2Edge, normalized)


def _normalized_edge_sort_key(edge: PipelineV2Edge) -> tuple[str, str, str, str, str]:
    return (
        edge["targetNodeId"],
        edge["targetPortId"],
        edge["sourceNodeId"],
        edge["sourcePortId"],
        edge["id"],
    )


def _generated_edge_id(edge: PipelineV2Edge) -> str:
    return f"edge_{edge['sourceNodeId']}_{edge['sourcePortId']}__{edge['targetNodeId']}_{edge['targetPortId']}"


def _edge_node_id(edge: Mapping[str, object], v2_key: str, v1_key: str, version: int) -> str:
    return _required_text(edge.get(v2_key if version == 2 else v1_key), f"edge {v2_key}")


def _edge_port(edge: Mapping[str, object], v2_key: str, v1_key: str, version: int) -> str | None:
    value = edge.get(v2_key if version == 2 else v1_key)
    return _optional_text(value) or None


def _default_input_port(descriptor: PipelineNodeDescriptor | None) -> str:
    if descriptor is None or not descriptor.input_ports:
        return "input"
    return descriptor.input_ports[0].port_id


def _default_output_port(descriptor: PipelineNodeDescriptor | None) -> str:
    if descriptor is None or not descriptor.output_ports:
        return "output"
    return descriptor.output_ports[0].port_id


def _normalized_graph(
    graph: Mapping[str, object],
    nodes: list[PipelineV2Node],
    edges: list[PipelineV2Edge],
    version: int,
) -> PipelineGraphV2:
    normalized = _clone_object(graph) if version == 2 else {}
    normalized.update(
        {
            "schemaVersion": 2,
            "nodes": nodes,
            "edges": edges,
            "layout": _optional_object(graph.get("layout"), "layout"),
            "outputContract": _optional_object(graph.get("outputContract"), "outputContract"),
            "tests": _optional_object_rows(graph.get("tests")),
            "schedule": _clone_value(graph.get("schedule")),
        }
    )
    metadata = graph.get("metadata")
    if isinstance(metadata, Mapping):
        normalized["metadata"] = _clone_object(metadata)
    else:
        normalized.pop("metadata", None)
    return cast(PipelineGraphV2, normalized)


def _object_rows(value: object, field: str) -> list[JsonObject]:
    if not isinstance(value, list):
        raise ValidationFailed(f"pipeline graph {field} must be a list", details={"field": field})
    rows: list[JsonObject] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValidationFailed(f"pipeline graph {field} item must be an object", details={"index": index})
        rows.append(_clone_object(item))
    return rows


def _optional_object_rows(value: object) -> list[JsonObject]:
    if value is None:
        return []
    return _object_rows(value, "tests")


def _optional_object(value: object, field: str) -> JsonObject:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValidationFailed(f"pipeline graph {field} must be an object", details={"field": field})
    return _clone_object(value)


def _optional_node_object(node: Mapping[str, object], field: str) -> JsonObject:
    value = node.get(field)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValidationFailed(
            f"pipeline graph node {field} must be an object",
            details={"node": node.get("id")},
        )
    return _clone_object(value)


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationFailed(f"pipeline graph {field} is required", details={"field": field})
    return value.strip()


def _optional_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _clone_object(value: Mapping[str, object] | Mapping[object, object]) -> JsonObject:
    return {str(key): _clone_value(item) for key, item in value.items()}


def _clone_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _clone_object(value)
    if isinstance(value, list):
        return [_clone_value(item) for item in value]
    if isinstance(value, tuple):
        return [_clone_value(item) for item in value]
    return value
