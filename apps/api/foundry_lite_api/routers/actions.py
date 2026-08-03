"""Action apply/validate routes."""

from __future__ import annotations

import asyncio
import functools
import json
from collections.abc import AsyncIterator

import anyio.to_thread
from fastapi import APIRouter, Header, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from foundry_lite.application.action_types import (
    ActionApplyResponse,
    ActionBatchApplyResponse,
    ActionCatalogItem,
    ActionCatalogPage,
    ActionExecutionPlanResponse,
    ActionValidationResponse,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import FoundryLiteError

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.request_context import _ctx
from foundry_lite_api.schemas import ActionApplyBatchRequest, ActionApplyRequest, ActionRunCancelRequest

router = APIRouter()


@router.get("/api/actions")
def list_actions(
    request: Request,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> ActionCatalogPage:
    try:
        return runtime.foundry.actions.list(cursor=cursor, limit=limit, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/actions/runs")
def list_action_runs(
    request: Request,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, object]:
    try:
        return runtime.foundry.actions.list_runs(cursor=cursor, limit=limit, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/actions/runs/{run_id}")
def get_action_run(request: Request, run_id: str) -> dict[str, object]:
    try:
        return runtime.foundry.actions.get_run(run_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/actions/runs/{run_id}/events")
async def stream_action_run_events(
    request: Request,
    run_id: str,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    return StreamingResponse(
        _action_event_stream(request, run_id, _event_sequence(last_event_id), _ctx(request)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/actions/runs/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
def cancel_action_run(
    request: Request,
    run_id: str,
    payload: ActionRunCancelRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> dict[str, object]:
    try:
        return runtime.foundry.actions.cancel(
            run_id, idempotency_key=idempotency_key, reason=payload.reason, ctx=_ctx(request)
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/actions/logs")
def list_action_logs(
    request: Request,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, object]:
    try:
        return runtime.foundry.actions.logs(cursor=cursor, limit=limit, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/actions/runs/{run_id}/revert-eligibility")
def action_revert_eligibility(request: Request, run_id: str) -> dict[str, object]:
    try:
        return runtime.foundry.actions.revert_eligibility(run_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/actions/runs/{run_id}/revert")
def revert_action_run(
    request: Request,
    run_id: str,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> dict[str, object]:
    try:
        return runtime.foundry.actions.revert(run_id, idempotency_key=idempotency_key, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/actions/{action_type}/schema")
def action_schema(request: Request, action_type: str) -> dict[str, object]:
    try:
        return runtime.foundry.actions.schema(action_type, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/actions/{action_type}")
def get_action(request: Request, action_type: str) -> ActionCatalogItem:
    try:
        return runtime.foundry.actions.get(action_type, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/actions/{action_type}/apply")
def apply_action(
    request: Request,
    action_type: str,
    payload: ActionApplyRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> ActionApplyResponse:
    try:
        return runtime.foundry.actions.apply(
            action_type,
            object_type=payload.target.object_type,
            object_id=payload.target.object_id,
            expected_object_version=payload.expected_object_version,
            params=payload.params,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/actions/{action_type}/runs", status_code=status.HTTP_202_ACCEPTED)
def start_action_run(
    request: Request,
    response: Response,
    action_type: str,
    payload: ActionApplyRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    wait_seconds: int = Query(default=0, ge=0, le=30, alias="waitSeconds"),
) -> dict[str, object]:
    try:
        snapshot = runtime.foundry.actions.start_run(
            action_type,
            object_type=payload.target.object_type,
            object_id=payload.target.object_id,
            expected_object_version=payload.expected_object_version,
            params=payload.params,
            idempotency_key=idempotency_key,
            wait_seconds=wait_seconds,
            ctx=_ctx(request),
        )
        if snapshot["status"] in {"succeeded", "failed", "cancelled", "conflict", "outcome_unknown"}:
            response.status_code = status.HTTP_200_OK
        return snapshot
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/actions/{action_type}/plan")
def plan_action(
    request: Request,
    action_type: str,
    payload: ActionApplyRequest,
) -> ActionExecutionPlanResponse:
    try:
        return runtime.foundry.actions.plan(
            action_type,
            object_type=payload.target.object_type,
            object_id=payload.target.object_id,
            expected_object_version=payload.expected_object_version,
            params=payload.params,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/actions/{action_type}/dry-run")
def dry_run_action(
    request: Request,
    action_type: str,
    payload: ActionApplyRequest,
) -> ActionExecutionPlanResponse:
    try:
        return runtime.foundry.actions.dry_run(
            action_type,
            object_type=payload.target.object_type,
            object_id=payload.target.object_id,
            expected_object_version=payload.expected_object_version,
            params=payload.params,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/actions/{action_type}/apply-batch")
def apply_action_batch(
    request: Request,
    action_type: str,
    payload: ActionApplyBatchRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> ActionBatchApplyResponse:
    try:
        return runtime.foundry.actions.apply_batch(
            action_type,
            object_type=payload.object_type,
            targets=[
                {"object_id": target.object_id, "expected_object_version": target.expected_object_version}
                for target in payload.targets
            ],
            params=payload.params,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/actions/{action_type}/validate")
def validate_action(
    request: Request,
    action_type: str,
    payload: ActionApplyRequest,
) -> ActionValidationResponse:
    try:
        return runtime.foundry.actions.validate(
            action_type,
            object_type=payload.target.object_type,
            object_id=payload.target.object_id,
            expected_object_version=payload.expected_object_version,
            params=payload.params,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


def _event_sequence(value: str | None) -> int:
    if value is None:
        return 0
    try:
        return max(0, int(value))
    except ValueError:
        return 0


async def _action_event_stream(
    request: Request,
    run_id: str,
    after_sequence: int,
    ctx: RequestContext,
) -> AsyncIterator[str]:
    cursor = after_sequence
    heartbeat_count = 0
    while not await request.is_disconnected():
        page = await anyio.to_thread.run_sync(
            functools.partial(runtime.foundry.actions.events, run_id, after_sequence=cursor, limit=100, ctx=ctx)
        )
        events = page["events"]
        if isinstance(events, list) and events:
            for event in events:
                if isinstance(event, dict):
                    cursor = int(event["id"])
                    yield _sse_event(event)
            heartbeat_count = 0
            continue
        snapshot = await anyio.to_thread.run_sync(functools.partial(runtime.foundry.actions.get_run, run_id, ctx=ctx))
        if snapshot["status"] in {"succeeded", "failed", "cancelled", "conflict", "outcome_unknown"}:
            return
        heartbeat_count += 1
        if heartbeat_count >= 15:
            yield ": heartbeat\n\n"
            heartbeat_count = 0
        await asyncio.sleep(1)


def _sse_event(event: dict[str, object]) -> str:
    data = json.dumps(event["data"], separators=(",", ":"), ensure_ascii=False)
    return f"id: {event['id']}\nevent: {event['event']}\ndata: {data}\n\n"
