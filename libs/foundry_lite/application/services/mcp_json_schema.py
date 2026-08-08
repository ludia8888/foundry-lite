"""Small fail-closed JSON Schema subset shared by MCP application boundaries."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import TypeGuard


@dataclass(frozen=True)
class McpJsonSchemaError(ValueError):
    """Describe the first advertised MCP input-schema violation."""

    path: str
    reason: str

    def __str__(self) -> str:
        return f"{self.path}: {self.reason}"


def validate_mcp_json_schema(value: object, schema: Mapping[str, object], *, path: str = "$") -> None:
    """Validate the JSON Schema keywords emitted by Foundry-lite MCP tools."""
    _validate_one_of(value, schema, path)
    expected = _validate_type(value, schema, path)
    _validate_const(value, schema, path)
    _validate_enum(value, schema, path)
    _validate_string(value, schema, path)
    _validate_numeric_bounds(value, schema, path)
    _validate_container(value, schema, path, expected)


def _validate_type(value: object, schema: Mapping[str, object], path: str) -> str | None:
    expected = schema.get("type")
    if isinstance(expected, str) and not _matches_type(value, expected):
        raise McpJsonSchemaError(path, f"expected {expected}")
    return expected if isinstance(expected, str) else None


def _validate_const(value: object, schema: Mapping[str, object], path: str) -> None:
    if "const" in schema and not _json_equal(value, schema["const"]):
        raise McpJsonSchemaError(path, "value does not match const")


def _validate_enum(value: object, schema: Mapping[str, object], path: str) -> None:
    allowed = schema.get("enum")
    if (
        isinstance(allowed, Sequence)
        and not isinstance(allowed, str)
        and not any(_json_equal(value, item) for item in allowed)
    ):
        raise McpJsonSchemaError(path, "value is outside enum")


def _validate_container(value: object, schema: Mapping[str, object], path: str, expected: str | None) -> None:
    if expected == "object":
        _validate_object(value, schema, path)
    elif expected == "array":
        _validate_array(value, schema, path)


def _validate_object(value: object, schema: Mapping[str, object], path: str) -> None:
    if not isinstance(value, Mapping):
        return
    properties_value = schema.get("properties", {})
    properties = properties_value if isinstance(properties_value, Mapping) else {}
    _validate_required(value, schema, path)
    _validate_extras(value, schema, properties, path)
    _validate_properties(value, properties, path)


def _validate_required(value: Mapping[object, object], schema: Mapping[str, object], path: str) -> None:
    required_value = schema.get("required", ())
    required = required_value if isinstance(required_value, Sequence) and not isinstance(required_value, str) else ()
    for name in required:
        if isinstance(name, str) and name not in value:
            raise McpJsonSchemaError(f"{path}.{name}", "required property is missing")


def _validate_extras(
    value: Mapping[object, object],
    schema: Mapping[str, object],
    properties: Mapping[object, object],
    path: str,
) -> None:
    if schema.get("additionalProperties") is False:
        extras = sorted(str(name) for name in value if name not in properties)
        if extras:
            raise McpJsonSchemaError(path, f"additional properties are not allowed: {', '.join(extras)}")


def _validate_properties(value: Mapping[object, object], properties: Mapping[object, object], path: str) -> None:
    for name, child_schema in properties.items():
        if name not in value or not isinstance(name, str) or not isinstance(child_schema, Mapping):
            continue
        validate_mcp_json_schema(value[name], child_schema, path=f"{path}.{name}")


def _validate_array(value: object, schema: Mapping[str, object], path: str) -> None:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return
    _validate_array_bounds(value, schema, path)
    unique_items = schema.get("uniqueItems")
    if "uniqueItems" in schema and not isinstance(unique_items, bool):
        raise McpJsonSchemaError(path, "advertised uniqueItems is invalid")
    if unique_items is True and _has_duplicate(value):
        raise McpJsonSchemaError(path, "array items must be unique")
    item_schema = schema.get("items")
    if not isinstance(item_schema, Mapping):
        return
    for index, item in enumerate(value):
        validate_mcp_json_schema(item, item_schema, path=f"{path}[{index}]")


def _validate_array_bounds(value: Sequence[object], schema: Mapping[str, object], path: str) -> None:
    minimum = schema.get("minItems")
    maximum = schema.get("maxItems")
    if isinstance(minimum, int) and len(value) < minimum:
        raise McpJsonSchemaError(path, f"requires at least {minimum} items")
    if isinstance(maximum, int) and len(value) > maximum:
        raise McpJsonSchemaError(path, f"allows at most {maximum} items")


def _validate_numeric_bounds(value: object, schema: Mapping[str, object], path: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if isinstance(minimum, int | float) and value < minimum:
        raise McpJsonSchemaError(path, f"must be greater than or equal to {minimum}")
    if isinstance(maximum, int | float) and value > maximum:
        raise McpJsonSchemaError(path, f"must be less than or equal to {maximum}")


def _validate_string(value: object, schema: Mapping[str, object], path: str) -> None:
    pattern = _validated_pattern(schema, path)
    if isinstance(value, str):
        _validate_string_bounds(value, schema, path)
        _validate_string_pattern(value, pattern, path)
    if "format" in schema:
        _validate_format(value, schema["format"], path)


def _validated_pattern(schema: Mapping[str, object], path: str) -> str | None:
    pattern = schema.get("pattern")
    if "pattern" in schema and not isinstance(pattern, str):
        raise McpJsonSchemaError(path, "advertised pattern is invalid")
    return pattern if isinstance(pattern, str) else None


def _validate_string_bounds(value: str, schema: Mapping[str, object], path: str) -> None:
    minimum = schema.get("minLength")
    maximum = schema.get("maxLength")
    if isinstance(minimum, int) and len(value) < minimum:
        raise McpJsonSchemaError(path, f"requires at least {minimum} characters")
    if isinstance(maximum, int) and len(value) > maximum:
        raise McpJsonSchemaError(path, f"allows at most {maximum} characters")


def _validate_string_pattern(value: str, pattern: str | None, path: str) -> None:
    if pattern is None:
        return
    try:
        if re.search(pattern, value) is None:
            raise McpJsonSchemaError(path, "string does not match pattern")
    except re.error as exc:
        raise McpJsonSchemaError(path, "advertised pattern is invalid") from exc


def _validate_one_of(value: object, schema: Mapping[str, object], path: str) -> None:
    choices = schema.get("oneOf")
    if choices is None:
        return
    if not isinstance(choices, Sequence) or isinstance(choices, str | bytes):
        raise McpJsonSchemaError(path, "advertised oneOf is invalid")
    matches = 0
    for choice in choices:
        if not isinstance(choice, Mapping):
            raise McpJsonSchemaError(path, "advertised oneOf is invalid")
        try:
            validate_mcp_json_schema(value, choice, path=path)
        except McpJsonSchemaError:
            continue
        matches += 1
    if matches != 1:
        raise McpJsonSchemaError(path, "value must match exactly one oneOf schema")


def _validate_format(value: object, raw_format: object, path: str) -> None:
    if not isinstance(raw_format, str) or raw_format not in _FORMAT_VALIDATORS:
        raise McpJsonSchemaError(path, f"unsupported schema format: {raw_format}")
    if not _FORMAT_VALIDATORS[raw_format](value):
        raise McpJsonSchemaError(path, f"value does not match {raw_format} format")


def _has_duplicate(values: Sequence[object]) -> bool:
    return any(_json_equal(value, previous) for index, value in enumerate(values) for previous in values[:index])


def _json_equal(left: object, right: object) -> bool:
    if _either_boolean(left, right):
        return type(left) is type(right) and left == right
    if _both_numeric(left, right):
        return left == right
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return _json_mapping_equal(left, right)
    if _is_json_sequence(left) and _is_json_sequence(right):
        return _json_sequence_equal(left, right)
    return type(left) is type(right) and left == right


def _either_boolean(left: object, right: object) -> bool:
    return isinstance(left, bool) or isinstance(right, bool)


def _both_numeric(left: object, right: object) -> bool:
    return isinstance(left, int | float) and isinstance(right, int | float)


def _json_mapping_equal(left: Mapping[object, object], right: Mapping[object, object]) -> bool:
    return set(left) == set(right) and all(_json_equal(left[key], right[key]) for key in left)


def _json_sequence_equal(left: Sequence[object], right: Sequence[object]) -> bool:
    return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right, strict=True))


def _is_json_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray)


def _is_date(value: object) -> bool:
    if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_datetime(value: object) -> bool:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value) is None
    ):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _is_nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_object_reference(value: object) -> bool:
    return isinstance(value, Mapping) and all(_is_nonempty_string(value.get(key)) for key in ("objectType", "objectId"))


def _is_media_reference(value: object, reference_kind: str = "media") -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    required = (
        "mediaSetId",
        "mediaItemId",
        "mediaItemVersionId",
        "logicalPath",
        "contentHash",
        "mimeType",
        "classification",
    )
    return bool(
        isinstance(value, Mapping)
        and value.get("referenceKind") == reference_kind
        and all(_is_nonempty_string(value.get(key)) for key in required)
        and isinstance(value.get("byteSize"), int)
        and not isinstance(value.get("byteSize"), bool)
        and value["byteSize"] >= 0
    )


_FORMAT_VALIDATORS = {
    "date": _is_date,
    "date-time": _is_datetime,
    "foundry-object": _is_nonempty_string,
    "foundry-interface": _is_nonempty_string,
    "foundry-media-version-id": _is_nonempty_string,
    "foundry-attachment-version-id": _is_nonempty_string,
    "foundry-media-reference": _is_media_reference,
    "foundry-attachment-reference": lambda value: _is_media_reference(value, "attachment"),
    "foundry-object-reference": _is_object_reference,
    "foundry-interface-reference": _is_object_reference,
}


def _matches_type(value: object, expected: str) -> bool:
    checks = {
        "object": lambda: isinstance(value, Mapping),
        "array": lambda: isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray),
        "string": lambda: isinstance(value, str),
        "integer": lambda: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda: isinstance(value, int | float) and not isinstance(value, bool),
        "boolean": lambda: isinstance(value, bool),
        "null": lambda: value is None,
    }
    check = checks.get(expected)
    if check is None:
        raise McpJsonSchemaError("$", f"unsupported schema type: {expected}")
    return check()
