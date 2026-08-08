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
