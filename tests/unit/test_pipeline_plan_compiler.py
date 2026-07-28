from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import cast

import pytest
from foundry_lite.application.services.pipeline_execution_contracts import (
    ComputeProfile,
    ModelRef,
    PipelineArtifactManifest,
    PipelineArtifactRef,
    PipelineNodeAttempt,
    PipelineNodeRun,
    PipelinePreviewRun,
    PipelineScheduleSpec,
    pipeline_artifact_manifest_payload,
    pipeline_execution_plan_payload,
)
from foundry_lite.application.services.pipeline_graph_contracts import PipelineArtifactKind
from foundry_lite.application.services.pipeline_graph_normalizer import normalize_pipeline_graph
from foundry_lite.application.services.pipeline_graph_v2_runtime_plan import (
    pipeline_graph_v2_runtime_plan,
)
from foundry_lite.application.services.pipeline_plan_compiler import (
    PipelinePlanCompilationFailed,
    PipelinePlanCompiler,
    PipelineRuntimeUnavailable,
)
from foundry_lite.domain.errors import ValidationFailed


def test_compiler_normalizes_v1_and_freezes_a_descriptor_pinned_plan() -> None:
    graph = _legacy_tabular_graph()
    compiler = PipelinePlanCompiler()

    plan = compiler.compile(graph)
    canonical_plan = compiler.compile(normalize_pipeline_graph(graph))

    assert plan.graph_schema_version == 2
    assert plan.plan_fingerprint == canonical_plan.plan_fingerprint
    assert [node.node_id for node in plan.nodes] == ["orders", "select", "out"]
    assert {(node.descriptor_id, node.spec_version) for node in plan.nodes} == {
        ("source.dataset", 1),
        ("transform.select_cast", 1),
        ("output.dataset", 1),
    }
    assert plan.result_artifact_ids == ("artifact:out:dataset",)
    assert plan.artifacts[-1].resource_ref == "analytics.orders"
    payload = pipeline_execution_plan_payload(plan)
    assert payload["nodes"][-1]["config"]["outputContract"] == {
        "columns": [{"name": "id", "type": "string", "nullable": False}]
    }
    assert payload["planFingerprint"] == plan.plan_fingerprint
    assert payload["nodes"][1]["descriptorId"] == "transform.select_cast"

    graph_nodes = cast(list[dict[str, object]], graph["nodes"])
    cast(dict[str, object], graph_nodes[1]["config"])["outputDatasetRef"] = "work.changed"
    assert plan.nodes[1].config["outputDatasetRef"] == "work.selected_orders"
    with pytest.raises(TypeError):
        cast(dict[str, object], plan.nodes[1].config)["outputDatasetRef"] = "work.mutated"
    with pytest.raises(FrozenInstanceError):
        plan.graph_schema_version = 3  # type: ignore[misc]


def test_target_pruning_compiles_only_the_selected_executable_branch() -> None:
    graph = _tabular_and_media_graph()
    compiler = PipelinePlanCompiler()

    plan = compiler.compile(graph, target_node_ids=["dataset_out"])

    assert [node.node_id for node in plan.nodes] == ["dataset_source", "dataset_out"]
    assert [edge.edge_id for edge in plan.edges] == ["dataset-edge"]
    assert plan.result_artifact_ids == ("artifact:dataset_out:dataset",)
    assert plan.validation_warnings == ()

    media_plan = compiler.compile(graph, target_node_ids=["media_out"])

    assert [node.node_id for node in media_plan.nodes] == ["media_source", "media_out"]
    assert [edge.edge_id for edge in media_plan.edges] == ["media-edge"]
    assert media_plan.result_artifact_ids == ("artifact:media_out:media",)
    assert media_plan.artifacts[-1].artifact_kind == PipelineArtifactKind.MEDIA_SET_SELECTION
    assert media_plan.artifacts[-1].resource_ref == "media.processed"
    assert media_plan.nodes[-1].runtime_capability == "media_output_runtime"
    assert media_plan.validation_warnings == ()


def test_runtime_plan_selects_target_ancestors_in_deployed_order() -> None:
    payload = pipeline_execution_plan_payload(PipelinePlanCompiler().compile(_tabular_and_media_graph()))

    full = pipeline_graph_v2_runtime_plan(payload)
    selected = pipeline_graph_v2_runtime_plan(payload, target_node_ids=["dataset_out"])

    assert [node.node_id for node in full.nodes] == [
        "dataset_source",
        "dataset_out",
        "media_source",
        "media_out",
    ]
    assert [node.node_id for node in selected.nodes] == ["dataset_source", "dataset_out"]
    assert [edge.edge_id for edge in selected.edges] == ["dataset-edge"]
    assert selected.source_contracts == {}

    with pytest.raises(ValidationFailed, match="target node") as raised:
        pipeline_graph_v2_runtime_plan(payload, target_node_ids=["missing"])
    assert raised.value.details["targetNodeIds"] == ["missing"]


def test_compiler_builds_an_engine_neutral_plan_for_executable_multimodal_nodes() -> None:
    plan = PipelinePlanCompiler().compile(_executable_multimodal_graph())

    assert {node.descriptor_id for node in plan.nodes} >= {
        "source.media_set",
        "transform.media",
        "transform.document_extract",
        "transform.chunk",
        "transform.embedding.text",
        "transform.use_llm",
        "bridge.media_to_table_rows",
        "bridge.content_units_to_dataset",
    }
    assert {node.runtime_capability for node in plan.nodes} >= {
        "content_pipeline_runtime",
        "governed_model_gateway_runtime",
        "media_pipeline_runtime",
        "media_processor_registry",
        "model_pipeline_runtime",
        "multimodal_bridge_runtime",
    }
    assert set(plan.result_artifact_ids) == {
        "artifact:content_dataset_out:dataset",
        "artifact:media_dataset_out:dataset",
        "artifact:semantic_index_out:index",
    }
    assert not {
        warning["nodeId"] for warning in plan.validation_warnings if warning["code"] == "node_runtime_unavailable"
    }


def test_compiler_never_enables_graph_v2_runtime_implicitly() -> None:
    compiler = PipelinePlanCompiler(
        available_runtime_capabilities={
            "tabular_v1_compiler",
            "semantic_index_candidate_runtime",
        }
    )

    with pytest.raises(PipelineRuntimeUnavailable) as captured:
        compiler.compile(_executable_multimodal_graph())

    assert captured.value.details == {
        "nodeId": "media_source",
        "descriptorId": "source.media_set",
        "specVersion": 1,
        "availability": "graph_v2_executable",
        "runtimeCapability": "media_pipeline_runtime",
        "reason": "runtime_capability_not_enabled",
    }


def test_compiler_pins_governed_candidate_outputs_without_claiming_serving_runtime() -> None:
    graph = {
        "schemaVersion": 2,
        "nodes": [
            _v2_node("source", "source", "source.dataset", datasetRef="raw.documents"),
            _v2_node("index", "output", "output.semantic_index", indexRef="search.documents"),
            _v2_node("ontology", "output", "output.ontology", mappingRef="Document"),
        ],
        "edges": [
            _v2_edge("source-index", "source", "dataset", "index", "index"),
            _v2_edge("source-ontology", "source", "dataset", "ontology", "input"),
        ],
        "layout": {},
        "outputContract": {"columns": []},
        "tests": [],
        "schedule": None,
    }

    plan = PipelinePlanCompiler().compile(graph)

    assert [node.node_id for node in plan.nodes] == ["source", "index", "ontology"]
    assert {node.runtime_capability for node in plan.nodes[1:]} == {
        "semantic_index_candidate_runtime",
        "ontology_mapping_candidate_runtime",
    }
    assert {
        (artifact.producer_node_id, artifact.artifact_kind.value, artifact.resource_ref) for artifact in plan.artifacts
    } == {
        ("source", "dataset_version", "raw.documents"),
        ("index", "vector_index_generation", "search.documents"),
        ("ontology", "ontology_mapping", "Document"),
    }
    assert {warning["code"] for warning in plan.validation_warnings} == {"node_commits_governed_candidate_only"}


def test_compiler_never_falls_back_when_runtime_capability_is_disabled() -> None:
    compiler = PipelinePlanCompiler(available_runtime_capabilities=())

    with pytest.raises(PipelineRuntimeUnavailable) as captured:
        compiler.compile(_legacy_tabular_graph())

    assert captured.value.details["reason"] == "runtime_capability_not_enabled"
    assert captured.value.details["runtimeCapability"] == "tabular_v1_compiler"


def test_compiler_returns_typed_validation_and_missing_target_failures() -> None:
    invalid = {
        "schemaVersion": 2,
        "nodes": [_v2_node("orders", "source", "source.dataset", datasetRef="raw.orders")],
        "edges": [],
        "layout": {},
        "outputContract": {"columns": []},
        "tests": [],
        "schedule": None,
    }

    with pytest.raises(PipelinePlanCompilationFailed) as invalid_capture:
        PipelinePlanCompiler().compile(invalid)

    assert invalid_capture.value.code == "PIPELINE_PLAN_COMPILATION_FAILED"
    errors = cast(list[dict[str, object]], invalid_capture.value.details["validationErrors"])
    assert {error["code"] for error in errors} >= {"output_node_count"}

    with pytest.raises(PipelinePlanCompilationFailed) as target_capture:
        PipelinePlanCompiler().compile(_legacy_tabular_graph(), target_node_ids=["missing"])

    assert target_capture.value.details["reason"] == "target_node_not_found"


def test_execution_contracts_pin_compute_model_schedule_and_no_commit_preview() -> None:
    compute = ComputeProfile(
        profile_id="duckdb-small",
        engine="duckdb",
        engine_version="1.3.2",
        capabilities=("tabular_v1_compiler",),
        cpu_limit=2.0,
        memory_mib=512,
        timeout_seconds=30,
    )
    model = ModelRef(
        model_id="bge-small",
        model_version="1",
        provider="local",
        revision="sha256:model",
        parameters_fingerprint="sha256:params",
    )
    schedule = PipelineScheduleSpec(
        trigger_kind="cron",
        timezone="Asia/Seoul",
        cron_expression="0 * * * *",
        trigger_config={"catchup": False, "labels": ["hourly"]},
    )
    artifact = _manifest_artifact()
    manifest = PipelineArtifactManifest(
        artifact=artifact,
        manifest_version=1,
        content_fingerprint="sha256:content",
        metadata={"lineage": ["source", "output"]},
        row_count=50,
    )
    node_run = PipelineNodeRun(
        node_run_id="node-run-1",
        pipeline_run_id="run-1",
        node_id="out",
        descriptor_id="output.dataset",
        spec_version=1,
        status="succeeded",
        output_artifact_ids=(artifact.artifact_id,),
        attempt_count=1,
    )
    attempt = PipelineNodeAttempt(
        attempt_id="attempt-1",
        node_run_id=node_run.node_run_id,
        attempt_number=1,
        status="succeeded",
        fencing_token=7,
    )
    preview = PipelinePreviewRun(
        preview_run_id="preview-1",
        pipeline_id="pipeline-1",
        branch_id="branch-1",
        status="succeeded",
        graph_fingerprint="sha256:graph",
        target_node_ids=("out",),
        limits={"tableRows": 50},
        artifacts=(manifest,),
    )
    plan = PipelinePlanCompiler().compile(
        _legacy_tabular_graph(),
        compute_profile=compute,
        model_refs=[model],
    )
    runtime_plan = pipeline_graph_v2_runtime_plan(pipeline_execution_plan_payload(plan))

    assert plan.compute_profile == compute
    assert plan.model_refs == (model,)
    assert runtime_plan.model_refs == (model,)
    assert schedule.trigger_config["labels"] == ("hourly",)
    assert manifest.metadata["lineage"] == ("source", "output")
    assert pipeline_artifact_manifest_payload(manifest) == {
        "manifestVersion": 1,
        "artifact": {
            "artifactId": "artifact:out:dataset",
            "artifactKind": "dataset_version",
            "producerNodeId": "out",
            "producerPortId": "dataset",
            "descriptorId": "output.dataset",
            "specVersion": 1,
            "resourceRef": "analytics.orders",
        },
        "contentFingerprint": "sha256:content",
        "metadata": {"lineage": ["source", "output"]},
        "securityMarkings": [],
        "rowCount": 50,
        "itemCount": None,
        "byteCount": None,
    }
    assert attempt.fencing_token == 7
    assert preview.is_commit_forbidden is True
    with pytest.raises(TypeError):
        cast(dict[str, object], preview.limits)["tableRows"] = 200
    with pytest.raises(ValidationFailed):
        PipelinePreviewRun(
            preview_run_id="preview-unsafe",
            pipeline_id="pipeline-1",
            branch_id="branch-1",
            status="queued",
            graph_fingerprint="sha256:graph",
            target_node_ids=("out",),
            limits={},
            is_commit_forbidden=False,
        )


def test_compute_and_model_pins_change_the_plan_fingerprint() -> None:
    compiler = PipelinePlanCompiler()
    base = compiler.compile(_legacy_tabular_graph())
    pinned = compiler.compile(
        _legacy_tabular_graph(),
        compute_profile=ComputeProfile("duckdb", "duckdb", "1.3.2"),
        model_refs=[ModelRef("model", "1", "local", "rev-1", "sha256:params")],
    )

    assert base.plan_fingerprint != pinned.plan_fingerprint


def _legacy_tabular_graph() -> dict[str, object]:
    return {
        "nodes": [
            {
                "id": "orders",
                "type": "dataset",
                "config": {"datasetRef": "raw.orders", "schema": [_column("id")]},
            },
            {
                "id": "select",
                "type": "select_cast",
                "config": {
                    "columns": [{"source": "id", "name": "id", "type": "BIGINT"}],
                    "outputDatasetRef": "work.selected_orders",
                },
            },
            {
                "id": "out",
                "type": "output_dataset",
                "config": {"outputDatasetRef": "analytics.orders"},
            },
        ],
        "edges": [
            {"id": "source-select", "source": "orders", "target": "select"},
            {"id": "select-out", "source": "select", "target": "out"},
        ],
        "layout": {},
        "outputContract": {"columns": [_column("id")]},
        "tests": [],
        "schedule": None,
    }


def _tabular_and_media_graph() -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "nodes": [
            _v2_node("dataset_source", "source", "source.dataset", datasetRef="raw.orders"),
            _v2_node("dataset_out", "output", "output.dataset", outputDatasetRef="analytics.orders"),
            _v2_node("media_source", "source", "source.media_set", mediaSetRef="media.documents"),
            _v2_node("media_out", "output", "output.media_set", mediaSetRef="media.processed"),
        ],
        "edges": [
            _v2_edge("dataset-edge", "dataset_source", "dataset", "dataset_out", "input"),
            _v2_edge("media-edge", "media_source", "media", "media_out", "media"),
        ],
        "layout": {},
        "outputContract": {"columns": []},
        "tests": [],
        "schedule": None,
    }


def _executable_multimodal_graph() -> dict[str, object]:
    semantic_config = {
        "modelAlias": "claude-structured",
        "promptVersionId": "document-v1",
        "outputColumn": "interpretation",
        "inputFields": ["text"],
        "outputSchema": {"type": "object", "properties": {"summary": {"type": "string"}}},
        "dataClassification": "CONFIDENTIAL",
    }
    return {
        "schemaVersion": 2,
        "nodes": [
            _v2_node("media_source", "source", "source.media_set", mediaSetRef="media.documents"),
            _v2_node("media_transform", "transform", "transform.media", processorId="pdf_normalize_v1@1"),
            _v2_node("extract", "transform", "transform.document_extract", processorId="pdf_layout_v1@1"),
            _v2_node("chunk", "transform", "transform.chunk", chunkSize=500),
            _v2_node("embed", "transform", "transform.embedding.text", modelRef="bge-small@1"),
            _v2_node("content_rows", "transform", "bridge.content_units_to_dataset"),
            _v2_node("content_llm", "transform", "transform.use_llm", **semantic_config),
            _v2_node(
                "content_dataset_out",
                "output",
                "output.dataset",
                outputDatasetRef="analytics.document_content",
            ),
            _v2_node("media_rows", "transform", "bridge.media_to_table_rows"),
            _v2_node("media_llm", "transform", "transform.use_llm", **semantic_config),
            _v2_node(
                "media_dataset_out",
                "output",
                "output.dataset",
                outputDatasetRef="analytics.document_media",
            ),
            _v2_node(
                "semantic_index_out",
                "output",
                "output.semantic_index",
                indexRef="search.document_content",
            ),
        ],
        "edges": [
            _v2_edge("media-transform", "media_source", "media", "media_transform", "media"),
            _v2_edge("transform-extract", "media_transform", "derivatives", "extract", "media"),
            _v2_edge("extract-chunk", "extract", "content", "chunk", "content"),
            _v2_edge("chunk-embed", "chunk", "content", "embed", "content"),
            _v2_edge("embed-index", "embed", "index", "semantic_index_out", "index"),
            _v2_edge("chunk-rows", "chunk", "content", "content_rows", "content"),
            _v2_edge("content-llm", "content_rows", "dataset", "content_llm", "input"),
            _v2_edge("content-out", "content_llm", "dataset", "content_dataset_out", "input"),
            _v2_edge("media-rows", "media_source", "media", "media_rows", "media"),
            _v2_edge("media-llm", "media_rows", "dataset", "media_llm", "input"),
            _v2_edge("media-out", "media_llm", "dataset", "media_dataset_out", "input"),
        ],
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


def _manifest_artifact() -> PipelineArtifactRef:
    return PipelineArtifactRef(
        artifact_id="artifact:out:dataset",
        artifact_kind=PipelineArtifactKind.DATASET_VERSION,
        producer_node_id="out",
        producer_port_id="dataset",
        descriptor_id="output.dataset",
        spec_version=1,
        resource_ref="analytics.orders",
    )


def _column(name: str) -> dict[str, object]:
    return {"name": name, "type": "string", "nullable": False}
