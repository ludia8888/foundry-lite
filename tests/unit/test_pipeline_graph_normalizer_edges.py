from __future__ import annotations

import pytest
from foundry_lite.application.services.pipeline_graph_normalizer import (
    canonical_node_config,
    normalize_pipeline_graph,
    pipeline_graph_schema_version,
)
from foundry_lite.domain.errors import ValidationFailed


@pytest.mark.parametrize("schema_version", [True, "2", 0, 3])
def test_pipeline_graph_schema_version_rejects_non_integer_or_unsupported_values(
    schema_version: object,
) -> None:
    with pytest.raises(ValidationFailed):
        pipeline_graph_schema_version({"schemaVersion": schema_version})


@pytest.mark.parametrize(
    "graph",
    [
        {"nodes": {}, "edges": []},
        {"nodes": [None], "edges": []},
        {"schemaVersion": 2, "nodes": [], "edges": [], "layout": []},
        {"schemaVersion": 2, "nodes": [], "edges": [], "outputContract": []},
        {"schemaVersion": 2, "nodes": [], "edges": [], "tests": [None]},
        {"nodes": [{"id": "", "type": "dataset"}], "edges": []},
        {"nodes": [{"id": "n", "type": "unknown"}], "edges": []},
        {
            "schemaVersion": 2,
            "nodes": [
                {
                    "id": "n",
                    "kind": "source",
                    "descriptorId": "source.dataset",
                    "specVersion": False,
                    "config": {},
                }
            ],
            "edges": [],
        },
        {
            "schemaVersion": 2,
            "nodes": [
                {
                    "id": "n",
                    "kind": "invalid",
                    "descriptorId": "source.dataset",
                    "specVersion": 1,
                    "config": {},
                }
            ],
            "edges": [],
        },
    ],
)
def test_normalizer_rejects_malformed_graph_boundaries(graph: dict[str, object]) -> None:
    with pytest.raises(ValidationFailed):
        normalize_pipeline_graph(graph)


@pytest.mark.parametrize("field", ["config", "data"])
def test_canonical_node_config_rejects_non_object_sections(field: str) -> None:
    with pytest.raises(ValidationFailed):
        canonical_node_config({"id": "n", field: []})


def test_normalizer_clones_tuple_metadata_and_generates_stable_duplicate_edge_ids() -> None:
    graph = {
        "nodes": [
            {"id": "src", "type": "dataset", "data": {"datasetRef": "raw.orders"}},
            {"id": "out", "type": "output_dataset", "data": {"datasetRef": "curated.orders"}},
        ],
        "edges": [
            {"source": "src", "target": "out"},
            {"source": "src", "target": "out"},
        ],
        "metadata": {"labels": ("stable", "reviewed")},
    }

    normalized = normalize_pipeline_graph(graph)

    assert normalized["metadata"] == {"labels": ["stable", "reviewed"]}
    assert [edge["id"] for edge in normalized["edges"]] == [
        "edge_src_dataset__out_input_1",
        "edge_src_dataset__out_input_2",
    ]
    output_node = next(node for node in normalized["nodes"] if node["id"] == "out")
    assert output_node["config"]["outputDatasetRef"] == "curated.orders"


def test_normalizer_drops_non_object_metadata_and_uses_empty_optional_sections() -> None:
    normalized = normalize_pipeline_graph(
        {
            "schemaVersion": 2,
            "nodes": [],
            "edges": [],
            "layout": None,
            "outputContract": None,
            "tests": None,
            "metadata": "invalid",
        }
    )

    assert normalized["layout"] == {}
    assert normalized["outputContract"] == {}
    assert normalized["tests"] == []
    assert "metadata" not in normalized
