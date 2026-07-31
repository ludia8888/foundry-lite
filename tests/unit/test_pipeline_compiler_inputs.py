"""A self-join (two edges from one source) must compile into two distinct inputs.

Input aliases were derived from the source node id and only appended an index when
the slug was empty, so two edges from the same source collapsed to one dict entry.
The join then saw a single input and compilation failed with "join node requires
exactly two inputs" even though graph validation had accepted the self-join.
"""

from __future__ import annotations

from foundry_lite.application.services import pipeline_compiler_service as compiler


def _self_join_graph() -> dict[str, object]:
    return {
        "nodes": [
            {"id": "src", "type": "dataset", "data": {"datasetRef": "raw.orders"}},
            {"id": "join", "type": "join", "data": {"leftKey": "id", "rightKey": "parent_id", "joinType": "inner"}},
        ],
        "edges": [
            {"source": "src", "target": "join"},
            {"source": "src", "target": "join"},
        ],
    }


def test_self_join_yields_two_distinct_inputs_and_compiles() -> None:
    graph = _self_join_graph()
    refs = compiler._initial_refs_by_node(graph)
    join_node = next(node for node in graph["nodes"] if node["id"] == "join")  # type: ignore[index]

    inputs = compiler._inputs_for_node(graph, join_node, refs)

    # Two incoming edges -> two distinct input entries, both mapping to the source.
    assert len(inputs) == 2
    assert set(inputs.values()) == {"raw.orders"}

    sql = compiler._generated_sql("join", join_node["data"], inputs)  # type: ignore[index]
    assert "left_input" in sql
    assert "right_input" in sql


def test_distinct_source_join_keeps_stable_slug_aliases() -> None:
    """Distinct sources keep their slug alias (Python transforms bind inputs by alias)."""
    graph = {
        "nodes": [
            {"id": "orders", "type": "dataset", "data": {"datasetRef": "raw.orders"}},
            {"id": "customers", "type": "dataset", "data": {"datasetRef": "raw.customers"}},
            {"id": "join", "type": "join", "data": {"leftKey": "id", "rightKey": "order_id"}},
        ],
        "edges": [
            {"source": "orders", "target": "join"},
            {"source": "customers", "target": "join"},
        ],
    }
    refs = compiler._initial_refs_by_node(graph)
    join_node = next(node for node in graph["nodes"] if node["id"] == "join")

    inputs = compiler._inputs_for_node(graph, join_node, refs)

    assert inputs == {"orders": "raw.orders", "customers": "raw.customers"}
