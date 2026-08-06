"""Consumer Ontology MCP Streamable HTTP transport."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Mapping, Sequence

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from foundry_lite.application.services.ontology_mcp_gateway import OntologyMcpToolCall
from foundry_lite.domain.errors import FoundryLiteError, ValidationFailed
from pydantic import ValidationError as PydanticValidationError

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.mcp_envelope import JsonRpcEnvelope
from foundry_lite_api.request_context import _ctx

router = APIRouter()
_PROTOCOL_VERSION = "2025-06-18"


@router.post("/mcp/ontology/{application_id}")
async def ontology_mcp_post(application_id: str, request: Request) -> Response:
    try:
        _require_origin(request)
        payload = await _json_body(request)
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc
    rpc_id = payload.id
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


@router.get("/mcp/ontology/{application_id}")
async def ontology_mcp_get(application_id: str, request: Request) -> StreamingResponse:
    try:
        _require_origin(request)
        ctx = _ctx(request)
        session_id = _session_id(application_id, request)
        runtime.foundry.ontology_mcp.open_session(ctx, application_id, session_id, origin=request.headers.get("origin"))
        events = runtime.foundry.ontology_mcp.session_events(
            ctx,
            application_id,
            session_id,
            after_sequence=_last_event_sequence(request, session_id),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc
    return StreamingResponse(
        _session_events(events, session_id),
        media_type="text/event-stream",
        headers={"Mcp-Session-Id": session_id, "Cache-Control": "no-cache"},
    )


@router.delete("/mcp/ontology/{application_id}")
def ontology_mcp_delete(application_id: str, request: Request) -> Response:
    try:
        _require_origin(request)
        runtime.foundry.ontology_mcp.close_session(
            _ctx(request), application_id, _required_existing_session_id(request)
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc
    return Response(status_code=204)


@router.get("/.well-known/oauth-protected-resource/mcp/ontology/{application_id}")
def ontology_mcp_protected_resource(application_id: str, request: Request) -> dict[str, object]:
    base = str(request.base_url).rstrip("/")
    return {
        "resource": f"{base}/mcp/ontology/{application_id}",
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
        "scopes_supported": [
            "osdk:object:*:read",
            "osdk:action:*:validate",
            "osdk:action:*:execute",
            "osdk:function:*:execute",
        ],
    }


def _dispatch(
    application_id: str,
    request: Request,
    payload: JsonRpcEnvelope,
) -> tuple[Mapping[str, object], str]:
    method = payload.method
    session_id = _session_id(application_id, request)
    ctx = _ctx(request)
    runtime.foundry.ontology_mcp.open_session(ctx, application_id, session_id, origin=request.headers.get("origin"))
    if method == "initialize":
        return _initialize_result(), session_id
    if method == "notifications/initialized":
        return {}, session_id
    if method == "tools/list":
        tools = runtime.foundry.ontology_mcp.list_tools(ctx, application_id, origin=request.headers.get("origin"))
        return tools, session_id
    if method == "tools/call":
        return _call_tool(application_id, session_id, request, payload), session_id
    raise ValidationFailed("unsupported Ontology MCP JSON-RPC method", details={"method": method})


def _call_tool(
    application_id: str,
    session_id: str,
    request: Request,
    payload: JsonRpcEnvelope,
) -> Mapping[str, object]:
    params = payload.params
    rpc_id = payload.id
    if not isinstance(rpc_id, str | int):
        raise ValidationFailed("Ontology MCP tools/call requires a JSON-RPC id")
    call = OntologyMcpToolCall(
        application_id=application_id,
        session_id=session_id,
        json_rpc_id=str(rpc_id),
        tool_name=_text(params, "name"),
        arguments=_mapping(params.get("arguments", {}), "params.arguments"),
        origin=request.headers.get("origin"),
    )
    return runtime.foundry.ontology_mcp.execute_tool(_ctx(request), call)


def _initialize_result() -> dict[str, object]:
    return {
        "protocolVersion": _PROTOCOL_VERSION,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": "foundry-lite-ontology-mcp", "version": "1.0.0"},
        "instructions": (
            "Only application-restricted Ontology resources are exposed. "
            "Low-risk autonomous Actions may run; all other Action apply calls return approval_required."
        ),
    }


async def _session_events(events: Sequence[Mapping[str, object]], session_id: str) -> AsyncIterator[str]:
    for event in events:
        sequence = event["sequence"]
        if not isinstance(sequence, int):
            raise ValidationFailed("Ontology MCP event sequence is invalid")
        event_type = str(event["event_type"])
        data = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "notifications/message",
                "params": {"level": "info", "data": event["payload_json"]},
            },
            sort_keys=True,
        )
        yield f"id: {session_id}:{sequence}\nevent: {event_type}\ndata: {data}\n\n"
    yield ": heartbeat\n\n"


async def _json_body(request: Request) -> JsonRpcEnvelope:
    """Parse the fixed part of the protocol into a model; `params` stays for the tool to validate."""
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise ValidationFailed("Ontology MCP request body must be JSON") from exc
    try:
        return JsonRpcEnvelope.model_validate(payload)
    except PydanticValidationError as exc:
        raise ValidationFailed(
            "Ontology MCP requires a JSON-RPC 2.0 request",
            details={"errorCount": exc.error_count()},
        ) from exc


def _require_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is not None and origin not in runtime.ALLOWED_BROWSER_ORIGINS:
        raise ValidationFailed("Ontology MCP Origin is not allowed")


def _session_id(application_id: str, request: Request) -> str:
    provided = request.headers.get("Mcp-Session-Id")
    if provided:
        return provided[:255]
    raw = f"{application_id}:{request.state.request_id}"
    return f"ontology-mcp-{hashlib.sha256(raw.encode()).hexdigest()[:24]}"


def _required_existing_session_id(request: Request) -> str:
    session_id = request.headers.get("Mcp-Session-Id")
    if not session_id:
        raise ValidationFailed("Ontology MCP DELETE requires Mcp-Session-Id")
    return session_id


def _last_event_sequence(request: Request, session_id: str) -> int:
    last_event_id = request.headers.get("Last-Event-ID")
    if not last_event_id:
        return 0
    prefix = f"{session_id}:"
    if not last_event_id.startswith(prefix):
        raise ValidationFailed("Ontology MCP Last-Event-ID does not belong to this session")
    try:
        sequence = int(last_event_id.removeprefix(prefix))
    except ValueError as exc:
        raise ValidationFailed("Ontology MCP Last-Event-ID sequence is invalid") from exc
    if sequence < 0:
        raise ValidationFailed("Ontology MCP Last-Event-ID sequence is invalid")
    return sequence


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValidationFailed("Ontology MCP field must be an object", details={"field": field})
    return {str(name): item for name, item in value.items()}


def _text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValidationFailed("Ontology MCP field is required", details={"field": key})
    return item.strip()


def _rpc_error(rpc_id: object, exc: FoundryLiteError) -> dict[str, object]:
    code = -32602 if exc.code == "VALIDATION_FAILED" else -32001
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {"code": code, "message": exc.message, "data": {"type": exc.code, **exc.details}},
    }
