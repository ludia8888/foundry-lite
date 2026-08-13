"""Boundary request and collaborator contracts for the Builder MCP gateway."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from foundry_lite.application.services.aip.fde_tool_result import FdePlatformToolRequest
from foundry_lite.application.services.aip.tool_broker import ToolBrokerResult
from foundry_lite.application.services.mcp_json_rpc import JsonRpcRequestId
from foundry_lite.domain.context import RequestContext

JsonObject = Mapping[str, object]


@dataclass(frozen=True)
class FdeMcpToolCall:
    """Normalized one-tool JSON-RPC request at the Builder MCP boundary."""

    application_id: str
    session_id: str
    json_rpc_id: JsonRpcRequestId
    mode: str
    workspace_ref: str
    tool_id: str
    arguments: JsonObject
    confirmation_receipt: str | None = None
    raw_input: JsonObject | None = None
    origin: str | None = None


class FdeMcpContextValidator(Protocol):
    def validate_scope(self, ctx: RequestContext, mode: str, workspace_ref: str) -> None: ...


class FdeMcpPlatformExecutor(Protocol):
    def execute(self, ctx: RequestContext, request: FdePlatformToolRequest) -> ToolBrokerResult: ...


class FdeMcpApplicationReader(Protocol):
    def get_application(self, app_id: str, *, ctx: RequestContext | None = None) -> JsonObject: ...


class FdeMcpAccessSessionValidator(Protocol):
    def require_active(self, ctx: RequestContext, application_id: str) -> None: ...
