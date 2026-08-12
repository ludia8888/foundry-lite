from __future__ import annotations

from typing import cast

import pytest
from foundry_lite.application.services.pipeline_compiler_service import (
    PipelineCompilerService,
    _alias_for_source,
    _compile_node,
    _CompileContext,
    _generated_sql,
    _transform_api_name,
)
from foundry_lite.application.services.pipeline_graph_model import (
    MAX_PIPELINE_EDGES,
    MAX_PIPELINE_NODES,
    bounded_preview_limit,
    node_by_id,
    node_data,
    output_contract_columns,
    output_dataset_ref,
    pipeline_graph_fingerprint,
    source_dataset_refs,
    topological_node_ids,
    validate_pipeline_graph,
)
from foundry_lite.application.services.pipeline_graph_validation_service import (
    _cast_suggestions,
    _output_contract_test_failures,
    _test_result,
)
from foundry_lite.application.services.pipeline_payloads import (
    bounded_pipeline_limit,
    require_open_branch,
    require_proposal_status,
    required_text,
    schedule_payload,
)
from foundry_lite.application.services.pipeline_run_execution import _compiled_items
from foundry_lite.application.services.pipeline_run_requests import (
    require_deployed as _require_deployed,
)
from foundry_lite.application.services.pipeline_run_requests import (
    require_pipeline_match as _require_pipeline_match,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed


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


def test_pipeline_graph_helpers_bound_limits_and_report_shape_errors() -> None:
    graph = _graph(
        nodes=[
            _node("orders", "dataset", datasetRef="raw.orders"),
            _node("cast", "select_cast", outputDatasetRef="work.cast"),
            _node("out", "output_dataset", outputDatasetRef="analytics.orders"),
        ],
        edges=[
            {"source": "orders", "target": "cast"},
            {"source": "cast", "target": "out"},
        ],
    )

    assert node_by_id(graph, "cast")["type"] == "select_cast"
    assert source_dataset_refs(graph, "cast") == ["raw.orders"]
    assert topological_node_ids(graph) == ["orders", "cast", "out"]
    assert bounded_preview_limit(None) == 500
    assert bounded_preview_limit(0) == 1
    assert bounded_preview_limit(999) == 500

    with pytest.raises(ValidationFailed, match="preview limit must be an integer"):
        bounded_preview_limit("ten")
    with pytest.raises(ValidationFailed, match="pipeline graph node not found"):
        node_by_id(graph, "missing")
    with pytest.raises(ValidationFailed, match="pipeline graph nodes must be a list"):
        validate_pipeline_graph({"nodes": {}, "edges": []})
    with pytest.raises(ValidationFailed, match="pipeline graph edges item must be an object"):
        validate_pipeline_graph({"nodes": [], "edges": ["bad"]})


def test_pipeline_graph_validation_reports_contract_and_input_count_errors() -> None:
    graph = _graph(
        nodes=[
            _node("orders", "dataset", schema=[_column("id")]),
            _node("join", "join"),
            _node("union", "union"),
            _node("out", "output_dataset"),
        ],
        edges=[{"source": "orders", "target": "join"}],
    )
    graph["outputContract"] = {"columns": [{"name": "id"}, {"name": "id", "type": "string"}]}

    result = validate_pipeline_graph(graph)

    codes = {error["code"] for error in _errors(result)}
    assert result["valid"] is False
    assert "join_input_count" in codes
    assert "union_input_count" in codes
    assert "output_column_type_required" in codes
    assert "duplicate_output_column" in codes
    assert {"code": "output_dataset_ref_missing"} in result["warnings"]


def test_pipeline_graph_validation_reports_node_and_join_configuration_errors() -> None:
    graph = _graph(
        nodes=[
            {"id": "", "type": "dataset"},
            _node("dup", "dataset", schema=[_column("id")]),
            _node("dup", "dataset", schema=[_column("id")]),
            _node("strange", "mystery"),
            _node("left", "dataset", schema=[_column("id")]),
            _node("right", "dataset", schema=[_column("customer_id")]),
            _node("join", "join", leftKey="id", rightKey="id", rightNodeId="missing"),
            _node("out", "output_dataset", outputDatasetRef="analytics.orders"),
        ],
        edges=[
            {"source": "left", "target": "join"},
            {"source": "right", "target": "join"},
            {"source": "join", "target": "out"},
            {"source": "out", "target": "missing"},
        ],
    )

    result = validate_pipeline_graph(graph)

    codes = {error["code"] for error in _errors(result)}
    assert "node_id_required" in codes
    assert "duplicate_node_id" in codes
    assert "unsupported_node_type" in codes
    assert "dangling_edge_target" in codes
    assert "join_input_source_missing" in codes


def test_pipeline_graph_validation_reports_join_key_and_node_data_shape_errors() -> None:
    graph = _graph(
        nodes=[
            _node("left", "dataset", schema=[_column("id")]),
            _node("right", "dataset", schema=[_column("id")]),
            _node("join", "join"),
            _node("out", "output_dataset", outputDatasetRef="analytics.orders"),
        ],
        edges=[
            {"source": "left", "target": "join"},
            {"source": "right", "target": "join"},
            {"source": "join", "target": "out"},
        ],
    )
    result = validate_pipeline_graph(graph)

    assert {"code": "join_key_required", "nodeId": "join"} in _errors(result)
    with pytest.raises(ValidationFailed, match="pipeline graph node data must be an object"):
        node_data({"id": "bad", "type": "dataset", "data": "not-an-object"})


def test_pipeline_graph_validation_covers_limit_contract_and_schema_edges() -> None:
    oversized = _graph(
        nodes=[
            *[_node(f"node_{index}", "dataset") for index in range(MAX_PIPELINE_NODES + 1)],
            _node("out", "output_dataset", outputDatasetRef="analytics.orders"),
        ],
        edges=[{"source": "node_0", "target": "out"} for _ in range(MAX_PIPELINE_EDGES + 1)],
    )
    nameless_contract = _graph(nodes=[_node("out", "output_dataset", outputDatasetRef="analytics.orders")])
    nameless_contract["outputContract"] = {"columns": [{"type": "string"}]}
    union_with_partial_schema = _graph(
        nodes=[
            _node("known", "dataset", schema=[_column("id")]),
            _node("unknown", "dataset"),
            _node("union", "union"),
            _node("out", "output_dataset", outputDatasetRef="analytics.orders"),
        ],
        edges=[
            {"source": "known", "target": "union"},
            {"source": "unknown", "target": "union"},
            {"source": "union", "target": "out"},
        ],
    )
    configured_join = _graph(
        nodes=[
            _node("left", "dataset", schema=[_column("id")]),
            _node("right", "dataset", schema=[_column("id")]),
            _node("join", "join", leftKey="id", rightKey="id", leftNodeId="left", rightNodeId="right"),
            _node("out", "output_dataset", outputDatasetRef="analytics.orders"),
        ],
        edges=[
            {"source": "left", "target": "join"},
            {"source": "right", "target": "join"},
            {"source": "join", "target": "out"},
        ],
    )

    oversized_codes = {error["code"] for error in _errors(validate_pipeline_graph(oversized))}
    nameless_codes = {error["code"] for error in _errors(validate_pipeline_graph(nameless_contract))}

    assert "too_many_nodes" in oversized_codes
    assert "too_many_edges" in oversized_codes
    assert "output_column_name_required" in nameless_codes
    assert validate_pipeline_graph(union_with_partial_schema)["valid"] is True
    assert validate_pipeline_graph(configured_join)["valid"] is True
    assert output_contract_columns({"outputContract": "not-an-object"}) == []
    assert node_data({"id": "empty", "type": "dataset", "config": None, "data": None}) == {}


def test_pipeline_generated_sql_rejects_unsafe_refs_and_cast_types() -> None:
    join_sql = _generated_sql(
        "join",
        {"leftKey": "id", "rightKey": "id", "joinType": "left"},
        {"a": "raw.a", "b": "raw.b"},
    )
    assert join_sql == (
        "SELECT * FROM {{ input('raw.a') }} AS left_input LEFT JOIN {{ input('raw.b') }} AS right_input "
        'ON left_input."id" = right_input."id"'
    )
    assert _generated_sql("union", {}, {"a": "raw.a", "b": "raw.b"}) == (
        "SELECT * FROM {{ input('raw.a') }} UNION ALL SELECT * FROM {{ input('raw.b') }}"
    )
    assert (
        _generated_sql(
            "select_cast",
            {"columns": [{"source": "amount", "name": "amount_float", "type": "DOUBLE"}]},
            {"input": "raw.orders"},
        )
        == 'SELECT CAST("amount" AS DOUBLE) AS "amount_float" FROM {{ input(\'raw.orders\') }}'
    )

    with pytest.raises(ValidationFailed, match="output_dataset node requires exactly one input"):
        _generated_sql("output_dataset", {}, {})
    with pytest.raises(ValidationFailed, match="union node requires at least two inputs"):
        _generated_sql("union", {}, {"a": "raw.a"})
    with pytest.raises(ValidationFailed, match="unsupported join type"):
        _generated_sql(
            "join",
            {"leftKey": "id", "rightKey": "id", "joinType": "sideways"},
            {"a": "raw.a", "b": "raw.b"},
        )
    with pytest.raises(ValidationFailed, match="join node requires exactly two inputs"):
        _generated_sql("join", {"leftKey": "id", "rightKey": "id"}, {"a": "raw.a"})
    with pytest.raises(ValidationFailed, match="select_cast node requires exactly one input"):
        _generated_sql("select_cast", {"columns": [{"name": "id"}]}, {"a": "raw.a", "b": "raw.b"})
    with pytest.raises(ValidationFailed, match="select_cast node requires columns"):
        _generated_sql("select_cast", {}, {"a": "raw.a"})
    with pytest.raises(ValidationFailed, match="unsupported generated SQL node"):
        _generated_sql("pivot", {}, {"a": "raw.a"})
    with pytest.raises(ValidationFailed, match="unsafe pipeline column identifier"):
        _generated_sql("select_cast", {"columns": [{"name": "bad;drop"}]}, {"a": "raw.a"})
    with pytest.raises(ValidationFailed, match="unsafe pipeline dataset reference"):
        _generated_sql("output_dataset", {}, {"input": "raw.orders') }} UNION SELECT 1 --"})

    with pytest.raises(ValidationFailed, match="unsafe pipeline cast type"):
        _generated_sql(
            "select_cast",
            {"columns": [{"name": "id", "type": "VARCHAR) FROM secrets --"}]},
            {"input": "raw.orders"},
        )


def test_pipeline_compiler_registers_python_and_generated_nodes_without_raw_paths() -> None:
    compiler = PipelineCompilerService()
    datasets = _DatasetRegistry()
    transforms = _TransformRegistry()
    compiler.bind_collaborators({"dataset_registry_service": datasets, "transform_service": transforms})

    compiled = compiler.compile_graph(
        pipeline_id="score_pipeline",
        version_id="version-1",
        graph=_python_pipeline_graph(),
        ctx=RequestContext(tenant_id="tenant-a", actor_user_id="user-a"),
    )

    assert [item["nodeId"] for item in compiled["transforms"]] == ["join", "cast", "score", "out"]
    assert datasets.refs == ["work.joined", "work.cast", "work.scored", "analytics.scored"]
    assert transforms.python_calls == [
        {
            "api_name": "pipeline_score_pipeline_version_1_score",
            "ctx": True,
            "function_name": "transform",
            "inputs": {"cast": "work.cast"},
            "output_dataset_ref": "work.scored",
            "source_code": "def transform(inputs):\n    return []",
        }
    ]
    assert transforms.sql_calls[-1]["sql"] == "SELECT * FROM {{ input('work.scored') }}"


def test_pipeline_compiler_compiles_canonical_v2_tabular_graph_without_mutating_it() -> None:
    compiler = PipelineCompilerService()
    datasets = _DatasetRegistry()
    transforms = _TransformRegistry()
    compiler.bind_collaborators({"dataset_registry_service": datasets, "transform_service": transforms})
    graph = _tabular_pipeline_graph_v2()

    compiled = compiler.compile_graph(
        pipeline_id="v2_orders",
        version_id="version-2",
        graph=graph,
        ctx=RequestContext(tenant_id="tenant-a", actor_user_id="user-a"),
    )

    assert graph["nodes"][0].get("type") is None
    assert [item["nodeId"] for item in compiled["transforms"]] == ["cast", "out"]
    assert datasets.refs == ["work.v2_cast", "analytics.v2_orders"]
    assert transforms.sql_calls[0]["inputs"] == {"orders": "raw.orders"}
    assert transforms.sql_calls[-1]["sql"] == "SELECT \"amount\" FROM {{ input('work.v2_cast') }}"


def test_pipeline_compiler_preserves_v2_named_join_port_roles() -> None:
    compiler = PipelineCompilerService()
    transforms = _TransformRegistry()
    compiler.bind_collaborators({"dataset_registry_service": _DatasetRegistry(), "transform_service": transforms})

    compiler.compile_graph(
        pipeline_id="v2_join",
        version_id="version-2",
        graph=_joined_pipeline_graph_v2(),
        ctx=RequestContext(tenant_id="tenant-a", actor_user_id="user-a"),
    )

    join_call = transforms.sql_calls[0]
    assert join_call["inputs"] == {"z_left": "raw.left", "a_right": "raw.right"}
    assert "{{ input('raw.left') }} AS left_input" in str(join_call["sql"])
    assert "{{ input('raw.right') }} AS right_input" in str(join_call["sql"])


def test_pipeline_compiler_rejects_bad_nodes_and_normalizes_generated_names() -> None:
    compiler = PipelineCompilerService()
    datasets = _DatasetRegistry()
    transforms = _TransformRegistry()
    compiler.bind_collaborators({"dataset_registry_service": datasets, "transform_service": transforms})
    compile_ctx = _CompileContext(
        dataset_registry_service=datasets,
        transform_service=transforms,
        request_context=RequestContext(tenant_id="tenant-a", actor_user_id="user-a"),
        pipeline_id="1-pipeline",
        version_id="version-1",
        graph=_graph(nodes=[]),
        refs_by_node={},
    )

    assert _alias_for_source("!!!", 3) == "input_3"
    assert _transform_api_name("1-pipeline", "version-1", "node-1") == "pipeline_1_pipeline_version_1_node_1"
    with pytest.raises(ValidationFailed, match="unsupported pipeline node type"):
        _compile_node(compile_ctx, {"id": "bad", "type": "mystery", "config": {}})
    with pytest.raises(ValidationFailed, match="pipeline node datasetRef is required"):
        compiler.compile_graph(
            pipeline_id="broken",
            version_id="version-1",
            graph=_graph(nodes=[_node("orders", "dataset"), _node("out", "output_dataset", outputDatasetRef="a.b")]),
            ctx=RequestContext(tenant_id="tenant-a", actor_user_id="user-a"),
        )
    with pytest.raises(ValidationFailed, match="pipeline node sourceCode is required"):
        compiler.compile_graph(
            pipeline_id="broken",
            version_id="version-1",
            graph=_graph(
                nodes=[
                    _node("orders", "dataset", datasetRef="raw.orders"),
                    _node("py", "python", outputDatasetRef="work.py"),
                    _node("out", "output_dataset", outputDatasetRef="analytics.py"),
                ],
                edges=[{"source": "orders", "target": "py"}, {"source": "py", "target": "out"}],
            ),
            ctx=RequestContext(tenant_id="tenant-a", actor_user_id="user-a"),
        )


def test_pipeline_payload_helpers_guard_bounds_and_terminal_states() -> None:
    assert bounded_pipeline_limit(0) == 1
    assert bounded_pipeline_limit(500, cap=20) == 20
    assert required_text("  ok  ", "field") == "ok"
    assert schedule_payload(None) is None

    with pytest.raises(ValidationFailed, match="field is required"):
        required_text(" ", "field")
    with pytest.raises(Exception, match="pipeline branch is not open"):
        require_open_branch(cast(dict, {"id": "branch-a", "status": "merged"}))
    with pytest.raises(Exception, match="pipeline proposal is not in the required state"):
        require_proposal_status(cast(dict, {"id": "proposal-a", "status": "executed"}), ("submitted",))


def test_pipeline_validation_and_run_helpers_cover_failure_shapes() -> None:
    missing_output_graph = {
        "nodes": [{"id": "out", "type": "output_dataset", "config": {}}],
        "edges": [],
        "layout": {},
        "outputContract": {"columns": []},
        "tests": "not-a-list",
        "schedule": None,
    }
    missing_type_graph = {
        "nodes": [{"id": "out", "type": "output_dataset", "config": {"outputDatasetRef": "analytics.orders"}}],
        "edges": [],
        "layout": {},
        "outputContract": {"columns": [{"name": "id"}]},
        "tests": [],
        "schedule": None,
    }

    result = _test_result(
        cast(
            dict,
            {
                "pipeline_id": "p",
                "id": "b",
                "graph": missing_output_graph,
                "graph_fingerprint": pipeline_graph_fingerprint(missing_output_graph),
            },
        )
    )

    assert result["status"] == "failed"
    assert result["testCount"] == 1
    assert result["declaredTestCount"] == 0
    assert result["proofKind"] == "static_graph_output_contract"
    assert result["isDataExecution"] is False
    assert result["evaluatedChecks"] == ["graph_validation", "output_descriptor_contracts"]
    assert _output_contract_test_failures({"nodes": [], "outputContract": {"columns": []}}) == []
    assert (
        _output_contract_test_failures(
            {
                "nodes": [
                    {
                        "id": "media-out",
                        "kind": "output",
                        "descriptorId": "output.media_set",
                        "config": {"mediaSetRef": "media.target"},
                    }
                ],
                "outputContract": {"columns": []},
            }
        )
        == []
    )
    assert (
        _output_contract_test_failures(
            {
                "nodes": [
                    {
                        "id": "dataset-out",
                        "kind": "output",
                        "descriptorId": "output.dataset",
                        "config": {"outputDatasetRef": "analytics.orders"},
                    }
                ],
                "outputContract": {"columns": []},
            }
        )
        == []
    )
    assert _cast_suggestions(
        [
            _column("unit_price"),
            _column("conversion_rate"),
            _column("plain_name"),
            _column("already_amount", "float"),
        ]
    ) == [
        {"column": "unit_price", "from": "string", "to": "float", "confidence": "medium"},
        {"column": "conversion_rate", "from": "string", "to": "float", "confidence": "medium"},
    ]
    assert _compiled_items({"transforms": "not-a-list"}) == []
    assert _compiled_items({"transforms": [{"apiName": "run_a"}, {"nodeId": "missing"}, "bad"]}) == [
        {"nodeId": "missing"}
    ]

    with pytest.raises(ValidationFailed, match="output contract column type is required"):
        _output_contract_test_failures(missing_type_graph)
    with pytest.raises(ValidationFailed, match="different pipeline"):
        _require_pipeline_match(cast(dict, {"id": "version-a", "pipeline_id": "other"}), "expected")
    with pytest.raises(ConflictDetected, match="pipeline version is not deployed"):
        _require_deployed(cast(dict, {"id": "version-a", "deployed_at": None}))


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


def _python_pipeline_graph() -> dict[str, object]:
    return _graph(
        nodes=[
            _node("orders", "dataset", datasetRef="raw.orders", schema=[_column("id"), _column("amount", "string")]),
            _node("customers", "dataset", datasetRef="raw.customers", schema=[_column("id"), _column("segment")]),
            _node("join", "join", leftKey="id", rightKey="id", outputDatasetRef="work.joined"),
            _node(
                "cast",
                "select_cast",
                outputDatasetRef="work.cast",
                columns=[{"source": "amount", "name": "amount", "type": "DOUBLE"}],
            ),
            _node(
                "score",
                "python",
                outputDatasetRef="work.scored",
                sourceCode="def transform(inputs):\n    return []\n",
                functionName="transform",
            ),
            _node("out", "output_dataset", outputDatasetRef="analytics.scored"),
        ],
        edges=[
            {"source": "orders", "target": "join", "targetHandle": "left"},
            {"source": "customers", "target": "join", "targetHandle": "right"},
            {"source": "join", "target": "cast"},
            {"source": "cast", "target": "score"},
            {"source": "score", "target": "out"},
        ],
    )


def _tabular_pipeline_graph_v2() -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "nodes": [
            {
                "id": "orders",
                "kind": "source",
                "descriptorId": "source.dataset",
                "specVersion": 1,
                "config": {"datasetRef": "raw.orders"},
            },
            {
                "id": "cast",
                "kind": "transform",
                "descriptorId": "transform.select_cast",
                "specVersion": 1,
                "config": {
                    "columns": [{"source": "amount", "name": "amount", "type": "DOUBLE"}],
                    "outputDatasetRef": "work.v2_cast",
                },
            },
            {
                "id": "out",
                "kind": "output",
                "descriptorId": "output.dataset",
                "specVersion": 1,
                "config": {"outputDatasetRef": "analytics.v2_orders"},
            },
        ],
        "edges": [
            {
                "id": "orders-cast",
                "sourceNodeId": "orders",
                "sourcePortId": "dataset",
                "targetNodeId": "cast",
                "targetPortId": "input",
            },
            {
                "id": "cast-out",
                "sourceNodeId": "cast",
                "sourcePortId": "dataset",
                "targetNodeId": "out",
                "targetPortId": "input",
            },
        ],
        "layout": {},
        "outputContract": {"columns": [_column("amount", "double")]},
        "tests": [],
        "schedule": None,
    }


def _joined_pipeline_graph_v2() -> dict[str, object]:
    graph = _tabular_pipeline_graph_v2()
    graph["nodes"] = [
        {
            "id": node_id,
            "kind": "source",
            "descriptorId": "source.dataset",
            "specVersion": 1,
            "config": {"datasetRef": dataset_ref},
        }
        for node_id, dataset_ref in (("z_left", "raw.left"), ("a_right", "raw.right"))
    ] + [
        {
            "id": "join",
            "kind": "transform",
            "descriptorId": "transform.join",
            "specVersion": 1,
            "config": {
                "leftKey": "id",
                "rightKey": "id",
                "joinType": "inner",
                "outputDatasetRef": "work.joined",
            },
        },
        {
            "id": "out",
            "kind": "output",
            "descriptorId": "output.dataset",
            "specVersion": 1,
            "config": {"outputDatasetRef": "analytics.joined"},
        },
    ]
    graph["edges"] = [
        _v2_edge("right", "a_right", "dataset", "join", "right"),
        _v2_edge("left", "z_left", "dataset", "join", "left"),
        _v2_edge("out", "join", "dataset", "out", "input"),
    ]
    return graph


def _v2_edge(edge_id: str, source: str, source_port: str, target: str, target_port: str) -> dict[str, object]:
    return {
        "id": edge_id,
        "sourceNodeId": source,
        "sourcePortId": source_port,
        "targetNodeId": target,
        "targetPortId": target_port,
    }


class _DatasetRegistry:
    def __init__(self) -> None:
        self.refs: list[str] = []
        self.datasets: dict[str, dict[str, object]] = {}

    def ensure_dataset(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        classification: str | None = None,
    ) -> dict[str, object]:
        self.refs.append(dataset_ref)
        row = {
            "id": dataset_ref,
            "ctx": ctx is not None,
            "classification": classification or "INTERNAL",
        }
        self.datasets[dataset_ref] = row
        return row

    def get_dataset(self, dataset_ref: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self.datasets.get(
            dataset_ref,
            {"id": dataset_ref, "ctx": ctx is not None, "classification": "INTERNAL"},
        )

    def find_dataset(self, dataset_ref: str, *, ctx: RequestContext | None = None) -> dict[str, object] | None:
        del ctx
        return self.datasets.get(dataset_ref)


class _TransformRegistry:
    def __init__(self) -> None:
        self.sql_calls: list[dict[str, object]] = []
        self.python_calls: list[dict[str, object]] = []

    def register_sql_transform(
        self,
        api_name: str,
        *,
        sql: str,
        inputs: dict[str, str],
        output_dataset_ref: str,
        ctx: RequestContext | None = None,
    ) -> None:
        self.sql_calls.append(
            {
                "api_name": api_name,
                "sql": sql,
                "inputs": dict(inputs),
                "output_dataset_ref": output_dataset_ref,
                "ctx": ctx is not None,
            }
        )

    def register_python_transform(
        self,
        api_name: str,
        *,
        source_code: str,
        function_name: str | None,
        inputs: dict[str, str],
        output_dataset_ref: str,
        ctx: RequestContext | None = None,
    ) -> None:
        self.python_calls.append(
            {
                "api_name": api_name,
                "source_code": source_code,
                "function_name": function_name,
                "inputs": dict(inputs),
                "output_dataset_ref": output_dataset_ref,
                "ctx": ctx is not None,
            }
        )
