from __future__ import annotations

from copy import deepcopy
from typing import cast

import pytest
from foundry_lite.application.services.pipeline_graph_model import (
    PipelineArtifactKind,
    node_data,
    normalize_pipeline_graph,
    output_dataset_refs,
    pipeline_graph_fingerprint,
    pipeline_node_descriptor_payloads,
    validate_pipeline_graph,
)
from foundry_lite.application.services.pipeline_graph_v2_validation import validate_pipeline_graph_v2
from foundry_lite.domain.errors import ValidationFailed


def test_descriptor_catalog_exposes_artifact_ports_and_honest_runtime_availability() -> None:
    descriptors = {item["descriptorId"]: item for item in pipeline_node_descriptor_payloads()}

    assert descriptors["transform.join"]["availability"] == "legacy_executable"
    assert descriptors["transform.join"]["runtimeCapability"] == "tabular_v1_compiler"
    executable = {
        "source.media_set",
        "transform.media",
        "transform.document_extract",
        "transform.chunk",
        "transform.embedding.text",
        "transform.use_llm",
        "bridge.media_to_table_rows",
        "bridge.content_units_to_dataset",
        "output.media_set",
    }
    unavailable = {
        descriptor_id
        for descriptor_id in executable
        if descriptors[descriptor_id]["availability"] != "graph_v2_executable"
    }
    assert unavailable == set()
    assert descriptors["source.media_set"]["outputPorts"] == [
        {"portId": "media", "artifactKind": PipelineArtifactKind.MEDIA_SET_SELECTION.value}
    ]
    assert descriptors["transform.document_extract"]["inputPorts"] == [
        {
            "portId": "media",
            "acceptedArtifactKinds": ["media_set_selection", "media_derivative_set"],
            "cardinality": "one",
            "required": True,
        }
    ]
    assert descriptors["transform.use_llm"]["inputPorts"] == [
        {
            "portId": "input",
            "acceptedArtifactKinds": ["dataset_version"],
            "cardinality": "one",
            "required": True,
        }
    ]
    assert descriptors["transform.use_llm"]["runtimeCapability"] == "governed_model_gateway_runtime"
    assert descriptors["output.media_set"]["outputPorts"] == [
        {"portId": "media", "artifactKind": PipelineArtifactKind.MEDIA_SET_SELECTION.value}
    ]
    assert descriptors["output.media_set"]["runtimeCapability"] == "media_output_runtime"
    assert descriptors["source.stream"]["availability"] == "graph_v2_executable"
    assert descriptors["transform.embedding.vision"]["availability"] == "validation_only"
    assert descriptors["output.virtual_table"]["availability"] == "validation_only"
    assert descriptors["output.geospatial"]["availability"] == "graph_v2_executable"
    assert descriptors["output.semantic_index"]["availability"] == "governed_candidate"
    assert descriptors["output.semantic_index"]["runtimeCapability"] == "semantic_index_candidate_runtime"
    assert descriptors["output.ontology"]["availability"] == "governed_candidate"
    assert descriptors["output.ontology"]["runtimeCapability"] == "ontology_mapping_candidate_runtime"


def test_v1_normalizer_is_pure_and_assigns_join_ports_without_edge_order_semantics() -> None:
    first = _legacy_join_graph(edge_sources=("z_orders", "a_customers"))
    second = _legacy_join_graph(edge_sources=("a_customers", "z_orders"))
    snapshot = deepcopy(first)
    persisted_fingerprint = pipeline_graph_fingerprint(first)

    normalized_first = normalize_pipeline_graph(first)
    normalized_second = normalize_pipeline_graph(second)

    assert first == snapshot
    assert pipeline_graph_fingerprint(first) == persisted_fingerprint
    assert normalized_first == normalized_second
    assert normalized_first["schemaVersion"] == 2
    assert _target_ports(normalized_first, "join") == {
        "a_customers": "left",
        "z_orders": "right",
    }
    join = next(node for node in normalized_first["nodes"] if node["id"] == "join")
    assert join["config"]["joinType"] == "full outer"


def test_v1_validation_reports_input_and_canonical_fingerprints_for_valid_graph() -> None:
    graph = _legacy_graph(
        [
            {"id": "orders", "type": "dataset", "config": {"datasetRef": "raw.orders"}},
            _legacy_output(),
        ],
        edges=[{"source": "orders", "target": "out"}],
    )
    input_fingerprint = pipeline_graph_fingerprint(graph)
    canonical = normalize_pipeline_graph(graph)

    validation = validate_pipeline_graph(graph)

    assert validation["valid"] is True
    assert validation["fingerprint"] == input_fingerprint
    assert validation["normalizedGraph"] == canonical
    assert validation["normalizedFingerprint"] == pipeline_graph_fingerprint(canonical)


def test_top_level_schema_is_the_canonical_read_over_stale_config_schema() -> None:
    node = {
        "id": "orders",
        "type": "dataset",
        "config": {"datasetRef": "raw.orders", "schema": [_column("stale")]},
        "schema": [_column("current")],
    }

    assert node_data(node)["schema"] == [_column("current")]
    normalized = normalize_pipeline_graph(_legacy_graph([node, _legacy_output()]))
    source = next(item for item in normalized["nodes"] if item["id"] == "orders")
    assert source["config"]["schema"] == [_column("current")]


def test_v1_output_dataset_ref_alias_is_promoted_into_canonical_v2_config() -> None:
    output = _legacy_output()
    config = cast(dict[str, object], output["config"])
    config["datasetRef"] = "clean.orders"
    config.pop("outputDatasetRef")

    source = {"id": "orders", "type": "dataset", "config": {"datasetRef": "raw.orders"}}
    normalized = normalize_pipeline_graph(
        _legacy_graph([source, output], edges=[{"source": "orders", "target": "out"}])
    )

    output_node = next(item for item in normalized["nodes"] if item["id"] == "out")
    assert output_node["config"]["outputDatasetRef"] == "clean.orders"


def test_v2_validation_accepts_multiple_typed_outputs_and_preserves_raw_fingerprint() -> None:
    graph = _v2_graph(
        nodes=[
            _v2_node(
                "orders",
                "source",
                "source.dataset",
                datasetRef="raw.orders",
                schema=[_column("id")],
            ),
            _v2_node("dataset_out", "output", "output.dataset", outputDatasetRef="analytics.orders"),
            _v2_node("ontology_out", "output", "output.ontology", mappingRef="OrderDocument"),
        ],
        edges=[
            _v2_edge("orders-dataset", "orders", "dataset", "dataset_out", "input"),
            _v2_edge("orders-ontology", "orders", "dataset", "ontology_out", "input"),
        ],
    )
    raw_fingerprint = pipeline_graph_fingerprint(graph)

    direct_result = validate_pipeline_graph_v2(graph)
    result = validate_pipeline_graph(graph)

    assert direct_result["valid"] is True
    assert result["valid"] is True
    assert result["fingerprint"] == raw_fingerprint
    assert output_dataset_refs(graph) == ["analytics.orders"]
    warnings = _rows(result["warnings"])
    assert {warning["code"] for warning in warnings} == {"node_commits_governed_candidate_only"}
    assert warnings[0]["servingAssetCreated"] is False
    assert warnings[0]["promotionRequired"] is True
    normalized = cast(dict[str, object], result["normalizedGraph"])
    assert normalized["schemaVersion"] == 2
    assert pipeline_graph_fingerprint(normalized) != raw_fingerprint
    assert result["normalizedFingerprint"] == pipeline_graph_fingerprint(normalized)


def test_v2_normalizer_preserves_unknown_node_contract_for_read_only_compatibility() -> None:
    graph = _v2_graph(
        nodes=[
            _v2_node("orders", "source", "source.dataset", datasetRef="raw.orders"),
            _v2_node(
                "future",
                "transform",
                "transform.future_semantic",
                prompt="Interpret the source without losing coordinates.",
                nested={"mode": "future"},
            ),
            _v2_node("out", "output", "output.dataset", outputDatasetRef="analytics.orders"),
        ],
        edges=[
            _v2_edge("orders-future", "orders", "dataset", "future", "input"),
            _v2_edge("future-out", "future", "dataset", "out", "input"),
        ],
    )
    future_node = next(node for node in cast(list[dict[str, object]], graph["nodes"]) if node["id"] == "future")
    future_node["futureNodeContract"] = {
        "introducedIn": 3,
        "preservationMode": "opaque",
    }
    future_edge = cast(list[dict[str, object]], graph["edges"])[1]
    future_edge["futureEdgeContract"] = {"lineageMode": "future-property-level"}
    graph["futureGraphContract"] = {
        "introducedIn": 3,
        "preservationMode": "opaque",
    }
    snapshot = deepcopy(graph)

    normalized = normalize_pipeline_graph(graph)
    renormalized = normalize_pipeline_graph(normalized)
    future = next(node for node in normalized["nodes"] if node["id"] == "future")
    normalized_edge = next(edge for edge in normalized["edges"] if edge["id"] == "future-out")
    errors = _rows(validate_pipeline_graph(normalized)["errors"])

    assert graph == snapshot
    assert renormalized == normalized
    assert future["config"] == {
        "prompt": "Interpret the source without losing coordinates.",
        "nested": {"mode": "future"},
    }
    assert future["futureNodeContract"] == future_node["futureNodeContract"]
    assert normalized_edge["futureEdgeContract"] == future_edge["futureEdgeContract"]
    assert normalized["futureGraphContract"] == graph["futureGraphContract"]
    assert {
        "code": "node_descriptor_not_found",
        "nodeId": "future",
        "descriptorId": "transform.future_semantic",
        "specVersion": 1,
    } in errors


def test_v2_join_normalizes_full_alias_and_requires_distinct_named_ports() -> None:
    graph = _v2_join_graph("full")

    result = validate_pipeline_graph(graph)

    assert result["valid"] is True
    normalized = cast(dict[str, object], result["normalizedGraph"])
    nodes = cast(list[dict[str, object]], normalized["nodes"])
    join = next(node for node in nodes if node["id"] == "join")
    assert cast(dict[str, object], join["config"])["joinType"] == "full outer"

    invalid = deepcopy(graph)
    invalid_edges = cast(list[dict[str, object]], invalid["edges"])
    invalid_edges[1]["targetPortId"] = "left"
    errors = _rows(validate_pipeline_graph(invalid)["errors"])
    assert {error["code"] for error in errors} >= {
        "required_input_port_missing",
        "input_port_cardinality",
    }


def test_v2_validation_rejects_artifact_mismatch_unknown_ports_and_bad_config() -> None:
    graph = _v2_graph(
        nodes=[
            _v2_node("media", "source", "source.media_set", mediaSetRef="media.documents"),
            _v2_node("out", "output", "output.dataset", outputDatasetRef="analytics.documents"),
        ],
        edges=[_v2_edge("bad-edge", "media", "missing", "out", "input")],
    )

    errors = _rows(validate_pipeline_graph(graph)["errors"])

    assert {error["code"] for error in errors} >= {"source_port_not_found"}

    cast(list[dict[str, object]], graph["edges"])[0]["sourcePortId"] = "media"
    errors = _rows(validate_pipeline_graph(graph)["errors"])
    assert {error["code"] for error in errors} >= {"artifact_kind_mismatch"}

    cast(dict[str, object], cast(list[dict[str, object]], graph["nodes"])[1]["config"])["outputDatasetRef"] = ""
    errors = _rows(validate_pipeline_graph(graph)["errors"])
    assert {error["code"] for error in errors} >= {"node_config_required"}


def test_use_llm_rejects_direct_media_and_accepts_explicit_media_reference_bridge() -> None:
    graph = _v2_graph(
        nodes=[
            _v2_node("media", "source", "source.media_set", mediaSetRef="media.documents"),
            _v2_node("semantic", "transform", "transform.use_llm", **_llm_config()),
            _v2_node("out", "output", "output.dataset", outputDatasetRef="analytics.documents"),
        ],
        edges=[
            _v2_edge("media-semantic", "media", "media", "semantic", "input"),
            _v2_edge("semantic-out", "semantic", "dataset", "out", "input"),
        ],
    )

    direct_errors = _rows(validate_pipeline_graph(graph)["errors"])

    assert {error["code"] for error in direct_errors} >= {"artifact_kind_mismatch"}

    nodes = cast(list[dict[str, object]], graph["nodes"])
    nodes.insert(1, _v2_node("rows", "transform", "bridge.media_to_table_rows"))
    graph["edges"] = [
        _v2_edge("media-rows", "media", "media", "rows", "media"),
        _v2_edge("rows-semantic", "rows", "dataset", "semantic", "input"),
        _v2_edge("semantic-out", "semantic", "dataset", "out", "input"),
    ]
    assert validate_pipeline_graph(graph)["valid"] is True


def test_trained_model_requires_durable_pipeline_reusable_import() -> None:
    graph = _v2_graph(
        nodes=[
            _v2_node("orders", "source", "source.dataset", datasetRef="raw.orders"),
            _v2_node(
                "risk",
                "transform",
                "transform.trained_model",
                modelRef="demo.transaction-risk",
                modelBranch="master",
                fallbackBranches=["master"],
                inputMappings={"amount": "$amount"},
                outputMappings={"riskScore": "risk_score", "decision": "decision"},
            ),
            _v2_node("out", "output", "output.dataset", outputDatasetRef="analytics.orders"),
        ],
        edges=[
            _v2_edge("orders-risk", "orders", "dataset", "risk", "input"),
            _v2_edge("risk-out", "risk", "dataset", "out", "input"),
        ],
    )

    errors = _rows(validate_pipeline_graph(graph)["errors"])
    assert {error["code"] for error in errors} >= {"trained_model_not_imported"}

    graph["metadata"] = {"reusables": {"trainedModels": ["demo.transaction-risk"]}}
    assert validate_pipeline_graph(graph)["valid"] is True


def test_v2_validation_rejects_legacy_node_shell_and_missing_output() -> None:
    graph = _v2_graph(
        nodes=[
            {
                **_v2_node("orders", "source", "source.dataset", datasetRef="raw.orders"),
                "data": {"label": "legacy"},
            }
        ],
        edges=[],
    )

    errors = _rows(validate_pipeline_graph(graph)["errors"])

    assert {error["code"] for error in errors} >= {
        "legacy_node_data_not_allowed",
        "output_node_count",
    }


def test_v1_output_rule_remains_exactly_one_for_compatibility() -> None:
    graph = _legacy_graph(
        [
            {"id": "orders", "type": "dataset", "config": {"datasetRef": "raw.orders"}},
            _legacy_output("out_a"),
            _legacy_output("out_b"),
        ],
        edges=[
            {"source": "orders", "target": "out_a"},
            {"source": "orders", "target": "out_b"},
        ],
    )

    errors = _rows(validate_pipeline_graph(graph)["errors"])

    assert {"code": "output_dataset_count", "expected": 1, "actual": 2} in errors


def test_v2_shape_validation_reports_limits_duplicate_ids_and_missing_fields() -> None:
    output = _v2_node("out", "output", "output.dataset", outputDatasetRef="analytics.out")
    malformed = {
        "schemaVersion": 2,
        "nodes": [
            output,
            {**output},
            {"id": "", "kind": "future", "descriptorId": "", "specVersion": True, "config": []},
        ],
        "edges": [
            {
                "id": "",
                "sourceNodeId": "",
                "sourcePortId": "",
                "targetNodeId": "",
                "targetPortId": "",
            },
            {
                **_v2_edge("duplicate", "out", "dataset", "out", "input"),
            },
            {
                **_v2_edge("duplicate", "out", "dataset", "out", "input"),
            },
        ],
    }

    errors = _rows(validate_pipeline_graph_v2(malformed)["errors"])

    assert {str(error["code"]) for error in errors} >= {
        "duplicate_node_id",
        "node_id_required",
        "unsupported_node_kind",
        "node_descriptor_required",
        "node_spec_version_required",
        "node_config_required",
        "edge_id_required",
        "duplicate_edge_id",
        "edge_field_required",
    }

    nodes = [output] * 201
    edges = [_v2_edge(str(index), "out", "dataset", "out", "input") for index in range(601)]
    limit_errors = _rows(validate_pipeline_graph_v2(_v2_graph(nodes=nodes, edges=edges))["errors"])
    assert {str(error["code"]) for error in limit_errors} >= {"too_many_nodes", "too_many_edges"}


@pytest.mark.parametrize(
    ("graph", "field"),
    [
        ({"nodes": {}, "edges": []}, "nodes"),
        ({"nodes": [], "edges": {}}, "edges"),
        ({"nodes": [None], "edges": []}, "nodes"),
        ({"nodes": [], "edges": [None]}, "edges"),
    ],
)
def test_v2_graph_requires_object_arrays(graph: dict[str, object], field: str) -> None:
    with pytest.raises(ValidationFailed) as raised:
        validate_pipeline_graph_v2(graph)

    assert raised.value.details.get("field") == field or raised.value.details.get("index") == 0


def test_v2_descriptor_validation_reports_kind_type_value_and_runtime_warning() -> None:
    graph = _v2_graph(
        nodes=[
            _v2_node("source", "transform", "source.dataset", datasetRef="raw.orders"),
            _v2_node(
                "join",
                "transform",
                "transform.join",
                leftKey="id",
                rightKey="id",
                joinType=42,
                outputDatasetRef="work.joined",
            ),
            _v2_node(
                "vision",
                "transform",
                "transform.embedding.vision",
                embeddingModel="vision-v1",
            ),
            _v2_node("out", "output", "output.dataset", outputDatasetRef="analytics.out"),
        ],
        edges=[],
    )

    result = validate_pipeline_graph_v2(graph)
    errors = _rows(result["errors"])
    warnings = _rows(result["warnings"])

    assert {str(error["code"]) for error in errors} >= {
        "node_kind_mismatch",
        "node_config_type",
    }
    assert {str(warning["code"]) for warning in warnings} >= {"node_runtime_unavailable"}

    join = cast(dict[str, object], cast(list[dict[str, object]], graph["nodes"])[1]["config"])
    join["joinType"] = "sideways"
    errors = _rows(validate_pipeline_graph_v2(graph)["errors"])
    assert {str(error["code"]) for error in errors} >= {"node_config_value"}


def test_v2_join_and_union_validate_source_schemas_and_input_counts() -> None:
    graph = _v2_graph(
        nodes=[
            _v2_node(
                "left",
                "source",
                "source.dataset",
                datasetRef="raw.left",
                schema=[_column("left_id")],
            ),
            _v2_node(
                "right",
                "source",
                "source.dataset",
                datasetRef="raw.right",
                schema=[_column("right_id")],
            ),
            _v2_node(
                "join",
                "transform",
                "transform.join",
                leftKey="missing",
                rightKey="missing",
                joinType="inner",
                outputDatasetRef="work.joined",
            ),
            _v2_node("union", "transform", "transform.union"),
            _v2_node("out", "output", "output.dataset", outputDatasetRef="analytics.out"),
        ],
        edges=[
            _v2_edge("left-join", "left", "dataset", "join", "left"),
            _v2_edge("right-join", "right", "dataset", "join", "right"),
            _v2_edge("left-union", "left", "dataset", "union", "inputs"),
            _v2_edge("right-union", "right", "dataset", "union", "inputs"),
            _v2_edge("union-out", "union", "dataset", "out", "input"),
        ],
    )

    errors = _rows(validate_pipeline_graph_v2(graph)["errors"])
    assert {str(error["code"]) for error in errors} >= {
        "join_key_missing",
        "union_schema_mismatch",
    }

    cast(list[dict[str, object]], graph["edges"]).pop(3)
    errors = _rows(validate_pipeline_graph_v2(graph)["errors"])
    assert {str(error["code"]) for error in errors} >= {"input_count"}


def _legacy_join_graph(edge_sources: tuple[str, str]) -> dict[str, object]:
    nodes = [
        {
            "id": "z_orders",
            "type": "dataset",
            "config": {"datasetRef": "raw.orders", "schema": [_column("order_id")]},
        },
        {
            "id": "a_customers",
            "type": "dataset",
            "config": {"datasetRef": "raw.customers", "schema": [_column("customer_id")]},
        },
        {
            "id": "join",
            "type": "join",
            "config": {
                "leftKey": "customer_id",
                "rightKey": "order_id",
                "joinType": "full",
                "outputDatasetRef": "work.joined",
            },
        },
        _legacy_output(),
    ]
    edges = [{"source": source, "target": "join"} for source in edge_sources] + [{"source": "join", "target": "out"}]
    return _legacy_graph(nodes, edges=edges)


def _legacy_graph(
    nodes: list[dict[str, object]],
    *,
    edges: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "nodes": nodes,
        "edges": edges or [],
        "layout": {},
        "outputContract": {"columns": [_column("id")]},
        "tests": [],
        "schedule": None,
    }


def _legacy_output(node_id: str = "out") -> dict[str, object]:
    return {
        "id": node_id,
        "type": "output_dataset",
        "config": {"outputDatasetRef": f"analytics.{node_id}"},
    }


def _v2_join_graph(join_type: str) -> dict[str, object]:
    return _v2_graph(
        nodes=[
            _v2_node("orders", "source", "source.dataset", datasetRef="raw.orders", schema=[_column("id")]),
            _v2_node(
                "customers",
                "source",
                "source.dataset",
                datasetRef="raw.customers",
                schema=[_column("id")],
            ),
            _v2_node(
                "join",
                "transform",
                "transform.join",
                leftKey="id",
                rightKey="id",
                joinType=join_type,
                outputDatasetRef="work.joined",
            ),
            _v2_node("out", "output", "output.dataset", outputDatasetRef="analytics.joined"),
        ],
        edges=[
            _v2_edge("orders-left", "orders", "dataset", "join", "left"),
            _v2_edge("customers-right", "customers", "dataset", "join", "right"),
            _v2_edge("join-out", "join", "dataset", "out", "input"),
        ],
    )


def _v2_graph(
    *,
    nodes: list[dict[str, object]],
    edges: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "nodes": nodes,
        "edges": edges,
        "layout": {},
        "outputContract": {"columns": []},
        "tests": [],
        "schedule": None,
    }


def _v2_node(
    node_id: str,
    kind: str,
    descriptor_id: str,
    **config: object,
) -> dict[str, object]:
    return {
        "id": node_id,
        "kind": kind,
        "descriptorId": descriptor_id,
        "specVersion": 1,
        "config": dict(config),
    }


def _v2_edge(
    edge_id: str,
    source: str,
    source_port: str,
    target: str,
    target_port: str,
) -> dict[str, object]:
    return {
        "id": edge_id,
        "sourceNodeId": source,
        "sourcePortId": source_port,
        "targetNodeId": target,
        "targetPortId": target_port,
    }


def _target_ports(graph: dict[str, object], target: str) -> dict[str, str]:
    edges = cast(list[dict[str, object]], graph["edges"])
    return {str(edge["sourceNodeId"]): str(edge["targetPortId"]) for edge in edges if edge["targetNodeId"] == target}


def _column(name: str) -> dict[str, object]:
    return {"name": name, "type": "string", "nullable": False}


def _llm_config() -> dict[str, object]:
    return {
        "modelAlias": "default-completion",
        "promptVersionId": "vision@1",
        "promptTemplate": "Inspect the attached PDF.",
        "outputColumn": "interpretation",
        "inputFields": ["mediaReference"],
        "outputSchema": {"type": "object", "properties": {}},
        "dataClassification": "public",
        "mediaReferenceField": "mediaReference",
    }


def _rows(value: object) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], value)
