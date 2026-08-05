"""Deterministic lazy tool discovery helpers for Builder MCP."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from foundry_lite.application.ports.ai_run_repository import AiToolCallRecord
from foundry_lite.application.ports.osdk_security_repository import OsdkMcpToolActivationRecord
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.aip.agent_runtime_ledger import hash_json
from foundry_lite.application.services.aip.tool_broker import ToolSpec
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed


class McpSearchCall(Protocol):
    @property
    def application_id(self) -> str: ...

    @property
    def session_id(self) -> str: ...

    @property
    def arguments(self) -> Mapping[str, object]: ...


def mcp_search_tool(modes: tuple[str, ...]) -> dict[str, object]:
    return {
        "name": "search_tools",
        "title": "search_tools",
        "description": "Search the permitted server-owned tool catalog and activate relevant tools for this session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": list(modes)},
                "workspaceRef": {"type": "string"},
                "arguments": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "maxResults": {"type": "integer", "minimum": 1, "maximum": 12},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
            "required": ["mode", "workspaceRef", "arguments"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    }


def allowed_modes(allowed: Mapping[str, tuple[ToolSpec, set[str]]]) -> tuple[str, ...]:
    return tuple(sorted({mode for _tool, modes in allowed.values() for mode in modes}))


def is_search(tool: ToolSpec) -> bool:
    return tool.tool_id == "fde.tools.search"


def required_search_query(arguments: Mapping[str, object]) -> str:
    value = arguments.get("query")
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise ValidationFailed("Builder MCP search_tools query must be 1-500 characters")
    return value.strip()


def search_limit(value: object) -> int:
    resolved = 8 if value is None else value
    if not isinstance(resolved, int) or isinstance(resolved, bool) or not 1 <= resolved <= 12:
        raise ValidationFailed("Builder MCP search_tools maxResults must be between 1 and 12")
    return resolved


def rank_tools(query: str, tools: tuple[ToolSpec, ...], limit: int) -> tuple[tuple[ToolSpec, int], ...]:
    terms = tuple(dict.fromkeys(part for part in _search_text(query).split() if part))
    ranked = [(tool, _tool_score(tool, terms)) for tool in tools]
    matches = [item for item in ranked if item[1] > 0]
    return tuple(sorted(matches, key=lambda item: (-item[1], item[0].tool_id))[:limit])


def activation_record(
    ctx: RequestContext, request: McpSearchCall, tool_id: str, query_hash: str
) -> OsdkMcpToolActivationRecord:
    return OsdkMcpToolActivationRecord(
        activation_id=_new_id("osdk_mcp_tool_activation"),
        tenant_id=ctx.tenant_id,
        app_id=request.application_id,
        session_id=request.session_id,
        client_id=ctx.client_id or "",
        actor_user_id=ctx.actor_user_id,
        tool_id=tool_id,
        query_hash=query_hash,
        activated_at=_now(),
    )


def search_ledger(
    ctx: RequestContext,
    request: McpSearchCall,
    run_id: str,
    output: Mapping[str, object],
) -> AiToolCallRecord:
    now = _now()
    return AiToolCallRecord(
        id=f"{run_id}-tool-1",
        tenant_id=ctx.tenant_id,
        ai_run_id=run_id,
        sequence=1,
        tool_id="search_tools",
        tool_version="v1",
        arguments_hash=hash_json(request.arguments),
        effect="READ",
        authorization_decision="allowed",
        confirmation_policy="NONE",
        status="succeeded",
        result_hash=hash_json(output),
        linked_action_run_id=None,
        started_at=now,
        completed_at=now,
        error_json=None,
        result_json=dict(output),
    )


def _tool_score(tool: ToolSpec, terms: tuple[str, ...]) -> int:
    tool_id = _search_text(tool.tool_id)
    description = _search_text(tool.description)
    return sum(8 if term in tool_id else 2 if term in description else 0 for term in terms)


def _search_text(value: str) -> str:
    normalized = "".join(character if character.isalnum() else " " for character in value.casefold())
    return " ".join(normalized.split())
