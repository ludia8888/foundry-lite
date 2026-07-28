"""Validation and payload helpers for Pipeline Builder trained models."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports.trained_model_inference import (
    TrainedModelDefinition,
    TrainedModelField,
    TrainedModelInvocation,
)
from foundry_lite.domain.errors import ValidationFailed

JsonObject = dict[str, object]

SUPPORTED_TRAINED_MODEL_TYPES = frozenset(
    {
        "boolean",
        "byte",
        "short",
        "integer",
        "long",
        "float",
        "double",
        "decimal",
        "string",
        "binary",
        "date",
        "timestamp",
        "array",
        "map",
        "struct",
        "mediaReference",
    }
)


def imported_trained_model_refs(graph: Mapping[str, object]) -> frozenset[str]:
    metadata = graph.get("metadata")
    if not isinstance(metadata, Mapping):
        return frozenset()
    reusables = metadata.get("reusables")
    if not isinstance(reusables, Mapping):
        return frozenset()
    raw_refs = reusables.get("trainedModels")
    if not isinstance(raw_refs, list):
        return frozenset()
    return frozenset(ref.strip() for ref in raw_refs if isinstance(ref, str) and ref.strip())


def require_trained_model_import(graph: Mapping[str, object], model_ref: str) -> None:
    if model_ref not in imported_trained_model_refs(graph):
        raise ValidationFailed(
            "trained model must be imported into pipeline Reusables before use",
            details={"modelRef": model_ref},
        )


def trained_model_definition_payload(definition: TrainedModelDefinition) -> JsonObject:
    return {
        "modelRef": definition.model_ref,
        "displayName": definition.display_name,
        "branch": definition.branch,
        "latestVersion": definition.version,
        "revision": definition.revision,
        "inputSchema": [_field_payload(field) for field in definition.input_fields],
        "outputSchema": [_field_payload(field) for field in definition.output_fields],
        "executionModes": list(definition.execution_modes),
        "previewSupported": definition.is_preview_supported,
        "resourceProfile": {
            "cpuCores": definition.cpu_cores,
            "memoryMiB": definition.memory_mib,
            "gpuType": definition.gpu_type,
            "startupTimeoutSeconds": definition.startup_timeout_seconds,
        },
        "versionResolution": "latest_on_branch_with_fallbacks",
        "inputCardinality": "single_table",
        "outputCardinality": "single_table",
    }


def validate_trained_model_config(
    config: Mapping[str, object],
    definition: TrainedModelDefinition,
) -> None:
    input_mappings = _mapping(config, "inputMappings")
    output_mappings = _mapping(config, "outputMappings")
    _require_input_mappings(definition, input_mappings)
    _require_output_mappings(definition, output_mappings)
    _require_supported_types(definition)


def require_trained_model_invocation_pin(
    invocation: TrainedModelInvocation,
    definition: TrainedModelDefinition,
) -> None:
    require_trained_model_definition_pin(
        model_ref=invocation.model_ref,
        expected_model_version=invocation.expected_model_version,
        expected_revision=invocation.expected_revision,
        expected_executable_reference=invocation.expected_executable_reference,
        definition=definition,
    )


def require_trained_model_definition_pin(
    *,
    model_ref: str,
    expected_model_version: str | None,
    expected_revision: str | None,
    expected_executable_reference: str | None,
    definition: TrainedModelDefinition,
) -> None:
    expected = {
        "modelVersion": expected_model_version,
        "revision": expected_revision,
        "executableReference": expected_executable_reference,
    }
    actual = {
        "modelVersion": definition.version,
        "revision": definition.revision,
        "executableReference": definition.executable_reference,
    }
    mismatches = {key: value for key, value in expected.items() if value is not None and actual[key] != value}
    if mismatches:
        raise ValidationFailed(
            "resolved trained model does not match the deployed execution-plan pin",
            details={"modelRef": model_ref, "expected": expected, "actual": actual},
        )


def map_trained_model_inputs(
    rows: Sequence[Mapping[str, object]],
    config: Mapping[str, object],
    definition: TrainedModelDefinition,
) -> tuple[Mapping[str, object], ...]:
    mappings = _mapping(config, "inputMappings")
    return tuple(_mapped_input(row, mappings, definition) for row in rows)


def merge_trained_model_outputs(
    source_rows: Sequence[Mapping[str, object]],
    model_rows: Sequence[Mapping[str, object]],
    config: Mapping[str, object],
    definition: TrainedModelDefinition,
) -> tuple[JsonObject, ...]:
    if len(source_rows) != len(model_rows):
        raise ValidationFailed("trained model output row count does not match its input")
    aliases = _mapping(config, "outputMappings")
    return tuple(
        _merged_output(source, output, aliases, definition)
        for source, output in zip(source_rows, model_rows, strict=True)
    )


def trained_model_branch_config(config: Mapping[str, object]) -> tuple[str, tuple[str, ...]]:
    branch = str(config.get("modelBranch") or "master").strip()
    raw_fallbacks = config.get("fallbackBranches")
    fallbacks = (
        tuple(str(value).strip() for value in raw_fallbacks if str(value).strip())
        if isinstance(raw_fallbacks, list)
        else ()
    )
    return branch, fallbacks


def _field_payload(field: TrainedModelField) -> JsonObject:
    return {
        "name": field.name,
        "type": field.data_type,
        "required": field.is_required,
    }


def _mapping(config: Mapping[str, object], field: str) -> Mapping[str, object]:
    value = config.get(field)
    if not isinstance(value, Mapping):
        raise ValidationFailed("trained model mapping must be an object", details={"field": field})
    return value


def _require_input_mappings(
    definition: TrainedModelDefinition,
    mappings: Mapping[str, object],
) -> None:
    missing = [field.name for field in definition.input_fields if field.is_required and not mappings.get(field.name)]
    if missing:
        raise ValidationFailed(
            "trained model required inputs are not mapped",
            details={"missingInputFields": missing},
        )


def _require_output_mappings(
    definition: TrainedModelDefinition,
    mappings: Mapping[str, object],
) -> None:
    expected = {field.name for field in definition.output_fields}
    missing = sorted(expected - set(mappings))
    aliases = [str(mappings[name]).strip() for name in expected if name in mappings]
    if missing or any(not alias for alias in aliases) or len(set(aliases)) != len(aliases):
        raise ValidationFailed(
            "trained model outputs require unique aliases",
            details={"missingOutputFields": missing, "aliases": aliases},
        )


def _require_supported_types(definition: TrainedModelDefinition) -> None:
    fields = (*definition.input_fields, *definition.output_fields)
    unsupported = sorted({field.data_type for field in fields if field.data_type not in SUPPORTED_TRAINED_MODEL_TYPES})
    if unsupported:
        raise ValidationFailed(
            "trained model API includes unsupported types",
            details={"unsupportedTypes": unsupported},
        )


def _mapped_input(
    row: Mapping[str, object],
    mappings: Mapping[str, object],
    definition: TrainedModelDefinition,
) -> Mapping[str, object]:
    result: JsonObject = {}
    for field in definition.input_fields:
        expression = mappings.get(field.name)
        if expression in {None, ""} and not field.is_required:
            continue
        result[field.name] = _evaluate_mapping_expression(row, str(expression))
    return result


def _evaluate_mapping_expression(row: Mapping[str, object], expression: str) -> object:
    source = expression.strip()
    if source.startswith("$"):
        return _path_value(row, source[1:])
    if source.startswith("cast(") and source.endswith(")"):
        return _cast_expression(row, source[5:-1])
    raise ValidationFailed(
        "trained model input expression is unsupported",
        details={"expression": expression, "supported": ["$column", "cast($column as type)"]},
    )


def _path_value(row: Mapping[str, object], path: str) -> object:
    value: object = row
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            raise ValidationFailed("trained model input expression references a missing field", details={"path": path})
        value = value[part]
    return value


def _cast_expression(row: Mapping[str, object], body: str) -> object:
    expression, separator, target = body.rpartition(" as ")
    if not separator:
        raise ValidationFailed("trained model cast expression is invalid", details={"expression": body})
    value = _evaluate_mapping_expression(row, expression)
    if target == "string":
        return str(value)
    if target in {"double", "float"}:
        return float(_numeric_cast_value(value, target))
    if target in {"integer", "long"}:
        return int(_numeric_cast_value(value, target))
    raise ValidationFailed("trained model cast target is unsupported", details={"targetType": target})


def _numeric_cast_value(value: object, target: str) -> str | int | float:
    if isinstance(value, str | int | float) and not isinstance(value, bool):
        return value
    raise ValidationFailed(
        "trained model numeric cast source is unsupported",
        details={"targetType": target, "actualType": type(value).__name__},
    )


def _merged_output(
    source: Mapping[str, object],
    output: Mapping[str, object],
    aliases: Mapping[str, object],
    definition: TrainedModelDefinition,
) -> JsonObject:
    expected = {field.name for field in definition.output_fields}
    actual = set(output)
    if actual != expected:
        raise ValidationFailed(
            "trained model returned columns that do not match its API",
            details={"missing": sorted(expected - actual), "extra": sorted(actual - expected)},
        )
    result = dict(source)
    for field in definition.output_fields:
        result[str(aliases[field.name])] = output[field.name]
    return result
