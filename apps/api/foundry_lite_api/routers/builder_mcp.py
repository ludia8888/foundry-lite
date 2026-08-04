"""MCP Streamable HTTP transport for the governed AI FDE builder tool plane."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Mapping

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from foundry_lite.application.services.aip.fde_catalog import FDE_MODES
from foundry_lite.application.services.aip.fde_mcp_service import FdeMcpToolCall
from foundry_lite.domain.errors import FoundryLiteError, ValidationFailed

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.request_context import _ctx

router = APIRouter()
_PROTOCOL_VERSION = "2025-06-18"


@router.post("/mcp/builder/{application_id}")
async def builder_mcp_post(application_id: str, request: Request) -> Response:
    try:
        _require_origin(request)
        payload = await _json_body(request)
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc
    rpc_id = payload.get("id")
    try:
        result, session_id = _dispatch(application_id, request, payload)
    except FoundryLiteError as exc:
        return JSONResponse(_rpc_error(rpc_id, exc), status_code=200)
    if rpc_id is None:
        return Response(status_code=202, headers={"Mcp-Session-Id": session_id})
    return JSONResponse(
        {"jsonrpc": "2.0", "id": rpc_id, "result": result},
        headers={"Mcp-Session-Id": session_id},
    )


@router.get("/mcp/builder/{application_id}")
async def builder_mcp_get(application_id: str, request: Request) -> StreamingResponse:
    try:
        _require_origin(request)
        runtime.foundry.aip.fde_mcp_tools(application_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc
    session_id = _session_id(application_id, request)
    return StreamingResponse(
        _ready_events(application_id, session_id),
        media_type="text/event-stream",
        headers={"Mcp-Session-Id": session_id, "Cache-Control": "no-cache"},
    )


@router.delete("/mcp/builder/{application_id}")
def builder_mcp_delete(application_id: str, request: Request) -> Response:
    try:
        _require_origin(request)
        runtime.foundry.aip.fde_mcp_tools(application_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc
    return Response(status_code=204)


@router.get("/.well-known/oauth-protected-resource/mcp/builder/{application_id}")
def builder_mcp_protected_resource(application_id: str, request: Request) -> dict[str, object]:
    base = str(request.base_url).rstrip("/")
    return {
        "resource": f"{base}/mcp/builder/{application_id}",
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
        "scopes_supported": [f"osdk:connector:fde_{mode.mode_id}:execute" for mode in FDE_MODES],
    }


@router.get("/.well-known/oauth-authorization-server")
def builder_mcp_authorization_server(request: Request) -> dict[str, object]:
    base = str(request.base_url).rstrip("/")
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/api/auth/osdk/oauth/authorize",
        "token_endpoint": f"{base}/api/auth/osdk/oauth/token",
        "revocation_endpoint": f"{base}/api/auth/osdk/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    }


def _dispatch(
    application_id: str,
    request: Request,
    payload: Mapping[str, object],
) -> tuple[Mapping[str, object], str]:
    _require_json_rpc(payload)
    method = payload.get("method")
    session_id = _session_id(application_id, request)
    if method == "initialize":
        return _initialize_result(), session_id
    if method == "notifications/initialized":
        return {}, session_id
    if method == "tools/list":
        return runtime.foundry.aip.fde_mcp_tools(application_id, ctx=_ctx(request)), session_id
    if method == "tools/call":
        return _call_tool(application_id, session_id, request, payload), session_id
    raise ValidationFailed("unsupported Builder MCP JSON-RPC method", details={"method": method})


def _call_tool(
    application_id: str,
    session_id: str,
    request: Request,
    payload: Mapping[str, object],
) -> Mapping[str, object]:
    params = _mapping(payload.get("params"), "params")
    arguments = _mapping(params.get("arguments"), "params.arguments")
    tool_arguments = _mapping(arguments.get("arguments"), "params.arguments.arguments")
    rpc_id = payload.get("id")
    if not isinstance(rpc_id, str | int):
        raise ValidationFailed("Builder MCP tools/call requires a JSON-RPC id")
    call = FdeMcpToolCall(
        application_id=application_id,
        session_id=session_id,
        json_rpc_id=str(rpc_id),
        mode=_text(arguments, "mode"),
        workspace_ref=_text(arguments, "workspaceRef"),
        tool_id=_text(params, "name"),
        arguments=tool_arguments,
        confirmed_tool_id=request.headers.get("X-FDE-Confirm-Tool"),
    )
    return runtime.foundry.aip.run_fde_mcp_tool(call, ctx=_ctx(request))


def _initialize_result() -> dict[str, object]:
    return {
        "protocolVersion": _PROTOCOL_VERSION,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": "foundry-lite-builder-mcp", "version": "1.0.0"},
        "instructions": (
            "Use explicit workspaceRef values. Mutations require an out-of-band X-FDE-Confirm-Tool header. "
            "Approval, merge, deploy, and activation tools are never exposed."
        ),
    }


async def _ready_events(application_id: str, session_id: str) -> AsyncIterator[str]:
    data = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "notifications/message",
            "params": {"level": "info", "data": {"applicationId": application_id, "status": "ready"}},
        },
        sort_keys=True,
    )
    yield f"id: {session_id}:1\nevent: message\ndata: {data}\n\n"
    yield ": heartbeat\n\n"


async def _json_body(request: Request) -> dict[str, object]:
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise ValidationFailed("Builder MCP request body must be JSON") from exc
    return _mapping(payload, "request")


def _require_json_rpc(payload: Mapping[str, object]) -> None:
    if payload.get("jsonrpc") != "2.0" or not isinstance(payload.get("method"), str):
        raise ValidationFailed("Builder MCP requires a JSON-RPC 2.0 request")


def _require_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is not None and origin not in runtime.ALLOWED_BROWSER_ORIGINS:
        raise ValidationFailed("Builder MCP Origin is not allowed")


def _session_id(application_id: str, request: Request) -> str:
    provided = request.headers.get("Mcp-Session-Id")
    if provided:
        return provided[:255]
    raw = f"{application_id}:{request.state.request_id}"
    return f"mcp-{hashlib.sha256(raw.encode()).hexdigest()[:24]}"


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValidationFailed(f"Builder MCP {field} must be an object")
    return {str(name): item for name, item in value.items()}


def _text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValidationFailed(f"Builder MCP {key} is required")
    return item.strip()


def _rpc_error(rpc_id: object, exc: FoundryLiteError) -> dict[str, object]:
    code = -32602 if exc.code == "VALIDATION_FAILED" else -32001
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {"code": code, "message": exc.message, "data": {"type": exc.code, **exc.details}},
    }
