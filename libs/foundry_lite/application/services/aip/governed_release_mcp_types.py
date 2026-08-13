"""Boundary DTOs for the separate Governed Release MCP plane."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from foundry_lite.application.services.mcp_json_rpc import JsonRpcRequestId


@dataclass(frozen=True)
class GovernedReleaseMcpToolCall:
    """One normalized tools/call request from the release MCP transport."""

    application_id: str
    session_id: str
    json_rpc_id: JsonRpcRequestId
    tool_name: str
    arguments: Mapping[str, object]
    widget_confirmation_token: str | None = None
    origin: str | None = None


__all__ = ["GovernedReleaseMcpToolCall"]
