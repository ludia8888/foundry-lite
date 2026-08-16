"""Governed Release MCP Streamable HTTP transport and embedded app resource."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from foundry_lite.application.services.aip.governed_release_authorization import GOVERNED_RELEASE_SCOPE
from foundry_lite.application.services.aip.governed_release_mcp_types import GovernedReleaseMcpToolCall
from foundry_lite.application.services.mcp_session_namespace import require_mcp_session_namespace
from foundry_lite.application.services.mcp_tool_results import serialized_text_content
from foundry_lite.application.services.runtime_error_payloads import scrub_error_mapping, scrub_error_text
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import FoundryLiteError, NotFound, PermissionDenied, RateLimited, ValidationFailed
from pydantic import ValidationError as PydanticValidationError

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.mcp_authorization import (
    mcp_permission_failure,
    mcp_tool_authentication_result,
    protected_resource_metadata,
    require_mcp_context,
)
from foundry_lite_api.mcp_envelope import JsonRpcEnvelope
from foundry_lite_api.mcp_internal_error_observability import log_mcp_internal_failure
from foundry_lite_api.mcp_protocol import (
    McpInvalidRequest,
    McpMethodNotFound,
    mcp_last_event_sequence,
    method_not_found,
    reject_initialize_session_id,
    require_mcp_protocol_version,
    require_mcp_session_id,
    validate_mcp_message,
    validate_tools_list_params,
)
from foundry_lite_api.mcp_rate_limit import mcp_rate_limit_http_error, mcp_result_headers

router = APIRouter()
_LOGGER = logging.getLogger(__name__)

RELEASE_CONSOLE_RESOURCE_URI = "ui://foundry-lite/governed-release-v9-87ac4aeadd8c.html"
LEGACY_RELEASE_CONSOLE_RESOURCE_URIS = frozenset(
    {
        "ui://foundry-lite/governed-release-v2.html",
        "ui://foundry-lite/governed-release-v3.html",
        "ui://foundry-lite/governed-release-v4.html",
        "ui://foundry-lite/governed-release-v5-25a98896119d.html",
        "ui://foundry-lite/governed-release-v6-f2bef02fe8ee.html",
        "ui://foundry-lite/governed-release-v7-dcca665d29a3.html",
        "ui://foundry-lite/governed-release-v8-04c14f7f069c.html",
    }
)
RELEASE_CONSOLE_MIME_TYPE = "text/html;profile=mcp-app"
_RELEASE_CONSOLE_PATH = Path(__file__).resolve().parents[4] / "apps" / "chatgpt-release-widget" / "index.html"


@router.post("/mcp/release/{application_id}")
async def release_mcp_post(application_id: str, request: Request) -> Response:
    ctx, payload = await _release_mcp_admission(application_id, request)
    return _release_mcp_post_response(application_id, request, ctx, payload)


async def _release_mcp_admission(application_id: str, request: Request) -> tuple[RequestContext, JsonRpcEnvelope]:
    try:
        _require_origin(request)
        ctx = require_mcp_context(request, "release", application_id)
        runtime.foundry.release.consume_release_mcp_endpoint_rate_limit(application_id, ctx=ctx)
        payload = await _json_body(request)
        require_mcp_protocol_version(request, is_initialization=payload.method == "initialize")
    except RateLimited as exc:
        raise mcp_rate_limit_http_error(exc, request) from exc
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc
    return ctx, payload


def _release_mcp_post_response(
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
        return _release_mcp_domain_failure(application_id, request, payload, session_id, rpc_id, exc)
    except Exception as exc:
        log_mcp_internal_failure(_LOGGER, request, plane="release", rpc_method=payload.method, exc=exc)
        return _release_mcp_internal_failure(request, payload, session_id, rpc_id)
    return _release_mcp_success(payload, session_id, rpc_id, result)


def _release_mcp_domain_failure(
    application_id: str,
    request: Request,
    payload: JsonRpcEnvelope,
    session_id: str | None,
    rpc_id: object,
    exc: FoundryLiteError,
) -> Response:
    if isinstance(exc, PermissionDenied):
        raise mcp_permission_failure(exc, request, "release", application_id) from exc
    if exc.details.get("resource") == "mcp_session":
        raise _handle_error(exc, request) from exc
    if payload.is_notification:
        return Response(status_code=400, headers=_session_response_headers(session_id))
    return JSONResponse(_rpc_error(rpc_id, exc, request), status_code=200)


def _release_mcp_internal_failure(
    request: Request,
    payload: JsonRpcEnvelope,
    session_id: str | None,
    rpc_id: object,
) -> Response:
    if payload.is_notification:
        return Response(status_code=500, headers=_session_response_headers(session_id))
    return JSONResponse(_internal_error(rpc_id, request), status_code=200)


def _release_mcp_success(
    payload: JsonRpcEnvelope,
    session_id: str | None,
    rpc_id: object,
    result: Mapping[str, object],
) -> Response:
    if session_id is None:
        raise RuntimeError("Release MCP session id was not resolved")
    if payload.is_notification:
        return Response(status_code=202, headers={"Mcp-Session-Id": session_id})
    return JSONResponse(
        {"jsonrpc": "2.0", "id": rpc_id, "result": result},
        headers=mcp_result_headers(result, {"Mcp-Session-Id": session_id}),
    )


@router.get("/mcp/release/{application_id}")
async def release_mcp_get(application_id: str, request: Request) -> StreamingResponse:
    try:
        _require_origin(request)
        require_mcp_protocol_version(request)
        ctx = require_mcp_context(request, "release", application_id)
        session_id = _required_existing_session_id(request)
        runtime.foundry.release.consume_release_mcp_endpoint_rate_limit(application_id, ctx=ctx)
        lease = runtime.foundry.release.claim_release_mcp_session_stream(application_id, session_id, ctx=ctx)
        try:
            events = runtime.foundry.release.release_mcp_session_events(
                application_id,
                session_id,
                after_sequence=mcp_last_event_sequence(request, session_id, "Release"),
                ctx=ctx,
            )
        except Exception:
            runtime.foundry.release.release_release_mcp_session_stream(
                application_id,
                session_id,
                lease.lease_id,
                ctx=ctx,
            )
            raise
    except RateLimited as exc:
        raise mcp_rate_limit_http_error(exc, request) from exc
    except FoundryLiteError as exc:
        if isinstance(exc, PermissionDenied):
            raise mcp_permission_failure(exc, request, "release", application_id) from exc
        raise _handle_error(exc, request) from exc
    try:
        response = StreamingResponse(
            _session_events(events, session_id, ctx, application_id, lease.lease_id),
            media_type="text/event-stream",
            headers={"Mcp-Session-Id": session_id, "Cache-Control": "no-cache"},
        )
    except Exception:
        runtime.foundry.release.release_release_mcp_session_stream(
            application_id,
            session_id,
            lease.lease_id,
            ctx=ctx,
        )
        raise
    return response


@router.delete("/mcp/release/{application_id}")
def release_mcp_delete(application_id: str, request: Request) -> Response:
    try:
        _require_origin(request)
        require_mcp_protocol_version(request)
        ctx = require_mcp_context(request, "release", application_id)
        session_id = _required_existing_session_id(request)
        runtime.foundry.release.consume_release_mcp_endpoint_rate_limit(application_id, ctx=ctx)
        runtime.foundry.release.close_release_mcp_session(application_id, session_id, ctx=ctx)
    except RateLimited as exc:
        raise mcp_rate_limit_http_error(exc, request) from exc
    except FoundryLiteError as exc:
        if isinstance(exc, PermissionDenied):
            raise mcp_permission_failure(exc, request, "release", application_id) from exc
        raise _handle_error(exc, request) from exc
    return Response(status_code=204)


@router.get("/.well-known/oauth-protected-resource/mcp/release/{application_id}")
def release_mcp_protected_resource(application_id: str, request: Request) -> dict[str, object]:
    try:
        return protected_resource_metadata(request, "release", application_id)
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/mcp/release/{application_id}/live-readiness")
def release_mcp_live_readiness(application_id: str, request: Request) -> JSONResponse:
    """Authenticated operator view; it never turns structural fixtures into live proof."""

    try:
        _require_origin(request)
        ctx = require_mcp_context(request, "release", application_id)
        runtime.foundry.release.consume_release_mcp_endpoint_rate_limit(application_id, ctx=ctx)
        runtime.foundry.release.release_mcp_tools(application_id, session_id=None, ctx=ctx)
        readiness = runtime.foundry.release.release_live_readiness(application_id, ctx=ctx)
    except RateLimited as exc:
        raise mcp_rate_limit_http_error(exc, request) from exc
    except FoundryLiteError as exc:
        if isinstance(exc, PermissionDenied):
            raise mcp_permission_failure(exc, request, "release", application_id) from exc
        raise _handle_error(exc, request) from exc
    return JSONResponse(readiness, headers={"Cache-Control": "no-store"})


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
            raise ValidationFailed("Release MCP protocol negotiation is missing")
        runtime.foundry.release.open_release_mcp_session(
            application_id,
            session_id=session_id,
            ctx=ctx,
        )
        return _initialize_result(negotiated_protocol)
    _require_active_session(application_id, session_id, ctx)
    if method == "notifications/initialized":
        return {}
    if method == "ping":
        return {}
    if method == "tools/list":
        validate_tools_list_params(payload.params)
        listed = runtime.foundry.release.release_mcp_tools(application_id, session_id=session_id, ctx=ctx)
        return _decorate_tool_list(listed)
    if method == "tools/call":
        return _call_tool(application_id, session_id, request, payload, ctx)
    if method == "resources/list":
        _validate_resources_list_params(payload.params)
        return {"resources": [_resource_descriptor()]}
    if method == "resources/read":
        return _read_resource(payload.params)
    raise method_not_found("Release", method)


def _require_active_session(application_id: str, session_id: str, ctx: RequestContext) -> None:
    runtime.foundry.release.release_mcp_session_events(application_id, session_id, after_sequence=0, ctx=ctx)


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
        raise ValidationFailed("Release MCP tools/call requires a JSON-RPC id")
    arguments = _mapping(params.get("arguments", {}), "params.arguments")
    widget_confirmation_token = _optional_text(arguments.pop("widgetConfirmationToken", None))
    call = GovernedReleaseMcpToolCall(
        application_id=application_id,
        session_id=session_id,
        json_rpc_id=rpc_id,
        tool_name=_text(params, "name"),
        arguments=arguments,
        widget_confirmation_token=widget_confirmation_token,
        origin=request.headers.get("origin"),
    )
    try:
        result = runtime.foundry.release.run_release_mcp_tool(call, ctx=ctx)
    except PermissionDenied as exc:
        return mcp_tool_authentication_result(exc, request, "release", application_id)
    return _decorate_status_readiness(result, application_id, call.tool_name, ctx)


def _decorate_status_readiness(
    result: Mapping[str, object],
    application_id: str,
    tool_name: str,
    ctx: RequestContext,
) -> Mapping[str, object]:
    if tool_name != "get_release_status" or result.get("isError") is True:
        return result
    structured = result.get("structuredContent")
    release = structured.get("release") if isinstance(structured, Mapping) else None
    if not isinstance(structured, Mapping) or not isinstance(release, Mapping):
        return result
    readiness = runtime.foundry.release.release_live_readiness(application_id, ctx=ctx)
    decorated_release = {**release, "liveReadiness": readiness}
    decorated_structured = {**structured, "release": decorated_release}
    return {
        **result,
        "structuredContent": decorated_structured,
        "content": serialized_text_content(decorated_structured),
    }


def _initialize_result(protocol_version: str) -> dict[str, object]:
    return {
        "protocolVersion": protocol_version,
        "capabilities": {
            "tools": {"listChanged": True},
            "resources": {"subscribe": False, "listChanged": False},
        },
        "serverInfo": {"name": "foundry-lite-governed-release-mcp", "version": "1.0.0"},
        "instructions": (
            "Review validation evidence before approving a release. Approval, execution, deployment, status, "
            "and rollback stay bound to the authenticated application and human identity."
        ),
    }


def _decorate_tool_list(result: Mapping[str, object]) -> dict[str, object]:
    tools = result.get("tools")
    if not isinstance(tools, Sequence) or isinstance(tools, str | bytes):
        raise ValidationFailed("Release MCP tool registry returned an invalid tools list")
    decorated = [_decorate_tool(tool) for tool in tools]
    return {**result, "tools": decorated}


def _decorate_tool(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValidationFailed("Release MCP tool registry returned an invalid tool descriptor")
    descriptor = {str(key): item for key, item in value.items()}
    raw_meta = descriptor.get("_meta", {})
    if not isinstance(raw_meta, Mapping):
        raise ValidationFailed("Release MCP tool descriptor _meta must be an object")
    raw_ui = raw_meta.get("ui", {})
    if not isinstance(raw_ui, Mapping):
        raise ValidationFailed("Release MCP tool descriptor _meta.ui must be an object")
    ui = {str(key): item for key, item in raw_ui.items()}
    ui.setdefault("resourceUri", RELEASE_CONSOLE_RESOURCE_URI)
    meta = {str(key): item for key, item in raw_meta.items()}
    meta["ui"] = ui
    meta.setdefault("openai/outputTemplate", RELEASE_CONSOLE_RESOURCE_URI)
    meta.setdefault("openai/widgetAccessible", True)
    meta["securitySchemes"] = _release_tool_security_schemes()
    descriptor["securitySchemes"] = _release_tool_security_schemes()
    descriptor["_meta"] = meta
    return descriptor


def _release_tool_security_schemes() -> list[dict[str, object]]:
    """Return the exact OAuth declaration enforced by this MCP plane."""

    return [{"type": "oauth2", "scopes": [GOVERNED_RELEASE_SCOPE]}]


def _resource_descriptor() -> dict[str, object]:
    return {
        "uri": RELEASE_CONSOLE_RESOURCE_URI,
        "name": "foundry-lite-governed-release-console",
        "title": "Governed Release Console",
        "description": "Review, approve, deploy, observe, and roll back a governed Foundry-lite release.",
        "mimeType": RELEASE_CONSOLE_MIME_TYPE,
        "_meta": _resource_meta(),
    }


def _read_resource(params: Mapping[str, object]) -> dict[str, object]:
    uri = _text(params, "uri")
    if uri != RELEASE_CONSOLE_RESOURCE_URI and uri not in LEGACY_RELEASE_CONSOLE_RESOURCE_URIS:
        raise NotFound("Governed Release MCP UI resource was not found", details={"uri": uri})
    if not _RELEASE_CONSOLE_PATH.is_file():
        raise NotFound(
            "Governed Release MCP UI asset is not installed",
            details={"uri": uri, "expectedAsset": "apps/chatgpt-release-widget/index.html"},
        )
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": RELEASE_CONSOLE_MIME_TYPE,
                "text": _RELEASE_CONSOLE_PATH.read_text(encoding="utf-8"),
                "_meta": _resource_meta(),
            }
        ]
    }


def _resource_meta() -> dict[str, object]:
    return {
        "ui": {
            "prefersBorder": True,
            "csp": {"connectDomains": [], "resourceDomains": []},
        },
        "openai/widgetDescription": (
            "A governed release console showing immutable evidence, approval state, deployment progress, "
            "and rollback controls."
        ),
        "openai/widgetPrefersBorder": True,
    }


def _validate_resources_list_params(params: Mapping[str, object]) -> None:
    cursor = params.get("cursor")
    if "cursor" in params:
        if not isinstance(cursor, str) or not cursor:
            raise ValidationFailed("MCP resources/list cursor must be a non-empty string")
        raise ValidationFailed("MCP resources/list pagination cursor is not supported")
    metadata = params.get("_meta")
    if "_meta" in params and not isinstance(metadata, Mapping):
        raise ValidationFailed("MCP resources/list _meta must be an object")


async def _session_events(
    events: Sequence[Mapping[str, object]],
    session_id: str,
    ctx: RequestContext,
    application_id: str,
    lease_id: str,
) -> AsyncIterator[str]:
    try:
        for event in events:
            sequence = event.get("sequence")
            if type(sequence) is not int or sequence < 1:
                raise ValidationFailed("Release MCP event sequence is invalid")
            method = str(event.get("event_type", ""))
            params = event.get("payload_json")
            if not method.startswith("notifications/") or not isinstance(params, Mapping):
                raise ValidationFailed("Release MCP session event is invalid")
            data = json.dumps({"jsonrpc": "2.0", "method": method, "params": dict(params)}, sort_keys=True)
            yield f"id: {session_id}:{sequence}\nevent: message\ndata: {data}\n\n"
        yield ": heartbeat\n\n"
    finally:
        runtime.foundry.release.release_release_mcp_session_stream(
            application_id,
            session_id,
            lease_id,
            ctx=ctx,
        )


async def _json_body(request: Request) -> JsonRpcEnvelope:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValidationFailed("Release MCP request body must be JSON") from exc
    try:
        return JsonRpcEnvelope.model_validate(payload)
    except PydanticValidationError as exc:
        raise ValidationFailed(
            "Release MCP requires a JSON-RPC 2.0 request",
            details={"errorCount": exc.error_count()},
        ) from exc


def _require_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is not None and origin not in runtime.ALLOWED_BROWSER_ORIGINS:
        raise ValidationFailed("Release MCP Origin is not allowed")


def _request_session_id(request: Request, payload: JsonRpcEnvelope) -> str:
    if payload.method != "initialize":
        return _required_existing_session_id(request)
    reject_initialize_session_id(request)
    return f"mcp-release-{uuid4().hex}"


def _required_existing_session_id(request: Request) -> str:
    session_id = require_mcp_session_id(request, "Release")
    require_mcp_session_namespace(session_id, "release")
    return session_id


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValidationFailed(f"Release MCP {field} must be an object")
    return {str(name): item for name, item in value.items()}


def _text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValidationFailed(f"Release MCP {key} is required")
    return item.strip()


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _rpc_error(rpc_id: object, exc: FoundryLiteError, request: Request) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {
            "code": _rpc_error_code(exc),
            "message": scrub_error_text(exc.message),
            "data": scrub_error_mapping({"type": exc.code, **exc.details, "requestId": _request_id(request)}),
        },
    }


def _rpc_error_code(exc: FoundryLiteError) -> int:
    if isinstance(exc, McpInvalidRequest):
        return -32600
    if isinstance(exc, McpMethodNotFound):
        return -32601
    if isinstance(exc, NotFound):
        return -32002
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
