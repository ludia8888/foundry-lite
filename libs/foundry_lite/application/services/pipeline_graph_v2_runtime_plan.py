"""Immutable target-path selection for a deployed Pipeline Graph v2 plan."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2RuntimeEdge,
    PipelineV2RuntimeNode,
    PipelineV2SourceContract,
    runtime_plan_edges,
    runtime_plan_nodes,
    runtime_source_contracts,
)
from foundry_lite.domain.errors import ValidationFailed


@dataclass(frozen=True, slots=True)
class PipelineGraphV2RuntimePlan:
    nodes: tuple[PipelineV2RuntimeNode, ...]
    edges: tuple[PipelineV2RuntimeEdge, ...]
    source_contracts: Mapping[str, PipelineV2SourceContract]


def pipeline_graph_v2_runtime_plan(
    plan: Mapping[str, object],
    *,
    target_node_ids: Sequence[str] = (),
) -> PipelineGraphV2RuntimePlan:
    """Select exact target ancestors while preserving deployed topological order."""

    nodes = runtime_plan_nodes(plan)
    edges = runtime_plan_edges(plan)
    selected = _selected_node_ids(nodes, edges, target_node_ids)
    selected_nodes = tuple(node for node in nodes if node.node_id in selected)
    selected_edges = tuple(
        edge for edge in edges if edge.source_node_id in selected and edge.target_node_id in selected
    )
    contracts = runtime_source_contracts(plan)
    return PipelineGraphV2RuntimePlan(
        nodes=selected_nodes,
        edges=selected_edges,
        source_contracts={node_id: contract for node_id, contract in contracts.items() if node_id in selected},
    )


def _selected_node_ids(
    nodes: Sequence[PipelineV2RuntimeNode],
    edges: Sequence[PipelineV2RuntimeEdge],
    targets: Sequence[str],
) -> frozenset[str]:
    known = {node.node_id for node in nodes}
    requested = frozenset(node_id for node_id in targets if node_id)
    missing = sorted(requested - known)
    if missing:
        raise ValidationFailed(
            "pipeline target node was not found in the deployed execution plan",
            details={"targetNodeIds": missing},
        )
    if not requested:
        return frozenset(known)
    incoming: dict[str, list[str]] = {}
    for edge in edges:
        incoming.setdefault(edge.target_node_id, []).append(edge.source_node_id)
    return _ancestors(requested, incoming)


def _ancestors(
    targets: frozenset[str],
    incoming: Mapping[str, Sequence[str]],
) -> frozenset[str]:
    selected = set(targets)
    pending = list(targets)
    while pending:
        current = pending.pop()
        for source_id in incoming.get(current, ()):
            if source_id not in selected:
                selected.add(source_id)
                pending.append(source_id)
    return frozenset(selected)
