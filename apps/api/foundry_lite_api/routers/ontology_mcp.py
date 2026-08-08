"""Consumer Ontology MCP Streamable HTTP transport."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from uuid import uuid4

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from foundry_lite.application.services.ontology_mcp_gateway import OntologyMcpToolCall
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import FoundryLiteError, PermissionDenied, RateLimited, ValidationFailed
from pydantic import ValidationError as PydanticValidationError

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.mcp_authorization import (
    mcp_permission_failure,
    protected_resource_metadata,
    require_mcp_context,
)
from foundry_lite_api.mcp_envelope import JsonRpcEnvelope
from foundry_lite_api.mcp_protocol import (
    McpInvalidRequest,
    McpMethodNotFound,
    method_not_found,
    reject_initialize_session_id,
    require_mcp_protocol_version,
    require_mcp_session_id,
    validate_mcp_message,
)
from foundry_lite_api.mcp_rate_limit import mcp_rate_limit_http_error, mcp_result_headers

router = APIRouter()


@router.post("/mcp/ontology/{application_id}")
async def ontology_mcp_post(application_id: str, request: Request) -> Response:
    ctx, payload = await _ontology_mcp_admission(application_id, request)
    return _ontology_mcp_post_response(application_id, request, ctx, payload)


async def _ontology_mcp_admission(application_id: str, request: Request) -> tuple[RequestContext, JsonRpcEnvelope]:
    try:
        _require_origin(request)
        ctx = require_mcp_context(request, "ontology", application_id)
        runtime.foundry.ontology_mcp.consume_endpoint_rate_limit(ctx, application_id)
        payload = await _json_body(request)
        require_mcp_protocol_version(request, is_initialization=payload.method == "initialize")
    except RateLimited as exc:
        raise mcp_rate_limit_http_error(exc, request) from exc
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc
    return ctx, payload


def _ontology_mcp_post_response(
    application_id: str,
    request: Request,
    ctx: RequestContext,
    payload: JsonRpcEnvelope,
) -> Response:
    rpc_id = payload.id
    session_id: str | None = None
    try:
        negotiated_protocol = validate_mcp_message(payload)
        session_id = _request_session_id(request, payload)
        result = _dispatch(application_id, session_id, request, payload, negotiated_protocol, ctx)
    except FoundryLiteError as exc:
        return _ontology_mcp_domain_failure(application_id, request, payload, session_id, rpc_id, exc)
    except Exception:
        return _ontology_mcp_internal_failure(request, payload, session_id, rpc_id)
    return _ontology_mcp_success(payload, session_id, rpc_id, result)


def _ontology_mcp_domain_failure(
    application_id: str,
    request: Request,
    payload: JsonRpcEnvelope,
    session_id: str | None,
    rpc_id: object,
    exc: FoundryLiteError,
) -> Response:
    if isinstance(exc, PermissionDenied):
        raise mcp_permission_failure(exc, request, "ontology", application_id) from exc
    if exc.details.get("resource") == "mcp_session":
        raise _handle_error(exc, request) from exc
    if payload.is_notification:
        return Response(status_code=400, headers=_session_response_headers(session_id))
    return JSONResponse(_rpc_error(rpc_id, exc, request), status_code=200)


def _ontology_mcp_internal_failure(
    request: Request, payload: JsonRpcEnvelope, session_id: str | None, rpc_id: object
) -> Response:
    if payload.is_notification:
        return Response(status_code=500, headers=_session_response_headers(session_id))
    return JSONResponse(_internal_error(rpc_id, request), status_code=200)


def _ontology_mcp_success(
    payload: JsonRpcEnvelope, session_id: str | None, rpc_id: object, result: Mapping[str, object]
) -> Response:
    if session_id is None:
        raise RuntimeError("Ontology MCP session id was not resolved")
    if payload.is_notification:
        return Response(status_code=202, headers={"Mcp-Session-Id": session_id})
    return JSONResponse(
        {"jsonrpc": "2.0", "id": rpc_id, "result": result},
        headers=mcp_result_headers(result, {"Mcp-Session-Id": session_id}),
    )


@router.get("/mcp/ontology/{application_id}")
async def ontology_mcp_get(application_id: str, request: Request) -> StreamingResponse:
    try:
        _require_origin(request)
        require_mcp_protocol_version(request)
        ctx = require_mcp_context(request, "ontology", application_id)
        session_id = _required_existing_session_id(request)
        runtime.foundry.ontology_mcp.consume_endpoint_rate_limit(ctx, application_id)
        runtime.foundry.ontology_mcp.resume_session(
            ctx,
            application_id,
            session_id,
            origin=request.headers.get("origin"),
        )
        lease = runtime.foundry.ontology_mcp.claim_session_stream(
            ctx, application_id, session_id, origin=request.headers.get("origin")
        )
        try:
            events = runtime.foundry.ontology_mcp.session_events(
                ctx,
                application_id,
                session_id,
                after_sequence=_last_event_sequence(request, session_id),
            )
        except Exception:
            runtime.foundry.ontology_mcp.release_session_stream(ctx, application_id, session_id, lease.lease_id)
            raise
    except RateLimited as exc:
        raise mcp_rate_limit_http_error(exc, request) from exc
    except FoundryLiteError as exc:
        if isinstance(exc, PermissionDenied):
            raise mcp_permission_failure(exc, request, "ontology", application_id) from exc
        raise _handle_error(exc, request) from exc
    try:
        response = StreamingResponse(
            _session_events(events, session_id, ctx, application_id, lease.lease_id),
            media_type="text/event-stream",
            headers={"Mcp-Session-Id": session_id, "Cache-Control": "no-cache"},
        )
    except Exception:
        runtime.foundry.ontology_mcp.release_session_stream(ctx, application_id, session_id, lease.lease_id)
        raise
    return response


@router.delete("/mcp/ontology/{application_id}")
def ontology_mcp_delete(application_id: str, request: Request) -> Response:
    try:
        _require_origin(request)
        require_mcp_protocol_version(request)
        ctx = require_mcp_context(request, "ontology", application_id)
        session_id = _required_existing_session_id(request)
        runtime.foundry.ontology_mcp.consume_endpoint_rate_limit(ctx, application_id)
        runtime.foundry.ontology_mcp.close_session(ctx, application_id, session_id)
    except RateLimited as exc:
        raise mcp_rate_limit_http_error(exc, request) from exc
    except FoundryLiteError as exc:
        if isinstance(exc, PermissionDenied):
            raise mcp_permission_failure(exc, request, "ontology", application_id) from exc
        raise _handle_error(exc, request) from exc
    return Response(status_code=204)


@router.get("/.well-known/oauth-protected-resource/mcp/ontology/{application_id}")
def ontology_mcp_protected_resource(application_id: str, request: Request) -> dict[str, object]:
    return protected_resource_metadata(request, "ontology", application_id)


def _dispatch(
    application_id: str,
    session_id: str,
    request: Request,
    payload: JsonRpcEnvelope,
    negotiated_protocol: str | None,
    ctx: RequestContext,
) -> Mapping[str, object]:
    method = payload.method
    if method == "initialize":
        if negotiated_protocol is None:
            raise ValidationFailed("Ontology MCP protocol negotiation is missing")
        runtime.foundry.ontology_mcp.open_session(
            ctx,
            application_id,
            session_id,
            origin=request.headers.get("origin"),
        )
        return _initialize_result(negotiated_protocol)
    runtime.foundry.ontology_mcp.resume_session(
        ctx,
        application_id,
        session_id,
        origin=request.headers.get("origin"),
    )
    if method == "notifications/initialized":
        return {}
    if method == "ping":
        return {}
    if method == "tools/list":
        tools = runtime.foundry.ontology_mcp.list_tools(ctx, application_id, origin=request.headers.get("origin"))
        return tools
    if method == "tools/call":
        return _call_tool(application_id, session_id, request, payload, ctx)
    raise method_not_found("Ontology", method)


def _call_tool(
    application_id: str,
    session_id: str,
    request: Request,
    payload: JsonRpcEnvelope,
    ctx: RequestContext,
) -> Mapping[str, object]:
    params = payload.params
    rpc_id = payload.id
    if not isinstance(rpc_id, str | int):
        raise ValidationFailed("Ontology MCP tools/call requires a JSON-RPC id")
    call = OntologyMcpToolCall(
        application_id=application_id,
        session_id=session_id,
        json_rpc_id=rpc_id,
        tool_name=_text(params, "name"),
        arguments=_mapping(params.get("arguments", {}), "params.arguments"),
        origin=request.headers.get("origin"),
    )
    return runtime.foundry.ontology_mcp.execute_tool(ctx, call)


def _initialize_result(protocol_version: str) -> dict[str, object]:
    return {
        "protocolVersion": protocol_version,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": "foundry-lite-ontology-mcp", "version": "1.0.0"},
        "instructions": (
            "Only application-restricted Ontology resources are exposed. "
            "Low-risk autonomous Actions may run; all other Action apply calls return approval_required."
        ),
    }


async def _session_events(
    events: Sequence[Mapping[str, object]],
    session_id: str,
    ctx: RequestContext,
    application_id: str,
    lease_id: str,
) -> AsyncIterator[str]:
    try:
        for event in events:
            sequence = event["sequence"]
            if not isinstance(sequence, int):
                raise ValidationFailed("Ontology MCP event sequence is invalid")
            method = _session_notification_method(event.get("event_type"))
            params = event.get("payload_json")
            if not isinstance(params, Mapping):
                raise ValidationFailed("Ontology MCP event payload is invalid")
            data = json.dumps({"jsonrpc": "2.0", "method": method, "params": dict(params)}, sort_keys=True)
            yield f"id: {session_id}:{sequence}\nevent: message\ndata: {data}\n\n"
        yield ": heartbeat\n\n"
    finally:
        runtime.foundry.ontology_mcp.release_session_stream(ctx, application_id, session_id, lease_id)


def _session_notification_method(event_type: object) -> str:
    method = str(event_type)
    legacy_methods = {
        "session.ready": "notifications/session.ready",
        "tool.completed": "notifications/tool.completed",
    }
    if method in legacy_methods:
        return legacy_methods[method]
    if method.startswith("notifications/"):
        return method
    raise ValidationFailed("Ontology MCP session event type is invalid")


async def _json_body(request: Request) -> JsonRpcEnvelope:
    """Parse the fixed part of the protocol into a model; `params` stays for the tool to validate."""
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
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


def _request_session_id(request: Request, payload: JsonRpcEnvelope) -> str:
    if payload.method != "initialize":
        return _required_existing_session_id(request)
    reject_initialize_session_id(request)
    return f"ontology-mcp-{uuid4().hex}"


def _required_existing_session_id(request: Request) -> str:
    return require_mcp_session_id(request, "Ontology")


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


def _rpc_error(rpc_id: object, exc: FoundryLiteError, request: Request) -> dict[str, object]:
    code = _rpc_error_code(exc)
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {
            "code": code,
            "message": exc.message,
            "data": {"type": exc.code, **exc.details, "requestId": _request_id(request)},
        },
    }


def _rpc_error_code(exc: FoundryLiteError) -> int:
    if isinstance(exc, McpInvalidRequest):
        return -32600
    if isinstance(exc, McpMethodNotFound):
        return -32601
    return -32602 if exc.code == "VALIDATION_FAILED" else -32001


def _internal_error(rpc_id: object, request: Request) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {"code": -32603, "message": "Internal error", "data": {"requestId": _request_id(request)}},
    }


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "unknown"))


def _session_response_headers(session_id: str | None) -> dict[str, str]:
    return {"Mcp-Session-Id": session_id} if session_id is not None else {}
