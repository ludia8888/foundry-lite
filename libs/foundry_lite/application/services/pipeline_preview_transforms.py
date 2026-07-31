"""Node strategies for bounded, non-committing Pipeline Builder previews."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from foundry_lite.application.primitives import _json_hash
from foundry_lite.application.services.pipeline_graph_contracts import (
    MAX_PIPELINE_LLM_PREVIEW_ROWS,
    PipelineV2Node,
)
from foundry_lite.application.services.pipeline_media_reference import (
    required_source_media_reference,
)
from foundry_lite.application.services.pipeline_preview_errors import PipelineCodeIsolationRequired
from foundry_lite.application.services.pipeline_preview_joins import join_rows as _join_rows
from foundry_lite.application.services.pipeline_preview_runtime import PipelinePreviewRuntime
from foundry_lite.application.services.pipeline_preview_structured import (
    output_geospatial,
    source_geospatial,
    source_stream,
)
from foundry_lite.domain.errors import ValidationFailed

JsonObject = dict[str, object]
PreviewInputs = Mapping[str, Sequence[JsonObject]]
NodeHandler = Callable[[PipelineV2Node, PreviewInputs, Mapping[str, object], PipelinePreviewRuntime], list[JsonObject]]
_DURATION_BOUNDED_PROCESSORS = frozenset({"asr_v1", "video_frames_v1", "video_vision_v1"})
_SCENE_BOUNDED_PROCESSORS = frozenset({"video_frames_v1", "video_vision_v1"})
_MAX_SEMANTIC_PREVIEW_CONCURRENCY = 4


def source_dataset(
    node: PipelineV2Node,
    inputs: PreviewInputs,
    limits: Mapping[str, object],
    runtime: PipelinePreviewRuntime,
) -> list[JsonObject]:
    del inputs
    dataset_ref = required_text(node["config"], "datasetRef", node)
    return runtime.dataset_rows(dataset_ref, limit=limit_value(limits, "tableRows"))


def source_media(
    node: PipelineV2Node,
    inputs: PreviewInputs,
    limits: Mapping[str, object],
    runtime: PipelinePreviewRuntime,
) -> list[JsonObject]:
    del inputs
    media_set_ref = required_text(node["config"], "mediaSetRef", node)
    return runtime.media_selection(
        media_set_ref,
        selection=_media_selection_config(node),
        item_limit=limit_value(limits, "mediaItems"),
        total_byte_limit=limit_value(limits, "totalBytes"),
    )


def select_cast(
    node: PipelineV2Node,
    inputs: PreviewInputs,
    limits: Mapping[str, object],
    runtime: PipelinePreviewRuntime,
) -> list[JsonObject]:
    del limits, runtime
    rows = single_input(inputs, node)
    columns = node["config"].get("columns")
    if not isinstance(columns, list) or not columns:
        raise ValidationFailed("select/cast preview requires columns", details={"nodeId": node["id"]})
    return [_select_row(row, columns, node) for row in rows]


def union(
    node: PipelineV2Node,
    inputs: PreviewInputs,
    limits: Mapping[str, object],
    runtime: PipelinePreviewRuntime,
) -> list[JsonObject]:
    del node, runtime
    rows = [dict(row) for group in inputs.values() for row in group]
    return rows[: limit_value(limits, "tableRows")]


def join(
    node: PipelineV2Node,
    inputs: PreviewInputs,
    limits: Mapping[str, object],
    runtime: PipelinePreviewRuntime,
) -> list[JsonObject]:
    del runtime
    left = list(inputs.get("left", ()))
    right = list(inputs.get("right", ()))
    left_key = required_text(node["config"], "leftKey", node)
    right_key = required_text(node["config"], "rightKey", node)
    join_type = str(node["config"].get("joinType") or "inner")
    rows = _join_rows(left, right, left_key, right_key, join_type)
    return rows[: limit_value(limits, "tableRows")]


def process_media(
    node: PipelineV2Node,
    inputs: PreviewInputs,
    limits: Mapping[str, object],
    runtime: PipelinePreviewRuntime,
) -> list[JsonObject]:
    items = single_input(inputs, node)
    processor_id = required_text(node["config"], "processorId", node)
    parameters = _processor_parameters(node["config"], limits, processor_id=processor_id)
    return runtime.process_media(items, processor_id=processor_id, parameters=parameters)


def document_extract(
    node: PipelineV2Node,
    inputs: PreviewInputs,
    limits: Mapping[str, object],
    runtime: PipelinePreviewRuntime,
) -> list[JsonObject]:
    derivatives = process_media(node, inputs, limits, runtime)
    units = [dict(unit) for derivative in derivatives for unit in object_items(derivative.get("units"))]
    return units[: limit_value(limits, "tableRows")]


def chunk(
    node: PipelineV2Node,
    inputs: PreviewInputs,
    limits: Mapping[str, object],
    runtime: PipelinePreviewRuntime,
) -> list[JsonObject]:
    del runtime
    units = single_input(inputs, node)
    size = required_positive_int(node["config"].get("chunkSize"), "chunkSize", node)
    overlap = bounded_int(node["config"].get("overlap"), 0, 0, max(size - 1, 0))
    chunks = [item for unit in units for item in _chunk_unit(unit, size=size, overlap=overlap)]
    return chunks[: limit_value(limits, "tableRows")]


def embed_text(
    node: PipelineV2Node,
    inputs: PreviewInputs,
    limits: Mapping[str, object],
    runtime: PipelinePreviewRuntime,
) -> list[JsonObject]:
    units = single_input(inputs, node)[: limit_value(limits, "tableRows")]
    model_ref = required_text(node["config"], "modelRef", node)
    return runtime.embed_content(units, model_ref=model_ref)


def media_to_table_rows(
    node: PipelineV2Node,
    inputs: PreviewInputs,
    limits: Mapping[str, object],
    runtime: PipelinePreviewRuntime,
) -> list[JsonObject]:
    del runtime
    items = single_input(inputs, node)[: limit_value(limits, "tableRows")]
    return [_media_reference_row(item) for item in items]


def use_llm(
    node: PipelineV2Node,
    inputs: PreviewInputs,
    limits: Mapping[str, object],
    runtime: PipelinePreviewRuntime,
) -> list[JsonObject]:
    table_limit = limit_value(limits, "tableRows")
    trial_limit = bounded_int(
        node["config"].get("trialCount"),
        3,
        1,
        MAX_PIPELINE_LLM_PREVIEW_ROWS,
    )
    items = single_input(inputs, node)[: min(table_limit, trial_limit, MAX_PIPELINE_LLM_PREVIEW_ROWS)]
    return runtime.interpret_semantics(
        items,
        config=node["config"],
        node_id=node["id"],
        descriptor_id=node["descriptorId"],
        spec_version=str(node["specVersion"]),
        max_concurrency=min(len(items), _MAX_SEMANTIC_PREVIEW_CONCURRENCY),
        request_timeout_seconds=bounded_int(limits.get("timeoutSeconds"), 30, 1, 30),
    )


def bridge_or_output(
    node: PipelineV2Node,
    inputs: PreviewInputs,
    limits: Mapping[str, object],
    runtime: PipelinePreviewRuntime,
) -> list[JsonObject]:
    del runtime
    return single_input(inputs, node)[: limit_value(limits, "tableRows")]


def unsupported_code_preview(
    node: PipelineV2Node,
    inputs: PreviewInputs,
    limits: Mapping[str, object],
    runtime: PipelinePreviewRuntime,
) -> list[JsonObject]:
    del inputs, limits, runtime
    raise PipelineCodeIsolationRequired(
        "unsaved SQL/Python preview requires the isolated code execution worker",
        details={
            "nodeId": node["id"],
            "descriptorId": node["descriptorId"],
            "requiredCapability": "isolated_code_execution_worker",
            "executionAttempted": False,
        },
    )


NODE_HANDLERS: dict[str, NodeHandler] = {
    "source.dataset": source_dataset,
    "source.media_set": source_media,
    "source.stream": source_stream,
    "source.geospatial": source_geospatial,
    "transform.select_cast": select_cast,
    "transform.union": union,
    "transform.join": join,
    "transform.media": process_media,
    "transform.document_extract": document_extract,
    "transform.chunk": chunk,
    "transform.embedding.text": embed_text,
    "transform.use_llm": use_llm,
    "bridge.media_to_table_rows": media_to_table_rows,
    "bridge.content_units_to_dataset": bridge_or_output,
    "bridge.stream_to_dataset": bridge_or_output,
    "output.dataset": bridge_or_output,
    "output.media_set": bridge_or_output,
    "output.semantic_index": bridge_or_output,
    "output.virtual_table": bridge_or_output,
    "output.ontology": bridge_or_output,
    "output.geospatial": output_geospatial,
    "transform.sql": unsupported_code_preview,
    "transform.python": unsupported_code_preview,
}


def single_input(inputs: PreviewInputs, node: PipelineV2Node) -> list[JsonObject]:
    groups = list(inputs.values())
    if len(groups) != 1:
        raise ValidationFailed(
            "pipeline preview node requires exactly one connected input",
            details={"nodeId": node["id"], "inputCount": len(groups)},
        )
    return [dict(item) for item in groups[0]]


def object_items(value: object) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def string_list(value: object, field: str, node: PipelineV2Node) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise ValidationFailed(
            f"pipeline preview {field} must be a non-empty string list",
            details={"nodeId": node["id"], "field": field},
        )
    return [str(item) for item in value]


def required_text(config: Mapping[str, object], field: str, node: PipelineV2Node) -> str:
    value = config.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValidationFailed("pipeline preview config is required", details={"nodeId": node["id"], "field": field})
    return value.strip()


def required_positive_int(value: object, field: str, node: PipelineV2Node) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValidationFailed(
            "pipeline preview positive integer is required",
            details={"nodeId": node["id"], "field": field},
        )
    return value


def bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationFailed("pipeline preview limit must be an integer", details={"value": value})
    return max(minimum, min(value, maximum))


def limit_value(limits: Mapping[str, object], key: str) -> int:
    value = limits.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationFailed("normalized pipeline preview limit is missing", details={"limit": key})
    return value


def _processor_parameters(
    config: Mapping[str, object],
    limits: Mapping[str, object],
    *,
    processor_id: str,
) -> JsonObject:
    nested = config.get("parameters")
    parameters = dict(nested) if isinstance(nested, Mapping) else {}
    parameters.pop("processingBounds", None)
    processor_name = processor_id.partition("@")[0]
    if processor_id.startswith(("pdf_text_v1@", "pdf_layout_v1@", "pdf_ocr_v1@")):
        parameters["pageSelection"] = _preview_page_selection(parameters, limits)
    if processor_name in _DURATION_BOUNDED_PROCESSORS:
        requested = _requested_processing_bounds(config, limits, processor_name)
        parameters["requestedProcessingBounds"] = requested
        parameters["processingBounds"] = _applied_processing_bounds(requested, limits, processor_name)
    return parameters


def _requested_processing_bounds(
    config: Mapping[str, object],
    limits: Mapping[str, object],
    processor_name: str,
) -> JsonObject:
    requested = _processing_bounds_config(config)
    duration_cap_ms = limit_value(limits, "audioVideoSeconds") * 1000
    bounds: JsonObject = {
        "maxDurationMs": _positive_bound(requested.get("maxDurationMs"), duration_cap_ms, "maxDurationMs")
    }
    if processor_name in _SCENE_BOUNDED_PROCESSORS:
        scene_cap = limit_value(limits, "sceneCount")
        bounds["maxSceneCount"] = _positive_bound(requested.get("maxSceneCount"), scene_cap, "maxSceneCount")
    return bounds


def _applied_processing_bounds(
    requested: Mapping[str, object],
    limits: Mapping[str, object],
    processor_name: str,
) -> JsonObject:
    duration_cap_ms = limit_value(limits, "audioVideoSeconds") * 1000
    bounds: JsonObject = {"maxDurationMs": min(_requested_bound(requested, "maxDurationMs"), duration_cap_ms)}
    if processor_name in _SCENE_BOUNDED_PROCESSORS:
        bounds["maxSceneCount"] = min(
            _requested_bound(requested, "maxSceneCount"),
            limit_value(limits, "sceneCount"),
        )
    return bounds


def _requested_bound(requested: Mapping[str, object], field: str) -> int:
    value = requested.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValidationFailed("normalized pipeline preview processing bound is missing", details={"field": field})
    return value


def _processing_bounds_config(config: Mapping[str, object]) -> Mapping[str, object]:
    value = config.get("processingBounds")
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValidationFailed("pipeline preview processingBounds must be an object")
    return value


def _positive_bound(value: object, default: int, field: str) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValidationFailed(
            "pipeline preview processing bound must be a positive integer",
            details={"field": field, "value": value},
        )
    return value


def _preview_page_selection(
    parameters: Mapping[str, object],
    limits: Mapping[str, object],
) -> JsonObject:
    raw = parameters.get("pageSelection")
    selection = dict(raw) if isinstance(raw, Mapping) else {}
    start = bounded_int(selection.get("start"), 1, 1, 10000)
    preview_limit = limit_value(limits, "pdfPages")
    requested = bounded_int(selection.get("limit"), preview_limit, 1, 10000)
    return {"start": start, "limit": min(requested, preview_limit)}


def _media_selection_config(node: PipelineV2Node) -> JsonObject:
    selection = node["config"].get("selection")
    if isinstance(selection, Mapping):
        return dict(selection)
    legacy_ids = node["config"].get("mediaItemVersionIds")
    if legacy_ids is not None:
        return {"mode": "version_ids", "versionIds": string_list(legacy_ids, "mediaItemVersionIds", node)}
    return {"mode": "current_heads"}


def _media_reference_row(item: Mapping[str, object]) -> JsonObject:
    reference = required_source_media_reference(item)
    security_envelope = item.get("securityEnvelope")
    return {
        "mediaReference": reference,
        "mediaItemVersionId": reference["mediaItemVersionId"],
        "securityEnvelope": dict(security_envelope) if isinstance(security_envelope, Mapping) else {},
    }


def _select_row(row: Mapping[str, object], columns: Sequence[object], node: PipelineV2Node) -> JsonObject:
    selected: JsonObject = {}
    for item in columns:
        if not isinstance(item, Mapping):
            raise ValidationFailed("select/cast column must be an object", details={"nodeId": node["id"]})
        source = str(item.get("source") or item.get("name") or "")
        name = str(item.get("name") or source)
        selected[name] = _cast_value(row.get(source), str(item.get("type") or "string"))
    envelope = row.get("securityEnvelope")
    if isinstance(envelope, Mapping):
        selected["securityEnvelope"] = dict(envelope)
    return selected


def _cast_value(value: object, type_name: str) -> object:
    if value is None:
        return None
    normalized = type_name.strip().lower()
    if normalized in {"string", "varchar", "text"}:
        return str(value)
    if normalized in {"integer", "int", "bigint", "long"}:
        return int(str(value))
    if normalized in {"float", "double", "decimal", "number"}:
        return float(str(value))
    if normalized in {"boolean", "bool"}:
        return _bool_value(value)
    return value


def _bool_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "y"}:
        return True
    if normalized in {"false", "0", "no", "n"}:
        return False
    raise ValidationFailed("cannot cast preview value to boolean", details={"value": str(value)})


def _chunk_unit(unit: Mapping[str, object], *, size: int, overlap: int) -> list[JsonObject]:
    words = str(unit.get("text") or "").split()
    if not words:
        return []
    step = max(size - overlap, 1)
    chunks: list[JsonObject] = []
    for ordinal, start in enumerate(range(0, len(words), step)):
        text = " ".join(words[start : start + size])
        chunks.append(_chunk_payload(unit, ordinal, text))
        if start + size >= len(words):
            break
    return chunks


def _chunk_payload(unit: Mapping[str, object], ordinal: int, text: str) -> JsonObject:
    return {
        **dict(unit),
        "unitKind": "chunk",
        "ordinal": ordinal,
        "text": text,
        "textHash": _json_hash({"text": text}),
    }
