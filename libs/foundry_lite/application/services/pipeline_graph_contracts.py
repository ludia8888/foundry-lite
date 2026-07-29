"""Typed public contracts for the Pipeline Builder v2 graph IR."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from foundry_lite.application.services.pipeline_graph_descriptor_payloads import (
    pipeline_node_descriptor_payload,
)
from foundry_lite.application.services.pipeline_graph_types import (  # noqa: F401
    PipelineGraphV2,
    PipelineV2Edge,
    PipelineV2Node,
)
from foundry_lite.application.services.pipeline_node_execution_policy import (
    PipelineNodeExecutionPolicy,
    pipeline_node_execution_policy,
)

JsonObject = dict[str, object]

MAX_PIPELINE_NODES = 200
MAX_PIPELINE_EDGES = 600
MAX_PIPELINE_TESTS = 50
DEFAULT_PIPELINE_PREVIEW_ROWS = MAX_PIPELINE_PREVIEW_ROWS = 500
MAX_PIPELINE_LLM_PREVIEW_ROWS = 50


class PipelineArtifactKind(StrEnum):
    """Artifact identities that may cross a Pipeline Builder v2 port."""

    DATASET_VERSION = "dataset_version"
    VIRTUAL_TABLE = "virtual_table"
    MEDIA_SET_SELECTION = "media_set_selection"
    MEDIA_DERIVATIVE_SET = "media_derivative_set"
    CONTENT_UNIT_SET = "content_unit_set"
    VECTOR_INDEX_GENERATION = "vector_index_generation"
    STREAM_CHECKPOINT = "stream_checkpoint"
    GEOSPATIAL_SERIES = "geospatial_series"
    ONTOLOGY_MAPPING = "ontology_mapping"


class PipelineNodeKind(StrEnum):
    SOURCE = "source"
    TRANSFORM = "transform"
    OUTPUT = "output"


class PipelineNodeAvailability(StrEnum):
    """Honest execution boundary exposed by the descriptor catalog."""

    LEGACY_EXECUTABLE = "legacy_executable"
    GRAPH_V2_EXECUTABLE = "graph_v2_executable"
    GOVERNED_CANDIDATE = "governed_candidate"
    VALIDATION_ONLY = "validation_only"


class PipelinePortCardinality(StrEnum):
    ONE = "one"
    MANY = "many"


class PipelineConfigValueKind(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    ARRAY = "array"
    OBJECT = "object"


@dataclass(frozen=True)
class PipelineInputPortDescriptor:
    port_id: str
    accepted_artifact_kinds: tuple[PipelineArtifactKind, ...]
    cardinality: PipelinePortCardinality = PipelinePortCardinality.ONE
    is_required: bool = True


@dataclass(frozen=True)
class PipelineOutputPortDescriptor:
    port_id: str
    artifact_kind: PipelineArtifactKind


@dataclass(frozen=True)
class PipelineConfigFieldDescriptor:
    field_name: str
    value_kind: PipelineConfigValueKind
    is_required: bool = False
    allowed_values: tuple[str, ...] = ()


@dataclass(frozen=True)
class PipelineNodeDescriptor:
    descriptor_id: str
    spec_version: int
    node_kind: PipelineNodeKind
    input_ports: tuple[PipelineInputPortDescriptor, ...]
    output_ports: tuple[PipelineOutputPortDescriptor, ...]
    config_fields: tuple[PipelineConfigFieldDescriptor, ...]
    availability: PipelineNodeAvailability
    runtime_capability: str
    execution_policy: PipelineNodeExecutionPolicy = PipelineNodeExecutionPolicy()


def pipeline_node_descriptors() -> tuple[PipelineNodeDescriptor, ...]:
    """Return the immutable server-owned node catalog."""

    return _PIPELINE_NODE_DESCRIPTORS


def pipeline_node_descriptor(descriptor_id: str, spec_version: int) -> PipelineNodeDescriptor | None:
    return _DESCRIPTOR_BY_KEY.get((descriptor_id, spec_version))


def pipeline_node_descriptor_payloads() -> list[JsonObject]:
    return [pipeline_node_descriptor_payload(descriptor) for descriptor in pipeline_node_descriptors()]


def _input(
    port_id: str,
    *artifact_kinds: PipelineArtifactKind,
    cardinality: PipelinePortCardinality = PipelinePortCardinality.ONE,
    is_required: bool = True,
) -> PipelineInputPortDescriptor:
    return PipelineInputPortDescriptor(port_id, artifact_kinds, cardinality, is_required)


def _output(port_id: str, artifact_kind: PipelineArtifactKind) -> PipelineOutputPortDescriptor:
    return PipelineOutputPortDescriptor(port_id, artifact_kind)


def _required_string(field_name: str) -> PipelineConfigFieldDescriptor:
    return PipelineConfigFieldDescriptor(field_name, PipelineConfigValueKind.STRING, is_required=True)


def _optional_string(
    field_name: str,
    *allowed_values: str,
) -> PipelineConfigFieldDescriptor:
    return PipelineConfigFieldDescriptor(
        field_name,
        PipelineConfigValueKind.STRING,
        allowed_values=allowed_values,
    )


def _required_integer(field_name: str) -> PipelineConfigFieldDescriptor:
    return PipelineConfigFieldDescriptor(field_name, PipelineConfigValueKind.INTEGER, is_required=True)


def _legacy_descriptor(
    descriptor_id: str,
    node_kind: PipelineNodeKind,
    input_ports: tuple[PipelineInputPortDescriptor, ...],
    output_ports: tuple[PipelineOutputPortDescriptor, ...],
    config_fields: tuple[PipelineConfigFieldDescriptor, ...],
) -> PipelineNodeDescriptor:
    return PipelineNodeDescriptor(
        descriptor_id,
        1,
        node_kind,
        input_ports,
        output_ports,
        config_fields,
        PipelineNodeAvailability.LEGACY_EXECUTABLE,
        "tabular_v1_compiler",
        pipeline_node_execution_policy(descriptor_id),
    )


def _planned_descriptor(
    descriptor_id: str,
    node_kind: PipelineNodeKind,
    input_ports: tuple[PipelineInputPortDescriptor, ...],
    output_ports: tuple[PipelineOutputPortDescriptor, ...],
    config_fields: tuple[PipelineConfigFieldDescriptor, ...],
    runtime_capability: str,
) -> PipelineNodeDescriptor:
    return PipelineNodeDescriptor(
        descriptor_id,
        1,
        node_kind,
        input_ports,
        output_ports,
        config_fields,
        PipelineNodeAvailability.VALIDATION_ONLY,
        runtime_capability,
        pipeline_node_execution_policy(descriptor_id),
    )


def _graph_v2_descriptor(
    descriptor_id: str,
    node_kind: PipelineNodeKind,
    input_ports: tuple[PipelineInputPortDescriptor, ...],
    output_ports: tuple[PipelineOutputPortDescriptor, ...],
    config_fields: tuple[PipelineConfigFieldDescriptor, ...],
    runtime_capability: str,
) -> PipelineNodeDescriptor:
    return PipelineNodeDescriptor(
        descriptor_id,
        1,
        node_kind,
        input_ports,
        output_ports,
        config_fields,
        PipelineNodeAvailability.GRAPH_V2_EXECUTABLE,
        runtime_capability,
        pipeline_node_execution_policy(descriptor_id),
    )


def _candidate_descriptor(
    descriptor_id: str,
    input_ports: tuple[PipelineInputPortDescriptor, ...],
    output_ports: tuple[PipelineOutputPortDescriptor, ...],
    config_fields: tuple[PipelineConfigFieldDescriptor, ...],
    runtime_capability: str,
) -> PipelineNodeDescriptor:
    return PipelineNodeDescriptor(
        descriptor_id,
        1,
        PipelineNodeKind.OUTPUT,
        input_ports,
        output_ports,
        config_fields,
        PipelineNodeAvailability.GOVERNED_CANDIDATE,
        runtime_capability,
        pipeline_node_execution_policy(descriptor_id),
    )


DATASET = PipelineArtifactKind.DATASET_VERSION
VIRTUAL = PipelineArtifactKind.VIRTUAL_TABLE
MEDIA = PipelineArtifactKind.MEDIA_SET_SELECTION
DERIVATIVE = PipelineArtifactKind.MEDIA_DERIVATIVE_SET
CONTENT = PipelineArtifactKind.CONTENT_UNIT_SET
VECTOR_INDEX = PipelineArtifactKind.VECTOR_INDEX_GENERATION
STREAM = PipelineArtifactKind.STREAM_CHECKPOINT
GEO = PipelineArtifactKind.GEOSPATIAL_SERIES
ONTOLOGY = PipelineArtifactKind.ONTOLOGY_MAPPING
MANY = PipelinePortCardinality.MANY
GEOSPATIAL_CONFIG_FIELDS = (
    _required_string("resourceRef"),
    _optional_string("geometryField"),
    _optional_string("longitudeField"),
    _optional_string("latitudeField"),
    _optional_string("timeField"),
)
_PIPELINE_NODE_DESCRIPTORS = (
    _legacy_descriptor(
        "source.dataset", PipelineNodeKind.SOURCE, (), (_output("dataset", DATASET),), (_required_string("datasetRef"),)
    ),
    _legacy_descriptor(
        "transform.sql",
        PipelineNodeKind.TRANSFORM,
        (_input("inputs", DATASET, cardinality=MANY, is_required=False),),
        (_output("dataset", DATASET),),
        (_required_string("sql"), _required_string("outputDatasetRef")),
    ),
    _legacy_descriptor(
        "transform.python",
        PipelineNodeKind.TRANSFORM,
        (_input("inputs", DATASET, cardinality=MANY, is_required=False),),
        (_output("dataset", DATASET),),
        (_required_string("sourceCode"), _required_string("outputDatasetRef")),
    ),
    _legacy_descriptor(
        "transform.join",
        PipelineNodeKind.TRANSFORM,
        (_input("left", DATASET), _input("right", DATASET)),
        (_output("dataset", DATASET),),
        (
            _required_string("leftKey"),
            _required_string("rightKey"),
            _optional_string("joinType", "inner", "left", "right", "full outer"),
            _required_string("outputDatasetRef"),
        ),
    ),
    _legacy_descriptor(
        "transform.union",
        PipelineNodeKind.TRANSFORM,
        (_input("inputs", DATASET, cardinality=MANY),),
        (_output("dataset", DATASET),),
        (_required_string("outputDatasetRef"),),
    ),
    _legacy_descriptor(
        "transform.select_cast",
        PipelineNodeKind.TRANSFORM,
        (_input("input", DATASET),),
        (_output("dataset", DATASET),),
        (
            PipelineConfigFieldDescriptor("columns", PipelineConfigValueKind.ARRAY, is_required=True),
            _required_string("outputDatasetRef"),
        ),
    ),
    _legacy_descriptor(
        "output.dataset",
        PipelineNodeKind.OUTPUT,
        (_input("input", DATASET),),
        (_output("dataset", DATASET),),
        (_required_string("outputDatasetRef"),),
    ),
    _planned_descriptor(
        "source.virtual_table",
        PipelineNodeKind.SOURCE,
        (),
        (_output("table", VIRTUAL),),
        (_required_string("virtualTableRef"),),
        "virtual_table_runtime",
    ),
    _graph_v2_descriptor(
        "source.media_set",
        PipelineNodeKind.SOURCE,
        (),
        (_output("media", MEDIA),),
        (
            _required_string("mediaSetRef"),
            PipelineConfigFieldDescriptor("selection", PipelineConfigValueKind.OBJECT),
        ),
        "media_pipeline_runtime",
    ),
    _graph_v2_descriptor(
        "source.stream",
        PipelineNodeKind.SOURCE,
        (),
        (_output("stream", STREAM),),
        (_required_string("sourceRef"),),
        "streaming_pipeline_runtime",
    ),
    _graph_v2_descriptor(
        "source.geospatial",
        PipelineNodeKind.SOURCE,
        (),
        (_output("series", GEO),),
        GEOSPATIAL_CONFIG_FIELDS,
        "geospatial_pipeline_runtime",
    ),
    _graph_v2_descriptor(
        "transform.media",
        PipelineNodeKind.TRANSFORM,
        (_input("media", MEDIA, DERIVATIVE),),
        (_output("derivatives", DERIVATIVE),),
        (_required_string("processorId"),),
        "media_processor_registry",
    ),
    _graph_v2_descriptor(
        "transform.document_extract",
        PipelineNodeKind.TRANSFORM,
        (_input("media", MEDIA, DERIVATIVE),),
        (_output("content", CONTENT),),
        (_required_string("processorId"),),
        "media_processor_registry",
    ),
    _graph_v2_descriptor(
        "transform.chunk",
        PipelineNodeKind.TRANSFORM,
        (_input("content", CONTENT),),
        (_output("content", CONTENT),),
        (_required_integer("chunkSize"),),
        "content_pipeline_runtime",
    ),
    _graph_v2_descriptor(
        "transform.embedding.text",
        PipelineNodeKind.TRANSFORM,
        (_input("content", CONTENT),),
        (_output("index", VECTOR_INDEX),),
        (_required_string("modelRef"),),
        "model_pipeline_runtime",
    ),
    _planned_descriptor(
        "transform.embedding.vision",
        PipelineNodeKind.TRANSFORM,
        (_input("media", MEDIA, DERIVATIVE),),
        (_output("index", VECTOR_INDEX),),
        (_required_string("modelRef"),),
        "model_pipeline_runtime",
    ),
    _graph_v2_descriptor(
        "transform.use_llm",
        PipelineNodeKind.TRANSFORM,
        (_input("input", DATASET),),
        (_output("dataset", DATASET),),
        (
            _required_string("modelAlias"),
            _optional_string("expectedModelId"),
            _optional_string("expectedModelRevision"),
            _required_string("promptVersionId"),
            _optional_string("promptMode", "text", "basic_vision", "layout_aware_vision"),
            _optional_string("promptTemplate"),
            _required_string("outputColumn"),
            PipelineConfigFieldDescriptor("inputFields", PipelineConfigValueKind.ARRAY, is_required=True),
            PipelineConfigFieldDescriptor("outputSchema", PipelineConfigValueKind.OBJECT, is_required=True),
            _required_string("dataClassification"),
            _optional_string("systemPrompt"),
            _optional_string("outputMode", "simple", "with_errors"),
            PipelineConfigFieldDescriptor("skipRecomputingRows", PipelineConfigValueKind.BOOLEAN),
            PipelineConfigFieldDescriptor("cacheGeneration", PipelineConfigValueKind.INTEGER),
            _optional_string("cachePolicy", "referenced_fields"),
            PipelineConfigFieldDescriptor("modelParameters", PipelineConfigValueKind.OBJECT),
            _optional_string("mediaReferenceField"),
            _optional_string("environment"),
            _optional_string("regionRequirement"),
        ),
        "governed_model_gateway_runtime",
    ),
    _graph_v2_descriptor(
        "transform.trained_model",
        PipelineNodeKind.TRANSFORM,
        (_input("input", DATASET),),
        (_output("dataset", DATASET),),
        (
            _required_string("modelRef"),
            _optional_string("modelBranch"),
            PipelineConfigFieldDescriptor("fallbackBranches", PipelineConfigValueKind.ARRAY),
            PipelineConfigFieldDescriptor("inputMappings", PipelineConfigValueKind.OBJECT, is_required=True),
            PipelineConfigFieldDescriptor("outputMappings", PipelineConfigValueKind.OBJECT, is_required=True),
        ),
        "trained_model_batch_runtime",
    ),
    _graph_v2_descriptor(
        "bridge.media_to_table_rows",
        PipelineNodeKind.TRANSFORM,
        (_input("media", MEDIA, DERIVATIVE),),
        (_output("dataset", DATASET),),
        (),
        "multimodal_bridge_runtime",
    ),
    _graph_v2_descriptor(
        "bridge.content_units_to_dataset",
        PipelineNodeKind.TRANSFORM,
        (_input("content", CONTENT),),
        (_output("dataset", DATASET),),
        (),
        "multimodal_bridge_runtime",
    ),
    _graph_v2_descriptor(
        "bridge.stream_to_dataset",
        PipelineNodeKind.TRANSFORM,
        (_input("stream", STREAM),),
        (_output("dataset", DATASET),),
        (),
        "streaming_pipeline_runtime",
    ),
    _graph_v2_descriptor(
        "output.media_set",
        PipelineNodeKind.OUTPUT,
        (_input("media", MEDIA, DERIVATIVE),),
        (_output("media", MEDIA),),
        (_required_string("mediaSetRef"),),
        "media_output_runtime",
    ),
    _candidate_descriptor(
        "output.semantic_index",
        (_input("index", VECTOR_INDEX, DATASET, CONTENT),),
        (_output("index", VECTOR_INDEX),),
        (_required_string("indexRef"),),
        "semantic_index_candidate_runtime",
    ),
    _planned_descriptor(
        "output.virtual_table",
        PipelineNodeKind.OUTPUT,
        (_input("input", DATASET, VIRTUAL),),
        (_output("table", VIRTUAL),),
        (_required_string("virtualTableRef"),),
        "virtual_table_runtime",
    ),
    _candidate_descriptor(
        "output.ontology",
        (_input("input", DATASET, CONTENT, MEDIA),),
        (_output("mapping", ONTOLOGY),),
        (_required_string("mappingRef"),),
        "ontology_mapping_candidate_runtime",
    ),
    _graph_v2_descriptor(
        "output.geospatial",
        PipelineNodeKind.OUTPUT,
        (_input("input", DATASET, GEO),),
        (_output("series", GEO),),
        GEOSPATIAL_CONFIG_FIELDS,
        "geospatial_pipeline_runtime",
    ),
)
_DESCRIPTOR_BY_KEY = {(item.descriptor_id, item.spec_version): item for item in _PIPELINE_NODE_DESCRIPTORS}
