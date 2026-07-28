from __future__ import annotations

import pytest
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2RuntimeArtifact,
    PipelineV2RuntimeEdge,
    PipelineV2RuntimeNode,
    input_artifacts_for_node,
    runtime_plan_edges,
    runtime_plan_nodes,
    runtime_source_contracts,
    single_input_artifact,
)
from foundry_lite.domain.errors import InvariantViolation


def _artifact(*, port_id: str = "dataset") -> PipelineV2RuntimeArtifact:
    return PipelineV2RuntimeArtifact(
        node_id="source",
        descriptor_id="source.dataset",
        spec_version=1,
        port_id=port_id,
        artifact_kind="dataset_version",
        plane="dataset",
        items=({"id": 1},),
        artifact_ref={"versionId": "dv-1"},
        manifest={"rowCount": 1},
        security_envelope={"classification": "INTERNAL"},
        status="COMMITTED",
        is_serving=True,
    )


def _node() -> PipelineV2RuntimeNode:
    return PipelineV2RuntimeNode(
        node_id="out",
        kind="output",
        descriptor_id="output.dataset",
        spec_version=1,
        runtime_capability="dataset_output_runtime",
        config={},
    )


def _edge() -> PipelineV2RuntimeEdge:
    return PipelineV2RuntimeEdge("source-out", "source", "dataset", "out", "input")


def _source_contract(node_id: str = "source") -> dict[str, object]:
    return {
        "nodeId": node_id,
        "descriptorId": "source.dataset",
        "artifactKind": "dataset_version",
        "resourceRef": "raw.orders",
        "sourceId": "ds-1",
        "schemaContract": {"columns": []},
        "schemaHash": "schema-1",
        "schemaVersion": 1,
        "versionPins": [
            {
                "versionId": "dv-1",
                "ordinal": 1,
                "contentFingerprint": "content-1",
                "metadata": {},
            }
        ],
        "securityEnvelope": {"classification": "INTERNAL"},
        "accessEvidence": {"permission": "dataset:read"},
    }


def test_runtime_plan_contracts_parse_exact_coordinates() -> None:
    nodes = runtime_plan_nodes(
        {
            "nodes": [
                {
                    "nodeId": "source",
                    "kind": "source",
                    "descriptorId": "source.dataset",
                    "specVersion": 1,
                    "runtimeCapability": "dataset_source_runtime",
                    "config": {"datasetRef": "raw.orders"},
                }
            ]
        }
    )
    edges = runtime_plan_edges(
        {
            "edges": [
                {
                    "edgeId": "source-out",
                    "sourceNodeId": "source",
                    "sourcePortId": "dataset",
                    "targetNodeId": "out",
                    "targetPortId": "input",
                }
            ]
        }
    )
    contracts = runtime_source_contracts({"sourceContracts": [_source_contract()]})

    assert nodes[0].config == {"datasetRef": "raw.orders"}
    assert edges == (_edge(),)
    assert contracts["source"].version_pins[0].version_id == "dv-1"


def test_runtime_input_resolution_rejects_missing_or_mismatched_upstream_artifacts() -> None:
    artifact = _artifact()
    assert input_artifacts_for_node("out", [_edge()], {"source": artifact}) == {"input": (artifact,)}
    assert single_input_artifact(_node(), {"input": (artifact,)}) is artifact

    with pytest.raises(InvariantViolation, match="unavailable"):
        input_artifacts_for_node("out", [_edge()], {})
    with pytest.raises(InvariantViolation, match="port does not match"):
        input_artifacts_for_node("out", [_edge()], {"source": _artifact(port_id="wrong")})
    with pytest.raises(InvariantViolation, match="exactly one"):
        single_input_artifact(_node(), {})


@pytest.mark.parametrize(
    "plan",
    [
        {"nodes": None},
        {"nodes": ["bad"]},
        {
            "nodes": [
                {
                    "nodeId": "source",
                    "kind": "source",
                    "descriptorId": "source.dataset",
                    "specVersion": 1,
                    "runtimeCapability": "dataset_source_runtime",
                    "config": [],
                }
            ]
        },
        {
            "nodes": [
                {
                    "nodeId": "",
                    "kind": "source",
                    "descriptorId": "source.dataset",
                    "specVersion": 1,
                    "runtimeCapability": "dataset_source_runtime",
                    "config": {},
                }
            ]
        },
        {
            "nodes": [
                {
                    "nodeId": "source",
                    "kind": "source",
                    "descriptorId": "source.dataset",
                    "specVersion": True,
                    "runtimeCapability": "dataset_source_runtime",
                    "config": {},
                }
            ]
        },
    ],
)
def test_runtime_plan_node_parser_fails_closed_on_malformed_shapes(plan: dict[str, object]) -> None:
    with pytest.raises(InvariantViolation):
        runtime_plan_nodes(plan)


def test_runtime_source_contract_parser_rejects_duplicate_or_malformed_evidence() -> None:
    with pytest.raises(InvariantViolation, match="duplicate source contracts"):
        runtime_source_contracts({"sourceContracts": [_source_contract(), _source_contract()]})

    invalid_contracts = (
        {**_source_contract(), "securityEnvelope": []},
        {**_source_contract(), "schemaContract": []},
        {**_source_contract(), "accessEvidence": []},
        {**_source_contract(), "schemaVersion": True},
        {
            **_source_contract(),
            "versionPins": [
                {
                    "versionId": "dv-1",
                    "ordinal": 1,
                    "contentFingerprint": "content-1",
                    "metadata": [],
                }
            ],
        },
    )
    for contract in invalid_contracts:
        with pytest.raises(InvariantViolation):
            runtime_source_contracts({"sourceContracts": [contract]})
