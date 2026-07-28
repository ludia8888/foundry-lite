"""Three-way Pipeline Graph v2 merge behavior."""

from __future__ import annotations

from copy import deepcopy
from typing import cast

from foundry_lite.application.services.pipeline_graph_contracts import (
    PipelineGraphV2,
)
from foundry_lite.application.services.pipeline_graph_three_way_merge import (
    merge_pipeline_graphs,
)


def test_three_way_merge_preserves_independent_node_changes() -> None:
    base = _graph([_node("source", "base")])
    ours = deepcopy(base)
    ours["nodes"][0]["config"]["label"] = "ours"
    theirs = deepcopy(base)
    theirs["nodes"].append(_node("output", "theirs"))

    result = merge_pipeline_graphs(base, ours, theirs)

    assert result.conflicts == ()
    assert [node["id"] for node in result.graph["nodes"]] == ["output", "source"]
    source = next(node for node in result.graph["nodes"] if node["id"] == "source")
    assert source["config"]["label"] == "ours"


def test_three_way_merge_reports_same_field_conflict_without_silent_overwrite() -> None:
    base = _graph([_node("source", "base")])
    ours = deepcopy(base)
    ours["nodes"][0]["config"]["label"] = "ours"
    theirs = deepcopy(base)
    theirs["nodes"][0]["config"]["label"] = "theirs"

    result = merge_pipeline_graphs(base, ours, theirs)

    assert result.conflicts == (
        {
            "path": "$.nodes[source].config.label",
            "kind": "concurrent_change",
        },
    )
    assert result.graph["nodes"][0]["config"]["label"] == "ours"


def test_three_way_merge_reports_delete_vs_modify_conflict() -> None:
    base = _graph([_node("source", "base")])
    ours = _graph([])
    theirs = deepcopy(base)
    theirs["nodes"][0]["config"]["label"] = "theirs"

    result = merge_pipeline_graphs(base, ours, theirs)

    assert result.conflicts == ({"path": "$.nodes[source]", "kind": "concurrent_change"},)


def _graph(nodes: list[dict[str, object]]) -> PipelineGraphV2:
    return cast(
        PipelineGraphV2,
        {
            "schemaVersion": 2,
            "nodes": nodes,
            "edges": [],
            "layout": {},
            "outputContract": {"columns": []},
            "tests": [],
            "schedule": None,
        },
    )


def _node(node_id: str, label: str) -> dict[str, object]:
    return {
        "id": node_id,
        "kind": "source",
        "descriptorId": "source.dataset",
        "specVersion": 1,
        "config": {"label": label, "datasetRef": f"raw.{node_id}"},
    }
