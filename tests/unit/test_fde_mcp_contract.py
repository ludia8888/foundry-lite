from __future__ import annotations

from dataclasses import replace

import pytest
from foundry_lite.application.services.aip.fde_mcp_contract import validate_outer_shape
from foundry_lite.application.services.aip.fde_mcp_types import FdeMcpToolCall
from foundry_lite.domain.errors import ValidationFailed


def test_validate_outer_shape_checks_envelope_but_defers_nested_tool_values() -> None:
    raw_input = {
        "mode": "platform_qa",
        "workspaceRef": "tenant:tenant-demo",
        "arguments": {"maxResults": "not-an-integer"},
    }
    request = FdeMcpToolCall(
        application_id="app-1",
        session_id="session-1",
        json_rpc_id="call-1",
        mode="platform_qa",
        workspace_ref="tenant:tenant-demo",
        tool_id="platform.docs.search",
        arguments={"maxResults": "not-an-integer"},
        raw_input=raw_input,
    )
    schema = {
        "type": "object",
        "properties": {
            "mode": {"type": "string"},
            "workspaceRef": {"type": "string"},
            "arguments": {
                "type": "object",
                "properties": {"maxResults": {"type": "integer"}},
                "additionalProperties": False,
            },
        },
        "required": ["mode", "workspaceRef", "arguments"],
        "additionalProperties": False,
    }

    validate_outer_shape(request, schema)

    with pytest.raises(ValidationFailed, match="outer input"):
        validate_outer_shape(request=replace(request, raw_input={**raw_input, "unexpected": True}), schema=schema)
