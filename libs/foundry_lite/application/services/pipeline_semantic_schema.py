"""Bounded JSON-schema validation for semantic Pipeline Builder outputs."""

from __future__ import annotations

import json
from collections.abc import Mapping

from foundry_lite.domain.errors import ValidationFailed

_SUPPORTED_OUTPUT_TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean"})
_MAX_SCHEMA_DEPTH = 8
_MAX_SCHEMA_PROPERTIES = 128


class SemanticOutputError(ValidationFailed):
    """Typed per-row failure for malformed model output."""

    code = "PIPELINE_SEMANTIC_OUTPUT_INVALID"


def parse_semantic_model_output(content: str, schema: Mapping[str, object]) -> object:
    """Parse one model response and enforce its configured output schema."""

    expected_type = str(schema.get("type") or "")
    value: object = content if expected_type == "string" else _parse_json_output(content)
    _validate_schema_value(value, schema, path="$")
    return value


def validate_semantic_output_schema(schema: Mapping[str, object]) -> None:
    """Validate the bounded JSON-schema subset accepted by semantic previews."""

    _validate_output_schema(schema)


def _parse_json_output(content: str) -> object:
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise SemanticOutputError("model response is not valid JSON") from exc


def _validate_schema_value(value: object, schema: Mapping[str, object], *, path: str) -> None:
    expected_type = str(schema.get("type") or "")
    if not _matches_output_type(value, expected_type):
        raise SemanticOutputError(
            "model response does not match the configured output type",
            details={"path": path, "expected": expected_type},
        )
    _validate_enum_value(value, schema, path)
    if expected_type == "object":
        _validate_object_value(value, schema, path)
    if expected_type == "array":
        _validate_array_value(value, schema, path)


def _validate_enum_value(value: object, schema: Mapping[str, object], path: str) -> None:
    enum_values = schema.get("enum")
    if not isinstance(enum_values, list) or value in enum_values:
        return
    raise SemanticOutputError(
        "model response is outside the configured output enum",
        details={"path": path, "allowed": enum_values},
    )


def _validate_object_value(value: object, schema: Mapping[str, object], path: str) -> None:
    assert isinstance(value, Mapping)
    property_map = _property_map(schema)
    _require_output_fields(value, _required_field_names(schema))
    _require_no_unexpected_fields(value, property_map, schema, path)
    _validate_object_children(value, property_map, path)


def _property_map(schema: Mapping[str, object]) -> Mapping[object, object]:
    properties = schema.get("properties")
    return properties if isinstance(properties, Mapping) else {}


def _required_field_names(schema: Mapping[str, object]) -> list[object]:
    required = schema.get("required")
    return required if isinstance(required, list) else []


def _require_output_fields(value: Mapping[object, object], required_fields: list[object]) -> None:
    missing = [field for field in required_fields if isinstance(field, str) and field not in value]
    if missing:
        raise SemanticOutputError("model response is missing required output fields", details={"missing": missing})


def _require_no_unexpected_fields(
    value: Mapping[object, object],
    property_map: Mapping[object, object],
    schema: Mapping[str, object],
    path: str,
) -> None:
    if schema.get("additionalProperties") is not False:
        return
    unexpected = sorted(str(key) for key in value if key not in property_map)
    if unexpected:
        raise SemanticOutputError(
            "model response contains unexpected output fields",
            details={"path": path, "unexpected": unexpected},
        )


def _validate_object_children(
    value: Mapping[object, object],
    property_map: Mapping[object, object],
    path: str,
) -> None:
    for key, child_schema in property_map.items():
        if key in value and isinstance(child_schema, Mapping):
            _validate_schema_value(value[key], child_schema, path=f"{path}.{key}")


def _validate_array_value(value: object, schema: Mapping[str, object], path: str) -> None:
    assert isinstance(value, list)
    item_schema = schema.get("items")
    if not isinstance(item_schema, Mapping):
        return
    for index, item in enumerate(value):
        _validate_schema_value(item, item_schema, path=f"{path}[{index}]")


def _validate_output_schema(schema: Mapping[str, object], *, depth: int = 0) -> None:
    if depth > _MAX_SCHEMA_DEPTH:
        raise ValidationFailed("pipeline model output schema exceeds maximum depth")
    schema_type = schema.get("type")
    if schema_type not in _SUPPORTED_OUTPUT_TYPES:
        raise ValidationFailed("pipeline model output schema type is unsupported", details={"type": schema_type})
    _validate_enum_schema(schema, str(schema_type))
    if schema_type == "object":
        _validate_object_schema(schema, depth)
    if schema_type == "array":
        _validate_array_schema(schema, depth)


def _validate_object_schema(schema: Mapping[str, object], depth: int) -> None:
    properties = _validated_properties(schema)
    _require_property_limit(properties)
    required_fields = _validated_required_fields(schema)
    _require_defined_fields(required_fields, properties)
    _validate_additional_properties_flag(schema)
    _validate_property_schemas(properties, depth)


def _validated_properties(schema: Mapping[str, object]) -> Mapping[object, object]:
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise ValidationFailed("pipeline object output schema properties must be an object")
    return properties


def _require_property_limit(properties: Mapping[object, object]) -> None:
    if len(properties) > _MAX_SCHEMA_PROPERTIES:
        raise ValidationFailed("pipeline model output schema has too many properties")


def _validated_required_fields(schema: Mapping[str, object]) -> list[str]:
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(isinstance(field, str) for field in required):
        raise ValidationFailed("pipeline object output schema required must be a string list")
    return required


def _require_defined_fields(required_fields: list[str], properties: Mapping[object, object]) -> None:
    missing = sorted(field for field in required_fields if field not in properties)
    if missing:
        raise ValidationFailed("pipeline output schema required fields are undefined", details={"fields": missing})


def _validate_additional_properties_flag(schema: Mapping[str, object]) -> None:
    additional = schema.get("additionalProperties")
    if additional is not None and not isinstance(additional, bool):
        raise ValidationFailed("pipeline output schema additionalProperties must be boolean")


def _validate_property_schemas(properties: Mapping[object, object], depth: int) -> None:
    for child in properties.values():
        if not isinstance(child, Mapping):
            raise ValidationFailed("pipeline output schema property definitions must be objects")
        _validate_output_schema(child, depth=depth + 1)


def _validate_array_schema(schema: Mapping[str, object], depth: int) -> None:
    items = schema.get("items")
    if not isinstance(items, Mapping):
        raise ValidationFailed("pipeline array output schema requires an item schema")
    _validate_output_schema(items, depth=depth + 1)


def _validate_enum_schema(schema: Mapping[str, object], schema_type: str) -> None:
    enum_values = schema.get("enum")
    if enum_values is None:
        return
    if not isinstance(enum_values, list) or not enum_values:
        raise ValidationFailed("pipeline output schema enum must be a non-empty list")
    if not all(_is_json_scalar(value) for value in enum_values):
        raise ValidationFailed("pipeline output schema enum supports scalar values only")
    invalid = [value for value in enum_values if not _matches_output_type(value, schema_type)]
    if invalid:
        raise ValidationFailed(
            "pipeline output schema enum values must match the declared type",
            details={"type": schema_type, "invalid": invalid},
        )


def _is_json_scalar(value: object) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def _matches_output_type(value: object, expected_type: str) -> bool:
    if expected_type == "object":
        return isinstance(value, Mapping)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    return False
