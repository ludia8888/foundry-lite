"""Bounded, non-committing executor for draft Pipeline Builder graphs."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from foundry_lite.application.primitives import _json_hash
from foundry_lite.application.services.pipeline_graph_contracts import (
    DEFAULT_PIPELINE_PREVIEW_ROWS,
    MAX_PIPELINE_PREVIEW_ROWS,
    PipelineGraphV2,
    PipelineNodeDescriptor,
    PipelineV2Edge,
    PipelineV2Node,
    pipeline_node_descriptor,
)
from foundry_lite.application.services.pipeline_graph_model import validate_pipeline_graph
from foundry_lite.application.services.pipeline_graph_normalizer import normalize_pipeline_graph
from foundry_lite.application.services.pipeline_preview_runtime import PipelinePreviewRuntime
from foundry_lite.application.services.pipeline_preview_security import (
    public_preview_security_envelopes,
    without_internal_security_envelopes,
)
from foundry_lite.application.services.pipeline_preview_transforms import NODE_HANDLERS, bounded_int, object_items
from foundry_lite.domain.errors import ValidationFailed

JsonObject = dict[str, object]

DEFAULT_PREVIEW_LIMITS: JsonObject = {
    "tableRows": DEFAULT_PIPELINE_PREVIEW_ROWS,
    "mediaItems": 5,
    "pdfPages": 3,
    "audioVideoSeconds": 60,
    "sceneCount": 12,
    "searchHits": 10,
    "totalBytes": 32 * 1024 * 1024,
    "timeoutSeconds": 30,
}


@dataclass(frozen=True)
class PreviewExecutionResult:
    outputs: list[JsonObject]
    artifacts: list[JsonObject]


def normalize_preview_limits(raw: Mapping[str, object] | None) -> JsonObject:
    source = dict(raw or {})
    return {
        "tableRows": bounded_int(
            source.get("tableRows"),
            DEFAULT_PIPELINE_PREVIEW_ROWS,
            1,
            MAX_PIPELINE_PREVIEW_ROWS,
        ),
        "mediaItems": bounded_int(source.get("mediaItems"), 5, 1, 20),
        "pdfPages": bounded_int(source.get("pdfPages"), 3, 1, 10),
        "audioVideoSeconds": bounded_int(source.get("audioVideoSeconds"), 60, 1, 60),
        "sceneCount": bounded_int(source.get("sceneCount"), 12, 1, 12),
        "searchHits": bounded_int(source.get("searchHits"), 10, 1, 10),
        "totalBytes": bounded_int(source.get("totalBytes"), 32 * 1024 * 1024, 1, 32 * 1024 * 1024),
        "timeoutSeconds": bounded_int(source.get("timeoutSeconds"), 30, 1, 30),
    }


def execute_pipeline_preview(
    graph: Mapping[str, object],
    *,
    preview_run_id: str,
    target_node_id: str | None,
    limits: Mapping[str, object],
    runtime: PipelinePreviewRuntime,
) -> PreviewExecutionResult:
    validation = validate_pipeline_graph(graph)
    if not validation["valid"]:
        raise ValidationFailed("pipeline preview graph is invalid", details={"validation": validation})
    canonical = normalize_pipeline_graph(graph)
    target_ids = _target_node_ids(canonical, target_node_id)
    selected = _ancestor_node_ids(canonical, target_ids)
    ordered = _topological_node_ids(canonical, selected)
    return _execute_selected(canonical, ordered, target_ids, preview_run_id, limits, runtime)


def _execute_selected(
    graph: PipelineGraphV2,
    ordered: Sequence[str],
    target_ids: Sequence[str],
    preview_run_id: str,
    limits: Mapping[str, object],
    runtime: PipelinePreviewRuntime,
) -> PreviewExecutionResult:
    values: dict[str, JsonObject] = {}
    artifacts: list[JsonObject] = []
    started = time.monotonic()
    for node_id in ordered:
        _require_time_budget(started, limits)
        node = _node_map(graph)[node_id]
        inputs = _inputs_for_node(graph, node_id, values)
        artifact = _execute_node(node, inputs, _remaining_limits(started, limits), runtime)
        values[node_id] = artifact
        artifacts.append(_artifact_payload(preview_run_id, node, artifact))
    outputs = [_output_payload(preview_run_id, _node_map(graph)[node_id], values[node_id]) for node_id in target_ids]
    return PreviewExecutionResult(outputs, artifacts)


def _execute_node(
    node: PipelineV2Node,
    inputs: Mapping[str, Sequence[JsonObject]],
    limits: Mapping[str, object],
    runtime: PipelinePreviewRuntime,
) -> JsonObject:
    descriptor = _descriptor(node)
    handler = NODE_HANDLERS.get(descriptor.descriptor_id)
    if handler is None:
        raise ValidationFailed(
            "pipeline node has no non-committing preview executor",
            details={
                "nodeId": node["id"],
                "descriptorId": descriptor.descriptor_id,
                "runtimeCapability": descriptor.runtime_capability,
            },
        )
    items = handler(node, inputs, limits, runtime)
    artifact_kind = descriptor.output_ports[0].artifact_kind.value
    return _artifact_value(node, artifact_kind, items)


def _artifact_value(node: PipelineV2Node, artifact_kind: str, items: list[JsonObject]) -> JsonObject:
    return {
        "nodeId": node["id"],
        "descriptorId": node["descriptorId"],
        "specVersion": node["specVersion"],
        "artifactKind": artifact_kind,
        "items": items,
        "itemCount": len(items),
        "schema": _infer_schema(items),
        "securityEnvelopes": _security_envelopes(items),
    }


def _artifact_payload(preview_run_id: str, node: PipelineV2Node, artifact: Mapping[str, object]) -> JsonObject:
    return {
        "artifactId": f"preview:{preview_run_id}:{node['id']}",
        **_public_artifact(artifact),
        "contentFingerprint": _json_hash(dict(artifact)),
        "serving": False,
        "commitForbidden": True,
        "passport": _artifact_passport(node, artifact),
    }


def _output_payload(preview_run_id: str, node: PipelineV2Node, artifact: Mapping[str, object]) -> JsonObject:
    return {
        "previewRunId": preview_run_id,
        **_public_artifact(artifact),
        "servingVersionCreated": False,
        "notice": "미리보기 전용 · 출력 버전이 생성되지 않음",
        "passport": _artifact_passport(node, artifact),
    }


def _public_artifact(artifact: Mapping[str, object]) -> JsonObject:
    payload = dict(artifact)
    payload["items"] = [without_internal_security_envelopes(item) for item in object_items(artifact.get("items"))]
    payload["schema"] = [
        dict(field) for field in object_items(artifact.get("schema")) if field.get("name") != "securityEnvelope"
    ]
    payload["securityEnvelopes"] = public_preview_security_envelopes(artifact.get("securityEnvelopes"))
    return payload


def _artifact_passport(node: PipelineV2Node, artifact: Mapping[str, object]) -> JsonObject:
    items = object_items(artifact.get("items"))
    processors = sorted({str(item["processorId"]) for item in items if item.get("processorId")})
    models = _model_pins(items)
    return {
        "artifactKind": artifact["artifactKind"],
        "descriptorId": node["descriptorId"],
        "specVersion": node["specVersion"],
        "processorPins": processors,
        "modelPins": models,
        "securityEnvelopes": public_preview_security_envelopes(artifact.get("securityEnvelopes")),
        "serving": False,
    }


def _target_node_ids(graph: PipelineGraphV2, target_node_id: str | None) -> list[str]:
    node_ids = {node["id"] for node in graph["nodes"]}
    if target_node_id is not None:
        if target_node_id not in node_ids:
            raise ValidationFailed("preview target node not found", details={"targetNodeId": target_node_id})
        return [target_node_id]
    targets = [node["id"] for node in graph["nodes"] if node["kind"] == "output"]
    if not targets:
        raise ValidationFailed("pipeline preview requires an output or target node")
    return sorted(targets)


def _ancestor_node_ids(graph: PipelineGraphV2, target_ids: Sequence[str]) -> set[str]:
    incoming: dict[str, list[str]] = {}
    for edge in graph["edges"]:
        incoming.setdefault(edge["targetNodeId"], []).append(edge["sourceNodeId"])
    selected = set(target_ids)
    stack = list(target_ids)
    while stack:
        current = stack.pop()
        for source in incoming.get(current, []):
            if source not in selected:
                selected.add(source)
                stack.append(source)
    return selected


def _topological_node_ids(graph: PipelineGraphV2, selected: set[str]) -> list[str]:
    incoming = {node_id: 0 for node_id in selected}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in selected}
    for edge in graph["edges"]:
        source, target = edge["sourceNodeId"], edge["targetNodeId"]
        if source in selected and target in selected:
            incoming[target] += 1
            outgoing[source].append(target)
    return _consume_topology(incoming, outgoing)


def _consume_topology(incoming: dict[str, int], outgoing: Mapping[str, Sequence[str]]) -> list[str]:
    ready = sorted(node_id for node_id, count in incoming.items() if count == 0)
    ordered: list[str] = []
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        for target in sorted(outgoing[current]):
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
    if len(ordered) != len(incoming):
        raise ValidationFailed("pipeline preview graph contains a cycle")
    return ordered


def _inputs_for_node(
    graph: PipelineGraphV2,
    node_id: str,
    values: Mapping[str, JsonObject],
) -> dict[str, list[JsonObject]]:
    inputs: dict[str, list[JsonObject]] = {}
    for edge in sorted(graph["edges"], key=_edge_sort_key):
        if edge["targetNodeId"] != node_id:
            continue
        artifact = values[edge["sourceNodeId"]]
        inputs.setdefault(edge["targetPortId"], []).extend(object_items(artifact.get("items")))
    return inputs


def _descriptor(node: PipelineV2Node) -> PipelineNodeDescriptor:
    descriptor = pipeline_node_descriptor(node["descriptorId"], node["specVersion"])
    if descriptor is None:
        raise ValidationFailed("pipeline node descriptor is unavailable", details={"nodeId": node["id"]})
    return descriptor


def _node_map(graph: PipelineGraphV2) -> dict[str, PipelineV2Node]:
    return {node["id"]: node for node in graph["nodes"]}


def _edge_sort_key(edge: PipelineV2Edge) -> tuple[str, str, str]:
    return (edge["targetPortId"], edge["sourceNodeId"], edge["id"])


def _infer_schema(items: Sequence[Mapping[str, object]]) -> list[JsonObject]:
    if not items:
        return []
    keys = sorted({key for item in items for key in item})
    return [
        {"name": key, "type": _value_type(next((item.get(key) for item in items if item.get(key) is not None), None))}
        for key in keys
    ]


def _value_type(value: object) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, list):
        return "array"
    if isinstance(value, Mapping):
        return "object"
    return "string"


def _security_envelopes(items: Sequence[Mapping[str, object]]) -> list[JsonObject]:
    unique: dict[str, JsonObject] = {}
    for item in items:
        envelope = item.get("securityEnvelope")
        if isinstance(envelope, Mapping):
            payload = dict(envelope)
            unique[_json_hash(payload)] = payload
    return [unique[key] for key in sorted(unique)]


def _model_pins(items: Sequence[Mapping[str, object]]) -> list[JsonObject]:
    models: list[JsonObject] = []
    for item in items:
        model = item.get("model")
        if isinstance(model, Mapping):
            models.append({str(key): value for key, value in model.items()})
    return models


def _require_time_budget(started: float, limits: Mapping[str, object]) -> None:
    elapsed = time.monotonic() - started
    timeout = bounded_int(limits.get("timeoutSeconds"), 30, 1, 30)
    if elapsed > timeout:
        raise ValidationFailed(
            "pipeline preview exceeded its hard time budget",
            details={"elapsedSeconds": elapsed, "timeoutSeconds": timeout},
        )


def _remaining_limits(started: float, limits: Mapping[str, object]) -> JsonObject:
    timeout = bounded_int(limits.get("timeoutSeconds"), 30, 1, 30)
    remaining = int(timeout - (time.monotonic() - started))
    if remaining < 1:
        _require_time_budget(started, limits)
        raise ValidationFailed("pipeline preview has no remaining execution time")
    return {**dict(limits), "timeoutSeconds": remaining}
