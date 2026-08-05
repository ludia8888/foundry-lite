from __future__ import annotations

from typing import cast

from foundry_lite.application.services.ontology_mcp_tools import approval_status_tool, function_tools


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
