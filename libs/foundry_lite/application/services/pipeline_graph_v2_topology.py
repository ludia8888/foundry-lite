"""Topology and named-port validation for canonical Pipeline Builder v2 graphs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.services.pipeline_graph_contracts import (
    JsonObject,
    PipelineArtifactKind,
    PipelineGraphV2,
    PipelineInputPortDescriptor,
    PipelineNodeDescriptor,
    PipelineNodeKind,
    PipelineOutputPortDescriptor,
    PipelinePortCardinality,
    PipelineV2Edge,
    PipelineV2Node,
    pipeline_node_descriptor,
)


def pipeline_topology_errors(graph: PipelineGraphV2) -> list[JsonObject]:
    descriptors = _descriptor_map(graph["nodes"])
    errors = _edge_endpoint_errors(graph["edges"], descriptors)
    if errors:
        return errors
    errors.extend(_port_contract_errors(graph["edges"], descriptors))
    errors.extend(_required_port_errors(graph["nodes"], graph["edges"], descriptors))
    errors.extend(_terminal_kind_errors(graph["nodes"], graph["edges"]))
    errors.extend(_cycle_errors(graph["nodes"], graph["edges"]))
    return errors


def _descriptor_map(nodes: Sequence[PipelineV2Node]) -> dict[str, PipelineNodeDescriptor | None]:
    return {node["id"]: pipeline_node_descriptor(node["descriptorId"], node["specVersion"]) for node in nodes}


def _edge_endpoint_errors(
    edges: Sequence[PipelineV2Edge],
    descriptors: Mapping[str, PipelineNodeDescriptor | None],
) -> list[JsonObject]:
    errors: list[JsonObject] = []
    for edge in edges:
        if edge["sourceNodeId"] not in descriptors:
            errors.append({"code": "dangling_edge_source", "edgeId": edge["id"]})
        if edge["targetNodeId"] not in descriptors:
            errors.append({"code": "dangling_edge_target", "edgeId": edge["id"]})
    return errors


def _port_contract_errors(
    edges: Sequence[PipelineV2Edge],
    descriptors: Mapping[str, PipelineNodeDescriptor | None],
) -> list[JsonObject]:
    errors: list[JsonObject] = []
    for edge in edges:
        errors.extend(_edge_port_contract_errors(edge, descriptors))
    return errors


def _edge_port_contract_errors(
    edge: PipelineV2Edge,
    descriptors: Mapping[str, PipelineNodeDescriptor | None],
) -> list[JsonObject]:
    source = descriptors.get(edge["sourceNodeId"])
    target = descriptors.get(edge["targetNodeId"])
    if source is None or target is None:
        return []
    source_port = next((port for port in source.output_ports if port.port_id == edge["sourcePortId"]), None)
    target_port = next((port for port in target.input_ports if port.port_id == edge["targetPortId"]), None)
    errors = _missing_port_errors(edge, source_port, target_port)
    if source_port is None or target_port is None:
        return errors
    errors.extend(_artifact_compatibility_errors(edge, source_port.artifact_kind, target_port))
    return errors


def _missing_port_errors(
    edge: PipelineV2Edge,
    source_port: PipelineOutputPortDescriptor | None,
    target_port: PipelineInputPortDescriptor | None,
) -> list[JsonObject]:
    errors: list[JsonObject] = []
    if source_port is None:
        errors.append({"code": "source_port_not_found", "edgeId": edge["id"], "portId": edge["sourcePortId"]})
    if target_port is None:
        errors.append({"code": "target_port_not_found", "edgeId": edge["id"], "portId": edge["targetPortId"]})
    return errors


def _artifact_compatibility_errors(
    edge: PipelineV2Edge,
    artifact_kind: PipelineArtifactKind,
    target_port: PipelineInputPortDescriptor,
) -> list[JsonObject]:
    if artifact_kind in target_port.accepted_artifact_kinds:
        return []
    return [
        {
            "code": "artifact_kind_mismatch",
            "edgeId": edge["id"],
            "artifactKind": str(artifact_kind),
            "acceptedArtifactKinds": [kind.value for kind in target_port.accepted_artifact_kinds],
        }
    ]


def _required_port_errors(
    nodes: Sequence[PipelineV2Node],
    edges: Sequence[PipelineV2Edge],
    descriptors: Mapping[str, PipelineNodeDescriptor | None],
) -> list[JsonObject]:
    counts = _input_connection_counts(edges)
    errors: list[JsonObject] = []
    for node in nodes:
        descriptor = descriptors.get(node["id"])
        if descriptor is None:
            continue
        for port in descriptor.input_ports:
            count = counts.get((node["id"], port.port_id), 0)
            errors.extend(_port_count_errors(node["id"], port, count))
    return errors


def _input_connection_counts(edges: Sequence[PipelineV2Edge]) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for edge in edges:
        key = (edge["targetNodeId"], edge["targetPortId"])
        counts[key] = counts.get(key, 0) + 1
    return counts


def _port_count_errors(node_id: str, port: PipelineInputPortDescriptor, count: int) -> list[JsonObject]:
    if port.is_required and count == 0:
        return [{"code": "required_input_port_missing", "nodeId": node_id, "portId": port.port_id}]
    if port.cardinality == PipelinePortCardinality.ONE and count > 1:
        return [{"code": "input_port_cardinality", "nodeId": node_id, "portId": port.port_id, "actual": count}]
    return []


def _terminal_kind_errors(
    nodes: Sequence[PipelineV2Node],
    edges: Sequence[PipelineV2Edge],
) -> list[JsonObject]:
    kind_by_id = {node["id"]: node["kind"] for node in nodes}
    errors: list[JsonObject] = []
    for edge in edges:
        if kind_by_id.get(edge["sourceNodeId"]) == PipelineNodeKind.OUTPUT.value:
            errors.append({"code": "output_node_has_outgoing_edge", "edgeId": edge["id"]})
        if kind_by_id.get(edge["targetNodeId"]) == PipelineNodeKind.SOURCE.value:
            errors.append({"code": "source_node_has_incoming_edge", "edgeId": edge["id"]})
    return errors


def _cycle_errors(nodes: Sequence[PipelineV2Node], edges: Sequence[PipelineV2Edge]) -> list[JsonObject]:
    incoming = {node["id"]: 0 for node in nodes}
    outgoing: dict[str, list[str]] = {node["id"]: [] for node in nodes}
    for edge in edges:
        incoming[edge["targetNodeId"]] += 1
        outgoing[edge["sourceNodeId"]].append(edge["targetNodeId"])
    ready = sorted(node_id for node_id, count in incoming.items() if count == 0)
    consumed = _consume_topology(ready, incoming, outgoing)
    if consumed == len(nodes):
        return []
    return [{"code": "cycle_detected", "node_count": len(nodes)}]


def _consume_topology(
    ready: list[str],
    incoming: dict[str, int],
    outgoing: Mapping[str, Sequence[str]],
) -> int:
    consumed = 0
    while ready:
        current = ready.pop(0)
        consumed += 1
        for target in sorted(outgoing[current]):
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
    return consumed
