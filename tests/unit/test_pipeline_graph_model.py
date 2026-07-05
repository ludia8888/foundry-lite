from __future__ import annotations

from typing import cast

import pytest
from foundry_lite.application.services.pipeline_compiler_service import _generated_sql
from foundry_lite.application.services.pipeline_graph_model import (
    node_data,
    output_dataset_ref,
    validate_pipeline_graph,
)
from foundry_lite.application.services.pipeline_graph_validation_service import _cast_suggestions
from foundry_lite.domain.errors import ValidationFailed


def test_pipeline_graph_validation_detects_cycle_and_dangling_edge() -> None:
    graph = _graph(
        nodes=[
            _node("a", "sql"),
            _node("b", "sql"),
            _node("out", "output_dataset", outputDatasetRef="analytics.orders"),
        ],
        edges=[
            {"id": "a-b", "source": "a", "target": "b"},
            {"id": "b-a", "source": "b", "target": "a"},
            {"id": "missing-out", "source": "missing", "target": "out"},
        ],
    )

    result = validate_pipeline_graph(graph)

    codes = {error["code"] for error in _errors(result)}
    assert result["valid"] is False
    assert "dangling_edge_source" in codes
    assert "cycle_detected" not in codes


def test_pipeline_graph_validation_detects_cycle_after_edges_are_well_formed() -> None:
    graph = _graph(
        nodes=[
            _node("a", "sql"),
            _node("b", "sql"),
            _node("out", "output_dataset", outputDatasetRef="analytics.orders"),
        ],
        edges=[
            {"id": "a-b", "source": "a", "target": "b"},
            {"id": "b-a", "source": "b", "target": "a"},
        ],
    )

    result = validate_pipeline_graph(graph)

    assert result["valid"] is False
    assert {error["code"] for error in _errors(result)} == {"cycle_detected"}


def test_pipeline_graph_validation_checks_join_keys_against_input_schemas() -> None:
    graph = _graph(
        nodes=[
            _node("orders", "dataset", schema=[_column("order_id"), _column("customer_id")]),
            _node("customers", "dataset", schema=[_column("id"), _column("segment")]),
            _node("join", "join", leftKey="customer_id", rightKey="customer_id"),
            _node("out", "output_dataset", outputDatasetRef="analytics.orders"),
        ],
        edges=[
            {"id": "orders-join", "source": "orders", "target": "join", "targetHandle": "left"},
            {"id": "customers-join", "source": "customers", "target": "join", "targetHandle": "right"},
            {"id": "join-out", "source": "join", "target": "out"},
        ],
    )

    result = validate_pipeline_graph(graph)

    assert result["valid"] is False
    assert {
        "code": "join_key_missing",
        "nodeId": "join",
        "role": "right",
        "source": "customers",
        "column": "customer_id",
    } in _errors(result)


def test_pipeline_graph_validation_detects_union_schema_mismatch() -> None:
    graph = _graph(
        nodes=[
            _node("orders_a", "dataset", schema=[_column("id"), _column("amount", "float")]),
            _node("orders_b", "dataset", schema=[_column("id"), _column("amount", "string")]),
            _node("union", "union"),
            _node("out", "output_dataset", outputDatasetRef="analytics.orders"),
        ],
        edges=[
            {"id": "a-union", "source": "orders_a", "target": "union"},
            {"id": "b-union", "source": "orders_b", "target": "union"},
            {"id": "union-out", "source": "union", "target": "out"},
        ],
    )

    result = validate_pipeline_graph(graph)

    assert result["valid"] is False
    assert any(error["code"] == "union_schema_mismatch" for error in _errors(result))


def test_pipeline_graph_node_data_accepts_config_and_cast_suggestions() -> None:
    node = {"id": "orders", "type": "dataset", "config": {"datasetRef": "raw.orders"}, "data": {"label": "Orders"}}

    assert node_data(node) == {"datasetRef": "raw.orders", "label": "Orders"}
    assert output_dataset_ref(_graph(nodes=[_node("out", "output_dataset", outputDatasetRef="analytics.orders")])) == (
        "analytics.orders"
    )
    assert _cast_suggestions([_column("created_at"), _column("order_count")]) == [
        {"column": "created_at", "from": "string", "to": "timestamp", "confidence": "medium"},
        {"column": "order_count", "from": "string", "to": "integer", "confidence": "medium"},
    ]


def test_pipeline_generated_sql_rejects_unsafe_refs_and_cast_types() -> None:
    with pytest.raises(ValidationFailed, match="unsafe pipeline dataset reference"):
        _generated_sql("output_dataset", {}, {"input": "raw.orders') }} UNION SELECT 1 --"})

    with pytest.raises(ValidationFailed, match="unsafe pipeline cast type"):
        _generated_sql(
            "select_cast",
            {"columns": [{"name": "id", "type": "VARCHAR) FROM secrets --"}]},
            {"input": "raw.orders"},
        )


def _graph(*, nodes: list[dict[str, object]], edges: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "nodes": nodes,
        "edges": edges or [],
        "layout": {},
        "outputContract": {"columns": [_column("id")]},
        "tests": [],
        "schedule": None,
    }


def _node(node_id: str, node_type: str, **config: object) -> dict[str, object]:
    return {"id": node_id, "type": node_type, "config": dict(config)}


def _column(name: str, column_type: str = "string") -> dict[str, object]:
    return {"name": name, "type": column_type, "nullable": False}


def _errors(result: dict[str, object]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], result["errors"])
