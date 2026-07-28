from __future__ import annotations

import pytest
from foundry_lite.application.services.pipeline_node_evidence_plan import PipelineEvidencePlan
from foundry_lite.domain.errors import InvariantViolation


def test_input_artifact_requires_the_target_nodes_named_incoming_edge() -> None:
    plan = PipelineEvidencePlan(_plan(include_target_edge=False))

    with pytest.raises(InvariantViolation, match="one named incoming edge"):
        plan.input_artifact_spec("target", "raw.orders")


def test_incoming_edge_rejects_missing_or_ambiguous_named_ports() -> None:
    missing = PipelineEvidencePlan(_plan(include_target_edge=False))
    with pytest.raises(InvariantViolation, match="missing or ambiguous"):
        missing.incoming_edge("target", "source", "dataset")

    ambiguous_plan = _plan(include_target_edge=True)
    edges = ambiguous_plan["edges"]
    assert isinstance(edges, list)
    edges.append(dict(edges[0], edgeId="duplicate"))
    ambiguous = PipelineEvidencePlan(ambiguous_plan)
    with pytest.raises(InvariantViolation, match="missing or ambiguous"):
        ambiguous.incoming_edge("target", "source", "dataset")


def _plan(*, include_target_edge: bool) -> dict[str, object]:
    edges: list[dict[str, object]] = []
    if include_target_edge:
        edges.append(
            {
                "edgeId": "source-target",
                "sourceNodeId": "source",
                "sourcePortId": "dataset",
                "targetNodeId": "target",
                "targetPortId": "input",
            }
        )
    return {
        "nodes": [
            {"nodeId": "source", "descriptorId": "source.dataset"},
            {"nodeId": "target", "descriptorId": "output.dataset"},
        ],
        "edges": edges,
        "artifacts": [
            {
                "artifactId": "source-dataset",
                "artifactKind": "dataset_version",
                "producerNodeId": "source",
                "producerPortId": "dataset",
                "resourceRef": "raw.orders",
            }
        ],
    }
