"""Object read, query, and subscription (SSE/WebSocket) routes."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import cast

import anyio.to_thread
from fastapi import APIRouter, Query, Request, WebSocket
from fastapi.responses import StreamingResponse
from foundry_lite.application.ports import (
    ObjectAggregationResult,
    ObjectLinkPayload,
    ObjectPayload,
    ObjectQueryResult,
)
from foundry_lite.domain.errors import FoundryLiteError, RateLimited
from pydantic import ValidationError

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error, _websocket_error
from foundry_lite_api.request_context import (
    _check_websocket_subscription_rate,
    _ctx,
    _websocket_ctx,
    _websocket_origin_allowed,
)
from foundry_lite_api.schemas import (
    InterfaceQueryRequest,
    JsonObject,
    ObjectAggregateRequest,
    ObjectQueryRequest,
    ObjectSubscriptionRequest,
)
from foundry_lite_api.serializers import _sse_json_events, _with_first_event

router = APIRouter()

# Per-process ceiling on simultaneous subscription streams per tenant. In-process
# only (mirrors the per-process rate limiter in runtime.py); a multi-worker
# deployment needs a shared counter to enforce this globally.
MAX_CONCURRENT_SUBSCRIPTIONS_PER_TENANT = 50

_STREAM_EXHAUSTED = object()


class _SubscriptionConcurrencyGuard:
    """Counts live subscription streams per tenant and caps the fan-out."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def acquire(self, tenant_id: str, *, limit: int) -> None:
        with self._lock:
            current = self._counts.get(tenant_id, 0)
            if current >= limit:
                raise RateLimited(
                    "Too many concurrent object subscription streams for this tenant",
                    details={"activeConnections": current, "maxConnections": limit},
                )
            self._counts[tenant_id] = current + 1

    def release(self, tenant_id: str) -> None:
        with self._lock:
            remaining = self._counts.get(tenant_id, 0) - 1
            if remaining > 0:
                self._counts[tenant_id] = remaining
            else:
                self._counts.pop(tenant_id, None)

    def active_count(self, tenant_id: str) -> int:
        with self._lock:
            return self._counts.get(tenant_id, 0)


_subscription_connections = _SubscriptionConcurrencyGuard()


def _next_subscription_event(events: Iterator[JsonObject]) -> object:
    try:
        return next(events)
    except StopIteration:
        return _STREAM_EXHAUSTED


async def _stream_subscription_events(websocket: WebSocket, events: Iterator[JsonObject]) -> None:
    # The subscription generator is synchronous and polls with a blocking sleep.
    # Driving next() directly on the coroutine would stall the event loop for
    # every other connection, so each step is offloaded to a worker thread
    # (mirroring how StreamingResponse already runs the SSE generator).
    while True:
        event = await anyio.to_thread.run_sync(_next_subscription_event, events)
        if event is _STREAM_EXHAUSTED:
            return
        await websocket.send_json(cast(JsonObject, event))


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


@router.post("/api/interfaces/{interface_type}/query")
def query_interface_objects(
    request: Request,
    interface_type: str,
    payload: InterfaceQueryRequest,
) -> ObjectQueryResult:
    """Union query across every object type implementing the interface."""
    try:
        return runtime.foundry.objects.query_by_interface(
            interface_type,
            ctx=_ctx(request),
            filter_ast=payload.filter_ast,
            order_by=payload.order_by,
            limit=payload.limit,
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/objects/{object_type}/aggregate")
def aggregate_objects(request: Request, object_type: str, payload: ObjectAggregateRequest) -> ObjectAggregationResult:
    try:
        return runtime.foundry.objects.aggregate(
            object_type,
            ctx=_ctx(request),
            filter_ast=payload.filter_ast,
            group_by=payload.group_by,
            select=[metric.model_dump() for metric in payload.select],
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/objects/{object_type}/subscriptions/stream")
def stream_object_subscription(
    request: Request,
    object_type: str,
    payload: ObjectSubscriptionRequest,
) -> StreamingResponse:
    ctx = _ctx(request)
    try:
        events = runtime.foundry.objects.subscription_events(
            object_type,
            ctx=ctx,
            filter_ast=payload.filter_ast,
            order_by=payload.order_by,
            properties=payload.properties,
            page_size=payload.page_size,
            last_seen_object_change_sequence=payload.last_seen_object_change_sequence,
            max_events=payload.max_events,
            poll_interval_seconds=payload.poll_interval_seconds,
        )
        _subscription_connections.acquire(ctx.tenant_id, limit=MAX_CONCURRENT_SUBSCRIPTIONS_PER_TENANT)
        try:
            first = cast(JsonObject, next(events))
        except BaseException:
            _subscription_connections.release(ctx.tenant_id)
            raise
        guarded = _release_after(ctx.tenant_id, _with_first_event(first, cast(Iterator[JsonObject], events)))
        return StreamingResponse(_sse_json_events(guarded), media_type="text/event-stream")
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


def _release_after(tenant_id: str, events: Iterator[JsonObject]) -> Iterator[JsonObject]:
    try:
        yield from events
    finally:
        _subscription_connections.release(tenant_id)


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
        _subscription_connections.acquire(ctx.tenant_id, limit=MAX_CONCURRENT_SUBSCRIPTIONS_PER_TENANT)
        try:
            await _stream_subscription_events(websocket, cast(Iterator[JsonObject], events))
        finally:
            _subscription_connections.release(ctx.tenant_id)
    except FoundryLiteError as exc:
        await websocket.send_json({"event": "error", "error": _websocket_error(exc, ctx.request_id)})
        await websocket.close(code=1008)
    except ValidationError:
        await websocket.send_json(
            {
                "event": "error",
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "object subscription request validation failed",
                    "request_id": ctx.request_id,
                },
            }
        )
        await websocket.close(code=1003)
