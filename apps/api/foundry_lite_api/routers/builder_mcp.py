"""MCP Streamable HTTP transport for the governed AI FDE builder tool plane."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from uuid import uuid4

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from foundry_lite.application.services.aip.fde_mcp_service import FdeMcpToolCall
from foundry_lite.application.services.mcp_session_namespace import require_mcp_session_namespace
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import FoundryLiteError, PermissionDenied, RateLimited, ValidationFailed
from pydantic import ValidationError as PydanticValidationError

from foundry_lite_api import runtime
from foundry_lite_api.builder_mcp_ui import (
    BUILDER_CONFIRMATION_TOOL,
    builder_resource_descriptors,
    decorate_builder_tool_list,
    read_builder_resource,
    validate_resources_list_params,
    validate_widget_approval_arguments,
)
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
    validate_tools_list_params,
)
from foundry_lite_api.mcp_rate_limit import mcp_rate_limit_http_error, mcp_result_headers

router = APIRouter()


@router.post("/mcp/builder/{application_id}")
async def builder_mcp_post(application_id: str, request: Request) -> Response:
    ctx, payload = await _builder_mcp_admission(application_id, request)
    return _builder_mcp_post_response(application_id, request, ctx, payload)


async def _builder_mcp_admission(application_id: str, request: Request) -> tuple[RequestContext, JsonRpcEnvelope]:
    try:
        _require_origin(request)
        ctx = require_mcp_context(request, "builder", application_id)
        runtime.foundry.aip.consume_fde_mcp_endpoint_rate_limit(application_id, ctx=ctx)
        payload = await _json_body(request)
        require_mcp_protocol_version(request, is_initialization=payload.method == "initialize")
    except RateLimited as exc:
        raise mcp_rate_limit_http_error(exc, request) from exc
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc
    return ctx, payload


def _builder_mcp_post_response(
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
        return _builder_mcp_domain_failure(application_id, request, payload, session_id, rpc_id, exc)
    except Exception:
        return _builder_mcp_internal_failure(request, payload, session_id, rpc_id)
    return _builder_mcp_success(payload, session_id, rpc_id, result)


def _builder_mcp_domain_failure(
    application_id: str,
    request: Request,
    payload: JsonRpcEnvelope,
    session_id: str | None,
    rpc_id: object,
    exc: FoundryLiteError,
) -> Response:
    if isinstance(exc, PermissionDenied):
        raise mcp_permission_failure(exc, request, "builder", application_id) from exc
    if exc.details.get("resource") == "mcp_session":
        raise _handle_error(exc, request) from exc
    if payload.is_notification:
        return Response(status_code=400, headers=_session_response_headers(session_id))
    return JSONResponse(_rpc_error(rpc_id, exc, request), status_code=200)


def _builder_mcp_internal_failure(
    request: Request, payload: JsonRpcEnvelope, session_id: str | None, rpc_id: object
) -> Response:
    if payload.is_notification:
        return Response(status_code=500, headers=_session_response_headers(session_id))
    return JSONResponse(_internal_error(rpc_id, request), status_code=200)


def _builder_mcp_success(
    payload: JsonRpcEnvelope, session_id: str | None, rpc_id: object, result: Mapping[str, object]
) -> Response:
    if session_id is None:
        raise RuntimeError("Builder MCP session id was not resolved")
    if payload.is_notification:
        return Response(status_code=202, headers={"Mcp-Session-Id": session_id})
    return JSONResponse(
        {"jsonrpc": "2.0", "id": rpc_id, "result": result},
        headers=mcp_result_headers(result, {"Mcp-Session-Id": session_id}),
    )


@router.get("/mcp/builder/{application_id}")
async def builder_mcp_get(application_id: str, request: Request) -> StreamingResponse:
    try:
        _require_origin(request)
        require_mcp_protocol_version(request)
        ctx = require_mcp_context(request, "builder", application_id)
        session_id = _required_existing_session_id(request)
        runtime.foundry.aip.consume_fde_mcp_endpoint_rate_limit(application_id, ctx=ctx)
        lease = runtime.foundry.aip.claim_fde_mcp_session_stream(application_id, session_id, ctx=ctx)
        try:
            events = runtime.foundry.aip.fde_mcp_session_events(
                application_id,
                session_id,
                after_sequence=_last_event_sequence(request, session_id),
                ctx=ctx,
            )
        except Exception:
            runtime.foundry.aip.release_fde_mcp_session_stream(application_id, session_id, lease.lease_id, ctx=ctx)
            raise
    except RateLimited as exc:
        raise mcp_rate_limit_http_error(exc, request) from exc
    except FoundryLiteError as exc:
        if isinstance(exc, PermissionDenied):
            raise mcp_permission_failure(exc, request, "builder", application_id) from exc
        raise _handle_error(exc, request) from exc
    try:
        response = StreamingResponse(
            _session_events(events, session_id, ctx, application_id, lease.lease_id),
            media_type="text/event-stream",
            headers={"Mcp-Session-Id": session_id, "Cache-Control": "no-cache"},
        )
    except Exception:
        runtime.foundry.aip.release_fde_mcp_session_stream(application_id, session_id, lease.lease_id, ctx=ctx)
        raise
    return response


@router.delete("/mcp/builder/{application_id}")
def builder_mcp_delete(application_id: str, request: Request) -> Response:
    try:
        _require_origin(request)
        require_mcp_protocol_version(request)
        ctx = require_mcp_context(request, "builder", application_id)
        session_id = _required_existing_session_id(request)
        runtime.foundry.aip.consume_fde_mcp_endpoint_rate_limit(application_id, ctx=ctx)
        runtime.foundry.aip.close_fde_mcp_session(
            application_id,
            session_id,
            ctx=ctx,
        )
    except RateLimited as exc:
        raise mcp_rate_limit_http_error(exc, request) from exc
    except FoundryLiteError as exc:
        if isinstance(exc, PermissionDenied):
            raise mcp_permission_failure(exc, request, "builder", application_id) from exc
        raise _handle_error(exc, request) from exc
    return Response(status_code=204)


@router.get("/.well-known/oauth-protected-resource/mcp/builder/{application_id}")
def builder_mcp_protected_resource(application_id: str, request: Request) -> dict[str, object]:
    try:
        return protected_resource_metadata(request, "builder", application_id)
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


def _dispatch(
    application_id: str,
    session_id: str,
    request: Request,
    payload: JsonRpcEnvelope,
    negotiated_protocol: str | None,
    ctx: RequestContext,
) -> Mapping[str, object]:
    if payload.method == "initialize":
        return _initialize_session(application_id, session_id, negotiated_protocol, ctx)
    _require_active_session(application_id, session_id, ctx)
    return _dispatch_active_session(application_id, session_id, request, payload, ctx)


def _initialize_session(
    application_id: str,
    session_id: str,
    negotiated_protocol: str | None,
    ctx: RequestContext,
) -> Mapping[str, object]:
    if negotiated_protocol is None:
        raise ValidationFailed("Builder MCP protocol negotiation is missing")
    runtime.foundry.aip.open_fde_mcp_session(application_id, session_id=session_id, ctx=ctx)
    return _initialize_result(negotiated_protocol)


def _dispatch_active_session(
    application_id: str,
    session_id: str,
    request: Request,
    payload: JsonRpcEnvelope,
    ctx: RequestContext,
) -> Mapping[str, object]:
    method = payload.method
    if method == "notifications/initialized":
        return {}
    if method == "ping":
        return {}
    if method == "tools/list":
        return _list_tools(application_id, session_id, payload, ctx)
    if method == "tools/call":
        return _call_tool(application_id, session_id, request, payload, ctx)
    if method == "resources/list":
        validate_resources_list_params(payload.params)
        return {"resources": builder_resource_descriptors()}
    if method == "resources/read":
        return read_builder_resource(payload.params)
    raise method_not_found("Builder", method)


def _list_tools(
    application_id: str,
    session_id: str,
    payload: JsonRpcEnvelope,
    ctx: RequestContext,
) -> Mapping[str, object]:
    params = payload.params
    validate_tools_list_params(params, is_builder=True)
    discovery_mode = _optional_text(params.get("discoveryMode")) or "eager"
    if discovery_mode == "lazy":
        runtime.foundry.aip.activate_fde_mcp_lazy_discovery(application_id, session_id, ctx=ctx)
    listed = runtime.foundry.aip.fde_mcp_tools(
        application_id,
        session_id=session_id,
        discovery_mode=discovery_mode,
        ctx=ctx,
    )
    return decorate_builder_tool_list(listed)


def _require_active_session(application_id: str, session_id: str, ctx: RequestContext) -> None:
    runtime.foundry.aip.fde_mcp_session_events(
        application_id,
        session_id,
        after_sequence=0,
        ctx=ctx,
    )


def _call_tool(
    application_id: str,
    session_id: str,
    request: Request,
    payload: JsonRpcEnvelope,
    ctx: RequestContext,
) -> Mapping[str, object]:
    params = payload.params
    arguments = _mapping(params.get("arguments"), "params.arguments")
    tool_name = _text(params, "name")
    if tool_name == BUILDER_CONFIRMATION_TOOL:
        challenge_id, widget_token = validate_widget_approval_arguments(arguments)
        return runtime.foundry.aip.approve_fde_mcp_widget_confirmation(
            application_id,
            session_id,
            challenge_id,
            widget_token,
            request.headers.get("origin"),
            ctx=ctx,
        )
    tool_arguments = _mapping(arguments.get("arguments"), "params.arguments.arguments")
    rpc_id = payload.id
    if not isinstance(rpc_id, str | int):
        raise ValidationFailed("Builder MCP tools/call requires a JSON-RPC id")
    call = FdeMcpToolCall(
        application_id=application_id,
        session_id=session_id,
        json_rpc_id=rpc_id,
        mode=_text(arguments, "mode"),
        workspace_ref=_text(arguments, "workspaceRef"),
        tool_id=tool_name,
        arguments=tool_arguments,
        confirmation_receipt=_optional_text(arguments.get("confirmationReceipt")),
        raw_input=arguments,
        origin=request.headers.get("origin"),
    )
    return runtime.foundry.aip.run_fde_mcp_tool(call, ctx=ctx)


def _initialize_result(protocol_version: str) -> dict[str, object]:
    return {
        "protocolVersion": protocol_version,
        "capabilities": {
            "tools": {"listChanged": True},
            "resources": {"subscribe": False, "listChanged": False},
        },
        "serverInfo": {"name": "foundry-lite-builder-mcp", "version": "1.0.0"},
        "instructions": (
            "When a user describes any business domain in natural language, infer a bounded domainBrief and call "
            "pilot.application.plan in osdk_react mode. Do not ask the user for API names or developer vocabulary. "
            "If its readiness questions are non-empty, ask only those concrete business questions and plan again. "
            "The embedded Domain OS Studio lets the user review the resulting people, records, states, rules, actions, "
            "and evidence, then explicitly create a test application. Use lazy discovery with search_tools or eager "
            "tools/list fallback and explicit workspaceRef values. "
            "Mutations return an approval challenge rendered by the embedded Builder confirmation app; "
            "the user can approve and retry the exact call inside ChatGPT. The authenticated human "
            "control-plane endpoint remains available for non-App clients. "
            "Approval, merge, deploy, and activation tools are never exposed."
        ),
    }


async def _session_events(
    events: list[Mapping[str, object]],
    session_id: str,
    ctx: RequestContext,
    application_id: str,
    lease_id: str,
) -> AsyncIterator[str]:
    try:
        for event in events:
            sequence = event.get("sequence")
            if not isinstance(sequence, int):
                raise ValidationFailed("Builder MCP event sequence is invalid")
            method = str(event.get("event_type", ""))
            params = event.get("payload_json")
            if not method.startswith("notifications/") or not isinstance(params, Mapping):
                raise ValidationFailed("Builder MCP session event is invalid")
            data = json.dumps({"jsonrpc": "2.0", "method": method, "params": dict(params)}, sort_keys=True)
            yield f"id: {session_id}:{sequence}\nevent: message\ndata: {data}\n\n"
        yield ": heartbeat\n\n"
    finally:
        runtime.foundry.aip.release_fde_mcp_session_stream(application_id, session_id, lease_id, ctx=ctx)


async def _json_body(request: Request) -> JsonRpcEnvelope:
    """Parse the fixed part of the protocol into a model; `params` stays for the tool to validate."""
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValidationFailed("Builder MCP request body must be JSON") from exc
    try:
        return JsonRpcEnvelope.model_validate(payload)
    except PydanticValidationError as exc:
        raise ValidationFailed(
            "Builder MCP requires a JSON-RPC 2.0 request",
            details={"errorCount": exc.error_count()},
        ) from exc


def _require_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is not None and origin not in runtime.ALLOWED_BROWSER_ORIGINS:
        raise ValidationFailed("Builder MCP Origin is not allowed")


def _request_session_id(request: Request, payload: JsonRpcEnvelope) -> str:
    if payload.method != "initialize":
        return _required_existing_session_id(request)
    reject_initialize_session_id(request)
    return f"mcp-{uuid4().hex}"


def _required_existing_session_id(request: Request) -> str:
    session_id = require_mcp_session_id(request, "Builder")
    require_mcp_session_namespace(session_id, "builder")
    return session_id


def _last_event_sequence(request: Request, session_id: str) -> int:
    last_event_id = request.headers.get("Last-Event-ID")
    if not last_event_id:
        return 0
    prefix = f"{session_id}:"
    if not last_event_id.startswith(prefix):
        raise ValidationFailed("Builder MCP Last-Event-ID does not belong to this session")
    try:
        sequence = int(last_event_id.removeprefix(prefix))
    except ValueError as exc:
        raise ValidationFailed("Builder MCP Last-Event-ID sequence is invalid") from exc
    if sequence < 0:
        raise ValidationFailed("Builder MCP Last-Event-ID sequence is invalid")
    return sequence


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValidationFailed(f"Builder MCP {field} must be an object")
    return {str(name): item for name, item in value.items()}


def _text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValidationFailed(f"Builder MCP {key} is required")
    return item.strip()


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


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
