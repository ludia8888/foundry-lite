"""Shared MCP lifecycle and Streamable HTTP wire validation."""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import Request
from foundry_lite.domain.errors import FoundryLiteError, ValidationFailed
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from foundry_lite_api.mcp_envelope import JsonRpcEnvelope

SUPPORTED_PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = frozenset({SUPPORTED_PROTOCOL_VERSION})
_REQUEST_METHODS = frozenset({"initialize", "ping", "tools/list", "tools/call"})


class McpMethodNotFound(FoundryLiteError):
    """An otherwise valid JSON-RPC request addressed an unsupported method."""

    code = "MCP_METHOD_NOT_FOUND"


class McpInvalidRequest(FoundryLiteError):
    """The message cannot be a valid MCP request or notification."""

    code = "MCP_INVALID_REQUEST"


class _McpClientInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    title: str | None = None


class _McpInitializeParams(BaseModel):
    model_config = ConfigDict(extra="allow")

    protocol_version: str = Field(alias="protocolVersion", min_length=1)
    capabilities: dict[str, object]
    client_info: _McpClientInfo = Field(alias="clientInfo")


def require_mcp_protocol_version(request: Request, *, is_initialization: bool = False) -> str | None:
    """Require the negotiated version after initialize; initialization itself has no header requirement."""

    received = request.headers.get("MCP-Protocol-Version")
    if received is None:
        if is_initialization:
            return None
        raise ValidationFailed(
            "MCP-Protocol-Version header is required after initialize",
            details={"supportedProtocolVersions": sorted(SUPPORTED_PROTOCOL_VERSIONS)},
        )
    if received in SUPPORTED_PROTOCOL_VERSIONS:
        return received
    raise ValidationFailed(
        "MCP protocol version is not supported",
        details={
            "receivedProtocolVersion": received,
            "supportedProtocolVersions": sorted(SUPPORTED_PROTOCOL_VERSIONS),
        },
    )


def validate_mcp_message(payload: JsonRpcEnvelope) -> str | None:
    """Validate method-level request/notification shape and negotiate initialization."""

    if payload.method in _REQUEST_METHODS:
        _require_request_id(payload)
    elif payload.method == "notifications/initialized":
        _require_notification(payload)
    elif payload.has_explicit_id:
        _require_request_id(payload)
    if payload.method == "initialize":
        return _initialize_protocol_version(payload.params)
    if payload.method == "tools/list":
        validate_tools_list_params(payload.params)
    return None


def validate_tools_list_params(
    params: Mapping[str, object],
    *,
    is_builder: bool = False,
) -> None:
    """Validate list pagination shape while returning a complete unpaginated result."""

    cursor = params.get("cursor")
    if "cursor" in params:
        if not isinstance(cursor, str) or not cursor:
            raise ValidationFailed("MCP tools/list cursor must be a non-empty string")
        raise ValidationFailed("MCP tools/list pagination cursor is not supported")
    metadata = params.get("_meta")
    if "_meta" in params and not isinstance(metadata, Mapping):
        raise ValidationFailed("MCP tools/list _meta must be an object")
    if is_builder:
        _validate_builder_discovery_mode(params)


def method_not_found(plane: str, method: str) -> McpMethodNotFound:
    return McpMethodNotFound(
        f"unsupported {plane} MCP JSON-RPC method",
        details={"method": method},
    )


def require_mcp_session_id(request: Request, plane: str) -> str:
    session_id = request.headers.get("Mcp-Session-Id")
    if not session_id:
        raise ValidationFailed(
            f"{plane} MCP request requires Mcp-Session-Id after initialize",
            details={"resource": "mcp_session"},
        )
    if len(session_id) > 255 or any(ord(character) < 0x21 or ord(character) > 0x7E for character in session_id):
        raise ValidationFailed(
            f"{plane} MCP session id must be at most 255 visible ASCII characters",
            details={"resource": "mcp_session"},
        )
    return session_id


def reject_initialize_session_id(request: Request) -> None:
    if request.headers.get("Mcp-Session-Id") is not None:
        raise McpInvalidRequest("MCP initialize request must not include Mcp-Session-Id")


def _require_request_id(payload: JsonRpcEnvelope) -> None:
    if not payload.has_explicit_id or type(payload.id) not in {str, int}:
        raise McpInvalidRequest(f"MCP {payload.method} requires a non-null string or integer id")


def _require_notification(payload: JsonRpcEnvelope) -> None:
    if payload.has_explicit_id:
        raise McpInvalidRequest(f"MCP {payload.method} notification must not include an id")


def _initialize_protocol_version(params: Mapping[str, object]) -> str:
    try:
        validated = _McpInitializeParams.model_validate(params)
    except ValidationError as exc:
        raise ValidationFailed(
            "MCP initialize params are invalid",
            details={"errorCount": exc.error_count()},
        ) from exc
    requested = validated.protocol_version
    return requested if requested in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSION


def _validate_builder_discovery_mode(params: Mapping[str, object]) -> None:
    if "discoveryMode" not in params:
        return
    mode = params.get("discoveryMode")
    if mode not in {"eager", "lazy"}:
        raise ValidationFailed("Builder MCP tools/list discoveryMode must be eager or lazy")
