"""No-commit stream and geospatial Pipeline preview strategies."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.services.pipeline_geospatial_contracts import (
    geospatial_spec,
    validate_geospatial_rows,
)
from foundry_lite.application.services.pipeline_graph_contracts import PipelineV2Node
from foundry_lite.application.services.pipeline_preview_runtime import PipelinePreviewRuntime
from foundry_lite.domain.errors import ValidationFailed

JsonObject = dict[str, object]
PreviewInputs = Mapping[str, Sequence[JsonObject]]


def source_stream(
    node: PipelineV2Node,
    inputs: PreviewInputs,
    limits: Mapping[str, object],
    runtime: PipelinePreviewRuntime,
) -> list[JsonObject]:
    del inputs
    source_ref = _required_text(node, "sourceRef")
    return runtime.stream_rows(source_ref, limit=_table_limit(limits))


def source_geospatial(
    node: PipelineV2Node,
    inputs: PreviewInputs,
    limits: Mapping[str, object],
    runtime: PipelinePreviewRuntime,
) -> list[JsonObject]:
    del inputs
    resource_ref = _required_text(node, "resourceRef")
    rows = runtime.dataset_rows(resource_ref, limit=_table_limit(limits))
    _validate_rows(node, rows)
    return rows


def output_geospatial(
    node: PipelineV2Node,
    inputs: PreviewInputs,
    limits: Mapping[str, object],
    runtime: PipelinePreviewRuntime,
) -> list[JsonObject]:
    del runtime
    rows = _single_input(node, inputs)[: _table_limit(limits)]
    _validate_rows(node, rows)
    return rows


def _validate_rows(node: PipelineV2Node, rows: Sequence[Mapping[str, object]]) -> None:
    spec = geospatial_spec({"columns": _row_fields(rows)}, node["config"])
    validate_geospatial_rows(rows, spec)


def _single_input(node: PipelineV2Node, inputs: PreviewInputs) -> list[JsonObject]:
    groups = list(inputs.values())
    if len(groups) != 1:
        raise ValidationFailed(
            "pipeline preview node requires exactly one connected input",
            details={"nodeId": node["id"], "inputCount": len(groups)},
        )
    return [dict(item) for item in groups[0]]


def _required_text(node: PipelineV2Node, field: str) -> str:
    value = node["config"].get(field)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValidationFailed(
        "pipeline preview config is required",
        details={"nodeId": node["id"], "field": field},
    )


def _table_limit(limits: Mapping[str, object]) -> int:
    value = limits.get("tableRows")
    return value if isinstance(value, int) and not isinstance(value, bool) else 50


def _row_fields(rows: Sequence[Mapping[str, object]]) -> list[JsonObject]:
    return [{"name": name} for name in sorted({str(field) for row in rows for field in row})]
