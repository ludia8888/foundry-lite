from __future__ import annotations

from typing import Any, cast

import pytest
from foundry_lite.application.services.ontology_mcp_schema import validate_tool_arguments
from foundry_lite.application.services.ontology_mcp_tools import approval_status_tool, function_tools, object_tools
from foundry_lite.domain.errors import ValidationFailed


def test_object_search_tool_does_not_advertise_a_retrieval_mode_the_planner_rejects() -> None:
    """Regression: the tool told agents to combine keyword and semantic search, and it 400s.

    `_search_route` has rejected that pair since the object semantic-search work landed; the
    MCP description added later advertised it as hybrid retrieval, so an agent that believed
    the schema got VALIDATION_FAILED. The advertised contract must describe the planner.
    """

    search_tool = next(
        tool for tool in object_tools("Order", ("osdk:object:Order:read",)) if tool["name"] == "object.Order.search"
    )
    properties = cast(dict[str, Any], cast(dict[str, Any], search_tool["inputSchema"])["properties"])
    description = cast(str, properties["semanticText"]["description"])

    assert "hybrid" not in description.lower()
    assert "instead of" in description.lower()
    assert "rejects" in description.lower()


def test_ontology_mcp_function_tool_uses_version_pinned_typed_input_contract() -> None:
    tools = function_tools(
        "orderRiskSummary",
        ("osdk:function:orderRiskSummary:execute",),
        {
            "displayName": "Order risk summary",
            "version": "v7",
            "inputs": [
                {"apiName": "objectId", "type": "string", "required": True},
                {"apiName": "threshold", "type": "float", "required": False},
                {"apiName": "includeHistory", "type": "boolean", "required": False},
            ],
        },
    )

    assert len(tools) == 1
    tool = tools[0]
    outer = cast(dict[str, object], tool["inputSchema"])
    inputs = cast(dict[str, object], cast(dict[str, object], outer["properties"])["inputs"])
    properties = cast(dict[str, object], inputs["properties"])

    assert tool["name"] == "function.orderRiskSummary.execute"
    assert "v7" in str(tool["description"])
    assert inputs["required"] == ["objectId"]
    assert inputs["additionalProperties"] is False
    assert properties["objectId"] == {"type": "string"}
    assert properties["threshold"] == {"type": "number"}
    assert properties["includeHistory"] == {"type": "boolean"}


def test_ontology_mcp_approval_status_is_read_only_and_has_no_decision_surface() -> None:
    tool = approval_status_tool()

    assert tool["name"] == "action_approval.get"
    assert cast(dict[str, object], tool["annotations"])["readOnlyHint"] is True
    assert "approve" not in str(tool["name"])
    assert cast(dict[str, object], tool["inputSchema"])["required"] == ["reviewId"]


def test_ontology_mcp_function_tool_projects_nested_batch_input_schema() -> None:
    tool = function_tools(
        "batchOrderEdits",
        ("osdk:function:batchOrderEdits:execute",),
        {
            "version": "1.0.0",
            "inputs": [
                {
                    "apiName": "requests",
                    "type": "array",
                    "itemType": "struct",
                    "required": True,
                    "fields": [
                        {"apiName": "objectId", "type": "string", "required": True},
                        {"apiName": "priority", "type": "integer", "required": False},
                    ],
                }
            ],
        },
    )[0]

    outer = cast(dict[str, object], tool["inputSchema"])
    inputs = cast(dict[str, object], cast(dict[str, object], outer["properties"])["inputs"])
    requests = cast(dict[str, object], cast(dict[str, object], inputs["properties"])["requests"])
    item = cast(dict[str, object], requests["items"])

    assert requests["type"] == "array"
    assert item["required"] == ["objectId"]
    assert cast(dict[str, object], item["properties"])["priority"] == {"type": "integer"}


def test_ontology_mcp_argument_validator_enforces_nested_advertised_constraints() -> None:
    schema: dict[str, object] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["mode", "refs"],
        "properties": {
            "mode": {"type": "string", "enum": ["safe"], "minLength": 4, "maxLength": 4},
            "refs": {
                "type": "array",
                "minItems": 1,
                "maxItems": 2,
                "uniqueItems": True,
                "items": {
                    "oneOf": [
                        {"type": "string", "pattern": "^obj-"},
                        {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["referenceKind", "version"],
                            "properties": {
                                "referenceKind": {"const": "object"},
                                "version": {"type": "integer", "minimum": 1, "maximum": 3},
                            },
                        },
                    ]
                },
            },
        },
    }

    validate_tool_arguments(
        {"mode": "safe", "refs": ["obj-1", {"referenceKind": "object", "version": 2}]},
        schema,
    )
    invalid_values = (
        ({"mode": "safe", "refs": ["wrong"]}, "oneOf", "$.refs[0]"),
        ({"mode": "safe", "refs": ["obj-1", "obj-1"]}, "uniqueItems", "$.refs"),
        (
            {"mode": "safe", "refs": [{"referenceKind": "media", "version": 2}]},
            "oneOf",
            "$.refs[0]",
        ),
        ({"mode": "safe", "refs": [{"referenceKind": "object", "version": True}]}, "oneOf", "$.refs[0]"),
    )
    for arguments, rule, path in invalid_values:
        with pytest.raises(ValidationFailed) as caught:
            validate_tool_arguments(arguments, schema)
        assert caught.value.details["schemaRule"] == rule
        assert caught.value.details["path"] == path
