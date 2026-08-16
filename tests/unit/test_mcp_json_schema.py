"""Focused proof for the MCP-advertised JSON Schema subset."""

from __future__ import annotations

import pytest
from foundry_lite.application.services.mcp_json_schema import McpJsonSchemaError, validate_mcp_json_schema

_SCHEMA = {
    "type": "object",
    "properties": {
        "mode": {"type": "string", "enum": ["safe"]},
        "options": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 3},
                "labels": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 2},
            },
            "required": ["limit", "labels"],
            "additionalProperties": False,
        },
    },
    "required": ["mode", "options"],
    "additionalProperties": False,
}


def test_mcp_json_schema_accepts_nested_advertised_contract() -> None:
    validate_mcp_json_schema({"mode": "safe", "options": {"limit": 2, "labels": ["a", "b"]}}, _SCHEMA)


@pytest.mark.parametrize(
    ("value", "path", "reason"),
    [
        ({"mode": "unsafe", "options": {"limit": 2, "labels": ["a"]}}, "$.mode", "outside enum"),
        ({"mode": "safe", "options": {"limit": True, "labels": ["a"]}}, "$.options.limit", "integer"),
        ({"mode": "safe", "options": {"limit": 0, "labels": ["a"]}}, "$.options.limit", "greater"),
        ({"mode": "safe", "options": {"limit": 2, "labels": [7]}}, "$.options.labels[0]", "string"),
        ({"mode": "safe", "options": {"limit": 2}}, "$.options.labels", "required"),
        ({"mode": "safe", "options": {"limit": 2, "labels": ["a"], "extra": 1}}, "$.options", "additional"),
        ({"mode": "safe", "options": {"limit": 2, "labels": []}}, "$.options.labels", "at least"),
    ],
)
def test_mcp_json_schema_rejects_nested_type_enum_bounds_required_and_extras(
    value: object,
    path: str,
    reason: str,
) -> None:
    with pytest.raises(McpJsonSchemaError) as exc_info:
        validate_mcp_json_schema(value, _SCHEMA)

    assert exc_info.value.path == path
    assert reason in exc_info.value.reason


def test_mcp_json_schema_enforces_const_pattern_unique_items_and_one_of() -> None:
    schema = {
        "type": "object",
        "properties": {
            "objectType": {"type": "string", "const": "Order"},
            "objectId": {"type": "string", "pattern": r"\S"},
            "targets": {"type": "array", "items": {"type": "integer"}, "uniqueItems": True},
            "reference": {
                "oneOf": [
                    {"type": "string", "format": "foundry-object"},
                    {
                        "type": "object",
                        "properties": {"objectType": {"type": "string"}, "objectId": {"type": "string"}},
                        "required": ["objectType", "objectId"],
                        "additionalProperties": False,
                    },
                ]
            },
        },
        "required": ["objectType", "objectId", "targets", "reference"],
        "additionalProperties": False,
    }
    validate_mcp_json_schema({"objectType": "Order", "objectId": "O-1", "targets": [1, 2], "reference": "O-1"}, schema)
    for value, path, reason in (
        ({"objectType": "Other", "objectId": "O-1", "targets": [1], "reference": "O-1"}, "$.objectType", "const"),
        ({"objectType": "Order", "objectId": "   ", "targets": [1], "reference": "O-1"}, "$.objectId", "pattern"),
        ({"objectType": "Order", "objectId": "O-1", "targets": [1, 1.0], "reference": "O-1"}, "$.targets", "unique"),
    ):
        with pytest.raises(McpJsonSchemaError) as exc_info:
            validate_mcp_json_schema(value, schema)
        assert exc_info.value.path == path
        assert reason in exc_info.value.reason


@pytest.mark.parametrize(
    ("value", "schema", "reason"),
    [
        ("2026-02-30", {"type": "string", "format": "date"}, "date format"),
        ("2026-08-09T12:30:00", {"type": "string", "format": "date-time"}, "date-time format"),
        ({"objectType": "Order"}, {"type": "object", "format": "foundry-object-reference"}, "object-reference"),
        ("value", {"type": "string", "format": "unknown-format"}, "unsupported schema format"),
    ],
)
def test_mcp_json_schema_enforces_advertised_formats(value: object, schema: dict[str, object], reason: str) -> None:
    with pytest.raises(McpJsonSchemaError, match=reason):
        validate_mcp_json_schema(value, schema)


@pytest.mark.parametrize(
    ("value", "schema", "reason"),
    [
        ("O-1", {"type": "string", "pattern": 7}, "advertised pattern is invalid"),
        ([1], {"type": "array", "uniqueItems": "true"}, "advertised uniqueItems is invalid"),
    ],
)
def test_mcp_json_schema_rejects_malformed_advertised_keyword_contracts(
    value: object,
    schema: dict[str, object],
    reason: str,
) -> None:
    with pytest.raises(McpJsonSchemaError, match=reason):
        validate_mcp_json_schema(value, schema)


@pytest.mark.parametrize(
    ("value", "schema", "reason"),
    [
        ("value", {"type": ["string", "null"]}, "advertised type is invalid"),
        ({}, {"type": "object", "properties": []}, "advertised properties is invalid"),
        ({}, {"type": "object", "required": "name"}, "advertised required is invalid"),
        ({}, {"type": "object", "required": [7]}, "advertised required is invalid"),
        ({}, {"type": "object", "additionalProperties": "false"}, "advertised additionalProperties is invalid"),
        ([], {"type": "array", "items": "string"}, "advertised items is invalid"),
        ([], {"type": "array", "minItems": True}, "advertised minItems is invalid"),
        ([], {"type": "array", "maxItems": -1}, "advertised maxItems is invalid"),
        ("x", {"type": "string", "minLength": -1}, "advertised minLength is invalid"),
        ("x", {"type": "string", "maxLength": False}, "advertised maxLength is invalid"),
        (1, {"type": "integer", "minimum": "0"}, "advertised minimum is invalid"),
        (1, {"type": "integer", "maximum": True}, "advertised maximum is invalid"),
    ],
)
def test_mcp_json_schema_never_silently_skips_malformed_advertised_constraints(
    value: object,
    schema: dict[str, object],
    reason: str,
) -> None:
    with pytest.raises(McpJsonSchemaError, match=reason):
        validate_mcp_json_schema(value, schema)


def test_nested_unsupported_type_reports_the_exact_property_path() -> None:
    schema = {"type": "object", "properties": {"name": {"type": "text"}}}

    with pytest.raises(McpJsonSchemaError) as exc_info:
        validate_mcp_json_schema({"name": "value"}, schema)

    assert exc_info.value.path == "$.name"
    assert exc_info.value.reason == "unsupported schema type: text"


def test_json_equality_keeps_booleans_distinct_but_matches_numeric_values_recursively() -> None:
    validate_mcp_json_schema(
        [{"value": 1.0}, [2]],
        {"type": "array", "const": [{"value": 1}, [2.0]], "uniqueItems": True},
    )
    with pytest.raises(McpJsonSchemaError, match="outside enum"):
        validate_mcp_json_schema(True, {"enum": [1]})


@pytest.mark.parametrize(
    ("value", "schema", "reason"),
    [
        ([1, 2], {"type": "array", "maxItems": 1}, "at most 1 items"),
        (4, {"type": "integer", "maximum": 3}, "less than or equal to 3"),
        ("", {"type": "string", "minLength": 1}, "at least 1 characters"),
        ("long", {"type": "string", "maxLength": 3}, "at most 3 characters"),
        ("value", {"type": "string", "pattern": "["}, "advertised pattern is invalid"),
        ("value", {"oneOf": "string"}, "advertised oneOf is invalid"),
        ("value", {"oneOf": [{"type": "string"}, 7]}, "advertised oneOf is invalid"),
        (1, {"oneOf": [{"type": "number"}, {"type": "integer"}]}, "exactly one"),
    ],
)
def test_mcp_json_schema_covers_upper_bounds_and_fail_closed_combinators(
    value: object,
    schema: dict[str, object],
    reason: str,
) -> None:
    with pytest.raises(McpJsonSchemaError, match=reason):
        validate_mcp_json_schema(value, schema)


def test_container_keywords_are_not_applied_after_a_non_container_type_was_accepted() -> None:
    validate_mcp_json_schema("value", {"properties": {"name": {"type": "string"}}})
    validate_mcp_json_schema(7, {"items": {"type": "string"}})


@pytest.mark.parametrize(
    ("value", "schema"),
    [
        ("not-a-date", {"type": "string", "format": "date"}),
        ("2026-13-40T25:61:61Z", {"type": "string", "format": "date-time"}),
        ("media-version-1", {"type": "string", "format": "foundry-media-reference"}),
        (
            {
                "referenceKind": "media",
                "mediaSetId": "set-1",
                "mediaItemId": "item-1",
                "mediaItemVersionId": "version-1",
                "logicalPath": "receipt.jpg",
                "contentHash": "sha256:value",
                "mimeType": "image/jpeg",
                "classification": "internal",
                "byteSize": 10,
            },
            {"type": "object", "format": "foundry-media-reference"},
        ),
    ],
)
def test_date_datetime_and_media_reference_edge_shapes(value: object, schema: dict[str, object]) -> None:
    if value == "not-a-date" or value == "2026-13-40T25:61:61Z":
        with pytest.raises(McpJsonSchemaError, match="format"):
            validate_mcp_json_schema(value, schema)
        return
    validate_mcp_json_schema(value, schema)


def test_media_reference_rejects_empty_text_and_wrong_reference_kind() -> None:
    with pytest.raises(McpJsonSchemaError, match="media-reference format"):
        validate_mcp_json_schema(" ", {"type": "string", "format": "foundry-media-reference"})
    with pytest.raises(McpJsonSchemaError, match="attachment-reference format"):
        validate_mcp_json_schema(
            {"referenceKind": "media"},
            {"type": "object", "format": "foundry-attachment-reference"},
        )
