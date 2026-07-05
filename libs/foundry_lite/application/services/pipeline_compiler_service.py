"""Compile Pipeline Builder graphs into executable transform definitions."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from foundry_lite.application.ports import DatasetRow
from foundry_lite.application.primitives import SQL_IDENTIFIER_PATTERN, _sql_identifier, _sql_literal
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.pipeline_graph_model import (
    graph_edges,
    graph_nodes,
    node_data,
    topological_node_ids,
    validate_pipeline_graph,
)
from foundry_lite.application.services.transform_service import TransformService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

SAFE_CAST_TYPE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*(\([0-9]+(,[0-9]+)?\))?$")


class PipelineCompilerService(CoreService):
    """Turn a validated graph into concrete SQL/Python transform definitions."""

    required_dependencies = ()
    required_collaborators = ("dataset_registry_service", "transform_service")
    dataset_registry_service: PipelineDatasetRegistry
    transform_service: TransformService

    def compile_graph(
        self,
        *,
        pipeline_id: str,
        version_id: str,
        graph: Mapping[str, object],
        ctx: RequestContext,
    ) -> dict[str, object]:
        validation = validate_pipeline_graph(graph)
        if not validation["valid"]:
            raise ValidationFailed("pipeline graph is invalid", details={"validation": validation})
        compiled: list[dict[str, object]] = []
        refs_by_node = _initial_refs_by_node(graph)
        compile_ctx = _CompileContext(
            dataset_registry_service=self.dataset_registry_service,
            transform_service=self.transform_service,
            request_context=ctx,
            pipeline_id=pipeline_id,
            version_id=version_id,
            graph=graph,
            refs_by_node=refs_by_node,
        )
        for node_id in topological_node_ids(graph):
            node = _node_map(graph)[node_id]
            node_type = str(node["type"])
            ref = _compile_node(compile_ctx, node)
            if ref is not None:
                refs_by_node[node_id] = ref
                if node_type != "dataset":
                    compiled.append(
                        {
                            "nodeId": node_id,
                            "datasetRef": ref,
                            "apiName": _transform_api_name(pipeline_id, version_id, node_id),
                        }
                    )
        return {"pipelineId": pipeline_id, "versionId": version_id, "transforms": compiled}


class PipelineDatasetRegistry(Protocol):
    def ensure_dataset(self, dataset_ref: str, *, ctx: RequestContext | None = None) -> DatasetRow: ...


@dataclass(frozen=True)
class _CompileContext:
    dataset_registry_service: PipelineDatasetRegistry
    transform_service: TransformService
    request_context: RequestContext
    pipeline_id: str
    version_id: str
    graph: Mapping[str, object]
    refs_by_node: dict[str, str]


def _compile_node(compile_ctx: _CompileContext, node: Mapping[str, object]) -> str | None:
    node_type = str(node["type"])
    if node_type == "dataset":
        return _required_dataset_ref(node)
    if node_type == "sql":
        return _register_sql(compile_ctx, node)
    if node_type == "python":
        return _register_python(compile_ctx, node)
    if node_type in {"join", "union", "select_cast", "output_dataset"}:
        return _register_generated_sql(compile_ctx, node)
    raise ValidationFailed("unsupported pipeline node type", details={"node_type": node_type})


def _register_sql(compile_ctx: _CompileContext, node: Mapping[str, object]) -> str:
    data = node_data(node)
    sql = _required_text(data, "sql", node)
    output_ref = _required_output_ref(data, node)
    compile_ctx.dataset_registry_service.ensure_dataset(output_ref, ctx=compile_ctx.request_context)
    compile_ctx.transform_service.register_sql_transform(
        _transform_api_name(compile_ctx.pipeline_id, compile_ctx.version_id, str(node["id"])),
        sql=sql,
        inputs=_inputs_for_node(compile_ctx.graph, node, compile_ctx.refs_by_node),
        output_dataset_ref=output_ref,
        ctx=compile_ctx.request_context,
    )
    return output_ref


def _register_python(compile_ctx: _CompileContext, node: Mapping[str, object]) -> str:
    data = node_data(node)
    output_ref = _required_output_ref(data, node)
    compile_ctx.dataset_registry_service.ensure_dataset(output_ref, ctx=compile_ctx.request_context)
    compile_ctx.transform_service.register_python_transform(
        _transform_api_name(compile_ctx.pipeline_id, compile_ctx.version_id, str(node["id"])),
        source_code=_required_text(data, "sourceCode", node),
        function_name=str(data["functionName"]) if data.get("functionName") is not None else None,
        inputs=_inputs_for_node(compile_ctx.graph, node, compile_ctx.refs_by_node),
        output_dataset_ref=output_ref,
        ctx=compile_ctx.request_context,
    )
    return output_ref


def _register_generated_sql(compile_ctx: _CompileContext, node: Mapping[str, object]) -> str:
    data = node_data(node)
    inputs = _inputs_for_node(compile_ctx.graph, node, compile_ctx.refs_by_node)
    output_ref = _required_output_ref(data, node)
    compile_ctx.dataset_registry_service.ensure_dataset(output_ref, ctx=compile_ctx.request_context)
    compile_ctx.transform_service.register_sql_transform(
        _transform_api_name(compile_ctx.pipeline_id, compile_ctx.version_id, str(node["id"])),
        sql=_generated_sql(str(node["type"]), data, inputs),
        inputs=inputs,
        output_dataset_ref=output_ref,
        ctx=compile_ctx.request_context,
    )
    return output_ref


def _generated_sql(node_type: str, data: Mapping[str, object], inputs: Mapping[str, str]) -> str:
    refs = list(inputs.values())
    if node_type == "join":
        return _join_sql(data, refs)
    if node_type == "union":
        return _union_sql(refs)
    if node_type == "select_cast":
        return _select_cast_sql(data, refs)
    if node_type == "output_dataset":
        if not refs:
            raise ValidationFailed("output_dataset node requires one input")
        return f"SELECT * FROM {_input_macro(refs[0])}"  # nosec B608
    raise ValidationFailed("unsupported generated SQL node", details={"node_type": node_type})


def _join_sql(data: Mapping[str, object], refs: list[str]) -> str:
    if len(refs) != 2:
        raise ValidationFailed("join node requires exactly two inputs", details={"input_count": len(refs)})
    left_key = _safe_column(str(data.get("leftKey") or data.get("key") or ""))
    right_key = _safe_column(str(data.get("rightKey") or data.get("key") or ""))
    join_type = _join_type(str(data.get("joinType") or "inner"))
    left_input = _input_macro(refs[0])
    right_input = _input_macro(refs[1])
    return (
        f"SELECT * FROM {left_input} AS left_input "  # nosec B608
        f"{join_type} JOIN {right_input} AS right_input "
        f"ON left_input.{left_key} = right_input.{right_key}"
    )


def _union_sql(refs: list[str]) -> str:
    if len(refs) < 2:
        raise ValidationFailed("union node requires at least two inputs", details={"input_count": len(refs)})
    return " UNION ALL ".join(f"SELECT * FROM {_input_macro(ref)}" for ref in refs)  # nosec B608


def _select_cast_sql(data: Mapping[str, object], refs: list[str]) -> str:
    if len(refs) != 1:
        raise ValidationFailed("select_cast node requires exactly one input", details={"input_count": len(refs)})
    columns = data.get("columns")
    if not isinstance(columns, list) or not columns:
        raise ValidationFailed("select_cast node requires columns", details={"nodeType": "select_cast"})
    expressions = [_cast_expression(column) for column in columns if isinstance(column, dict)]
    return f"SELECT {', '.join(expressions)} FROM {_input_macro(refs[0])}"  # nosec B608


def _cast_expression(column: Mapping[str, object]) -> str:
    source = _safe_column(str(column.get("source") or column.get("name") or ""))
    name = _safe_column(str(column.get("name") or column.get("source") or ""))
    target_type = _safe_cast_type(str(column.get("type") or "VARCHAR"))
    return f"CAST({source} AS {target_type}) AS {name}"


def _inputs_for_node(
    graph: Mapping[str, object],
    node: Mapping[str, object],
    refs_by_node: Mapping[str, str],
) -> dict[str, str]:
    inputs: dict[str, str] = {}
    for edge in graph_edges(graph):
        if edge.get("target") != node["id"]:
            continue
        source = str(edge["source"])
        if source not in refs_by_node:
            raise ValidationFailed("pipeline node input is not compiled", details={"source": source})
        inputs[_alias_for_source(source, len(inputs))] = refs_by_node[source]
    return inputs


def _initial_refs_by_node(graph: Mapping[str, object]) -> dict[str, str]:
    refs: dict[str, str] = {}
    for node in graph_nodes(graph):
        if node.get("type") == "dataset":
            refs[str(node["id"])] = _required_dataset_ref(node)
    return refs


def _node_map(graph: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    return {str(node["id"]): node for node in graph_nodes(graph)}


def _required_dataset_ref(node: Mapping[str, object]) -> str:
    data = node_data(node)
    return _safe_dataset_ref(_required_text(data, "datasetRef", node))


def _required_output_ref(data: Mapping[str, object], node: Mapping[str, object]) -> str:
    return _safe_dataset_ref(_required_text(data, "outputDatasetRef", node))


def _required_text(data: Mapping[str, object], key: str, node: Mapping[str, object]) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationFailed(f"pipeline node {key} is required", details={"nodeId": node.get("id"), "field": key})
    return value.strip()


def _safe_column(value: str) -> str:
    if not SQL_IDENTIFIER_PATTERN.fullmatch(value):
        raise ValidationFailed("unsafe pipeline column identifier", details={"identifier": value})
    return _sql_identifier(value)


def _safe_dataset_ref(value: str) -> str:
    normalized = value.strip()
    parts = normalized.split(".")
    if len(parts) != 2 or not all(SQL_IDENTIFIER_PATTERN.fullmatch(part) for part in parts):
        raise ValidationFailed("unsafe pipeline dataset reference", details={"datasetRef": value})
    return normalized


def _input_macro(dataset_ref: str) -> str:
    return "{{ input(" + _sql_literal(_safe_dataset_ref(dataset_ref)) + ") }}"


def _safe_cast_type(value: str) -> str:
    normalized = value.strip().upper()
    if not SAFE_CAST_TYPE_PATTERN.fullmatch(normalized):
        raise ValidationFailed("unsafe pipeline cast type", details={"type": value})
    return normalized


def _join_type(value: str) -> str:
    normalized = value.strip().lower().replace("_", " ")
    choices = {"inner": "INNER", "left": "LEFT", "right": "RIGHT", "full outer": "FULL OUTER"}
    if normalized not in choices:
        raise ValidationFailed("unsupported join type", details={"joinType": value})
    return choices[normalized]


def _alias_for_source(source_node_id: str, index: int) -> str:
    slug = "".join(char if char.isalnum() else "_" for char in source_node_id).strip("_")
    return slug or f"input_{index}"


def _transform_api_name(pipeline_id: str, version_id: str, node_id: str) -> str:
    raw = f"pipeline_{pipeline_id}_{version_id}_{node_id}"
    name = "".join(char if char.isalnum() else "_" for char in raw)
    if not name or not name[0].isalpha():
        return f"pipeline_{name}"
    return name[:180]
