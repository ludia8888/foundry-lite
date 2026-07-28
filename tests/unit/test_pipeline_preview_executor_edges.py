from __future__ import annotations

from typing import cast

import pytest
from foundry_lite.application.services.pipeline_graph_contracts import PipelineGraphV2, PipelineV2Node
from foundry_lite.application.services.pipeline_preview_executor import (
    _ancestor_node_ids,
    _consume_topology,
    _descriptor,
    _execute_node,
    _infer_schema,
    _inputs_for_node,
    _model_pins,
    _remaining_limits,
    _require_time_budget,
    _security_envelopes,
    _target_node_ids,
    _topological_node_ids,
    _value_type,
    execute_pipeline_preview,
)
from foundry_lite.application.services.pipeline_preview_runtime import PipelinePreviewRuntime
from foundry_lite.application.services.pipeline_preview_transforms import NODE_HANDLERS
from foundry_lite.domain.errors import ValidationFailed


def _graph(*, include_output: bool = True) -> PipelineGraphV2:
    nodes: list[PipelineV2Node] = [
        {
            "id": "source",
            "kind": "source",
            "descriptorId": "source.dataset",
            "specVersion": 1,
            "config": {"datasetRef": "raw.orders"},
        }
    ]
    edges: list[dict[str, object]] = []
    if include_output:
        nodes.append(
            {
                "id": "out",
                "kind": "output",
                "descriptorId": "output.dataset",
                "specVersion": 1,
                "config": {"outputDatasetRef": "clean.orders"},
            }
        )
        edges.append(
            {
                "id": "source-out",
                "sourceNodeId": "source",
                "sourcePortId": "dataset",
                "targetNodeId": "out",
                "targetPortId": "input",
            }
        )
    return {
        "schemaVersion": 2,
        "nodes": nodes,
        "edges": edges,  # type: ignore[typeddict-item]
        "layout": {},
        "outputContract": {"columns": []},
        "tests": [],
        "schedule": None,
    }


def test_preview_graph_target_and_topology_helpers_reject_missing_or_cyclic_nodes() -> None:
    graph = _graph()
    assert _target_node_ids(graph, "source") == ["source"]
    assert _target_node_ids(graph, None) == ["out"]
    assert _ancestor_node_ids(graph, ["out"]) == {"source", "out"}
    assert _topological_node_ids(graph, {"source", "out"}) == ["source", "out"]

    with pytest.raises(ValidationFailed, match="target node not found"):
        _target_node_ids(graph, "missing")
    with pytest.raises(ValidationFailed, match="requires an output"):
        _target_node_ids(_graph(include_output=False), None)
    with pytest.raises(ValidationFailed, match="contains a cycle"):
        _consume_topology({"a": 1, "b": 1}, {"a": ["b"], "b": ["a"]})


def test_preview_executor_rejects_invalid_graph_and_missing_runtime_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationFailed, match="graph is invalid"):
        execute_pipeline_preview(
            {"schemaVersion": 2, "nodes": [], "edges": []},
            preview_run_id="preview-1",
            target_node_id=None,
            limits={"timeoutSeconds": 30},
            runtime=cast(PipelinePreviewRuntime, object()),
        )

    node = _graph()["nodes"][0]
    monkeypatch.delitem(NODE_HANDLERS, "source.dataset")
    with pytest.raises(ValidationFailed, match="no non-committing preview executor"):
        _execute_node(node, {}, {"tableRows": 5}, cast(PipelinePreviewRuntime, object()))

    with pytest.raises(ValidationFailed, match="descriptor is unavailable"):
        _descriptor({**node, "specVersion": 999})


def test_preview_artifact_helpers_infer_schema_evidence_and_inputs() -> None:
    rows = [
        {
            "flag": True,
            "count": 2,
            "ratio": 1.5,
            "tags": ["a"],
            "metadata": {"source": "orders"},
            "name": None,
            "securityEnvelope": {"classification": "internal"},
            "model": {"name": "claude", "version": "1"},
        },
        {
            "name": "order",
            "securityEnvelope": {"classification": "internal"},
        },
    ]
    schema = {field["name"]: field["type"] for field in _infer_schema(rows)}
    assert schema == {
        "count": "integer",
        "flag": "boolean",
        "metadata": "object",
        "model": "object",
        "name": "string",
        "ratio": "float",
        "securityEnvelope": "object",
        "tags": "array",
    }
    assert _infer_schema([]) == []
    assert [_value_type(value) for value in (True, 1, 1.5, [], {}, None)] == [
        "boolean",
        "integer",
        "float",
        "array",
        "object",
        "string",
    ]
    assert _security_envelopes(rows) == [{"classification": "internal"}]
    assert _model_pins(rows) == [{"name": "claude", "version": "1"}]

    graph = _graph()
    assert _inputs_for_node(
        graph,
        "out",
        {"source": {"items": rows}},
    ) == {"input": rows}


def test_preview_time_budget_rejects_expired_or_fractional_remaining_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "foundry_lite.application.services.pipeline_preview_executor.time.monotonic",
        lambda: 10.0,
    )
    with pytest.raises(ValidationFailed, match="hard time budget"):
        _require_time_budget(0.0, {"timeoutSeconds": 1})
    with pytest.raises(ValidationFailed, match="no remaining execution time"):
        _remaining_limits(9.2, {"timeoutSeconds": 1})
