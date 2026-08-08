"""Bounded JSON-schema validation for advertised consumer Ontology MCP tools."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence

from foundry_lite.domain.errors import ValidationFailed

JsonSchema = Mapping[str, object]


def validate_tool_arguments(arguments: Mapping[str, object], schema: JsonSchema) -> None:
    """Validate a decoded tools/call argument object against the exact published schema."""

    _validate_value(arguments, schema, path="$")


def _validate_value(value: object, schema: JsonSchema, *, path: str) -> None:
    _validate_one_of(value, schema, path)
    _validate_const(value, schema, path)
    _validate_enum(value, schema, path)
    expected = schema.get("type")
    if expected is not None:
        _validate_typed_value(value, schema, path, expected)


def _validate_const(value: object, schema: JsonSchema, path: str) -> None:
    if "const" in schema and value != schema["const"]:
        _invalid(path, "const")


def _validate_enum(value: object, schema: JsonSchema, path: str) -> None:
    enum = schema.get("enum")
    if isinstance(enum, Sequence) and not isinstance(enum, str | bytes) and value not in enum:
        _invalid(path, "enum")


def _validate_typed_value(value: object, schema: JsonSchema, path: str, expected: object) -> None:
    if not _matches_type(value, expected):
        _invalid(path, "type")
    if expected == "object":
        _validate_object(value, schema, path)
    elif expected == "array":
        _validate_array(value, schema, path)
    elif expected == "string":
        _validate_string(value, schema, path)
    elif expected in {"integer", "number"}:
        _validate_number(value, schema, path)


def _validate_one_of(value: object, schema: JsonSchema, path: str) -> None:
    branches = schema.get("oneOf")
    if not isinstance(branches, Sequence) or isinstance(branches, str | bytes):
        return
    matches = sum(1 for branch in branches if isinstance(branch, Mapping) and _schema_matches(value, branch, path))
    if matches != 1:
        _invalid(path, "oneOf")


def _schema_matches(value: object, schema: JsonSchema, path: str) -> bool:
    try:
        _validate_value(value, schema, path=path)
    except ValidationFailed:
        return False
    return True


def _validate_object(value: object, schema: JsonSchema, path: str) -> None:
    assert isinstance(value, Mapping)
    properties = schema.get("properties")
    property_map = properties if isinstance(properties, Mapping) else {}
    _validate_required_properties(value, schema, path)
    _validate_additional_properties(value, schema, path, property_map)
    _validate_property_values(value, property_map, path)


def _validate_required_properties(value: Mapping[object, object], schema: JsonSchema, path: str) -> None:
    required = schema.get("required")
    required_names = required if isinstance(required, Sequence) and not isinstance(required, str | bytes) else ()
    missing = sorted(str(name) for name in required_names if isinstance(name, str) and name not in value)
    if missing:
        _invalid(path, "required", fields=missing)


def _validate_additional_properties(
    value: Mapping[object, object], schema: JsonSchema, path: str, property_map: Mapping[object, object]
) -> None:
    if schema.get("additionalProperties") is False:
        unexpected = sorted(str(name) for name in value if name not in property_map)
        if unexpected:
            _invalid(path, "additionalProperties", fields=unexpected)


def _validate_property_values(value: Mapping[object, object], property_map: Mapping[object, object], path: str) -> None:
    for name, child_schema in property_map.items():
        if name in value and isinstance(name, str) and isinstance(child_schema, Mapping):
            _validate_value(value[name], child_schema, path=f"{path}.{name}")


def _validate_array(value: object, schema: JsonSchema, path: str) -> None:
    assert isinstance(value, list)
    _validate_size(len(value), schema, path, "Items")
    item_schema = schema.get("items")
    if isinstance(item_schema, Mapping):
        for index, item in enumerate(value):
            _validate_value(item, item_schema, path=f"{path}[{index}]")
    if schema.get("uniqueItems") is True:
        canonical = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
        if len(canonical) != len(set(canonical)):
            _invalid(path, "uniqueItems")


def _validate_string(value: object, schema: JsonSchema, path: str) -> None:
    assert isinstance(value, str)
    _validate_size(len(value), schema, path, "Length")
    pattern = schema.get("pattern")
    if isinstance(pattern, str) and re.search(pattern, value) is None:
        _invalid(path, "pattern")


def _validate_size(size: int, schema: JsonSchema, path: str, suffix: str) -> None:
    minimum = schema.get(f"min{suffix}")
    maximum = schema.get(f"max{suffix}")
    if isinstance(minimum, int) and size < minimum:
        _invalid(path, f"min{suffix}")
    if isinstance(maximum, int) and size > maximum:
        _invalid(path, f"max{suffix}")


def _validate_number(value: object, schema: JsonSchema, path: str) -> None:
    assert isinstance(value, int | float) and not isinstance(value, bool)
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if isinstance(minimum, int | float) and value < minimum:
        _invalid(path, "minimum")
    if isinstance(maximum, int | float) and value > maximum:
        _invalid(path, "maximum")


def _matches_type(value: object, expected: object) -> bool:
    if isinstance(expected, Sequence) and not isinstance(expected, str | bytes):
        return any(_matches_type(value, item) for item in expected)
    matcher = _TYPE_MATCHERS.get(expected) if isinstance(expected, str) else None
    return matcher(value) if matcher is not None else False


_TYPE_MATCHERS = {
    "object": lambda value: isinstance(value, Mapping),
    "array": lambda value: isinstance(value, list),
    "string": lambda value: isinstance(value, str),
    "boolean": lambda value: isinstance(value, bool),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, int | float) and not isinstance(value, bool),
    "null": lambda value: value is None,
}


def _invalid(path: str, reason: str, *, fields: Sequence[str] = ()) -> None:
    details: dict[str, object] = {"path": path, "schemaRule": reason}
    if fields:
        details["fields"] = list(fields)
    raise ValidationFailed("Ontology MCP tool arguments do not match the advertised schema", details=details)
