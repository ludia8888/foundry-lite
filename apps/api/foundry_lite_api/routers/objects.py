"""Object read, query, and subscription (SSE/WebSocket) routes."""

from __future__ import annotations

from collections.abc import Iterator
from typing import cast

from fastapi import APIRouter, Query, Request, WebSocket
from fastapi.responses import StreamingResponse
from foundry_lite.application.ports import ObjectLinkPayload, ObjectPayload, ObjectQueryResult
from foundry_lite.domain.errors import FoundryLiteError
from pydantic import ValidationError

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error, _websocket_error
from foundry_lite_api.request_context import (
    _check_websocket_subscription_rate,
    _ctx,
    _websocket_ctx,
    _websocket_origin_allowed,
)
from foundry_lite_api.schemas import JsonObject, ObjectQueryRequest, ObjectSubscriptionRequest
from foundry_lite_api.serializers import _sse_json_events, _with_first_event

router = APIRouter()


@router.get("/api/objects/{object_type}/{object_id}")
def get_object(
    request: Request,
    object_type: str,
    object_id: str,
    include_explain: bool = Query(default=False, alias="explain"),
) -> ObjectPayload:
    try:
        return runtime.foundry.objects.get(object_type, object_id, ctx=_ctx(request), include_explain=include_explain)
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/objects/{object_type}/{object_id}/links/{link_type}")
def get_object_links(request: Request, object_type: str, object_id: str, link_type: str) -> list[ObjectLinkPayload]:
    try:
        return runtime.foundry.objects.links(object_type, object_id, link_type, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/objects/{object_type}/query")
def query_objects(request: Request, object_type: str, payload: ObjectQueryRequest) -> ObjectQueryResult:
    try:
        return runtime.foundry.objects.query(
            object_type,
            ctx=_ctx(request),
            filter_ast=payload.filter_ast,
            order_by=payload.order_by,
            limit=payload.limit,
            cursor=payload.cursor,
            search_text=payload.search_text,
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/objects/{object_type}/subscriptions/stream")
def stream_object_subscription(
    request: Request,
    object_type: str,
    payload: ObjectSubscriptionRequest,
) -> StreamingResponse:
    try:
        events = runtime.foundry.objects.subscription_events(
            object_type,
            ctx=_ctx(request),
            filter_ast=payload.filter_ast,
            order_by=payload.order_by,
            properties=payload.properties,
            page_size=payload.page_size,
            last_seen_object_change_sequence=payload.last_seen_object_change_sequence,
            max_events=payload.max_events,
            poll_interval_seconds=payload.poll_interval_seconds,
        )
        first = cast(JsonObject, next(events))
        return StreamingResponse(
            _sse_json_events(_with_first_event(first, cast(Iterator[JsonObject], events))),
            media_type="text/event-stream",
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.websocket("/api/objects/{object_type}/subscriptions/ws")
async def websocket_object_subscription(websocket: WebSocket, object_type: str) -> None:
    if not _websocket_origin_allowed(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    ctx = _websocket_ctx(websocket)
    try:
        _check_websocket_subscription_rate(ctx, object_type)
        payload = await websocket.receive_json()
        request = ObjectSubscriptionRequest.model_validate(payload or {})
        events = runtime.foundry.objects.subscription_events(
            object_type,
            ctx=ctx,
            filter_ast=request.filter_ast,
            order_by=request.order_by,
            properties=request.properties,
            page_size=request.page_size,
            last_seen_object_change_sequence=request.last_seen_object_change_sequence,
            max_events=request.max_events,
            poll_interval_seconds=request.poll_interval_seconds,
        )
        for event in events:
            await websocket.send_json(cast(JsonObject, event))
    except FoundryLiteError as exc:
        await websocket.send_json({"event": "error", "error": _websocket_error(exc, ctx.request_id)})
        await websocket.close(code=1008)
    except ValidationError as exc:
        await websocket.send_json({"event": "error", "error": {"code": "VALIDATION_ERROR", "message": str(exc)}})
        await websocket.close(code=1003)
