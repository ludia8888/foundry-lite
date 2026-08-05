"""Action apply/validate routes."""

from __future__ import annotations

import asyncio
import functools
import json
import os
from collections.abc import AsyncIterator

import anyio.to_thread
from fastapi import APIRouter, Form, Header, Query, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from foundry_lite.application.action_types import (
    ActionApplyResponse,
    ActionBatchApplyResponse,
    ActionCatalogItem,
    ActionCatalogPage,
    ActionExecutionPlanResponse,
    ActionMediaUploadResult,
    ActionValidationResponse,
)
from foundry_lite.application.upload_limits import max_media_upload_bytes
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import FoundryLiteError, ValidationFailed

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.request_context import _ctx
from foundry_lite_api.schemas import (
    MEDIA_UPLOAD_FILE,
    ActionApplyBatchRequest,
    ActionApplyRequest,
    ActionEffectCancelRequest,
    ActionEffectReconcileRequest,
    ActionFunctionBatchRunRequest,
    ActionNotificationPolicyCreateRequest,
    ActionNotificationPolicyDisableRequest,
    ActionNotificationPolicyUpdateRequest,
    ActionRunCancelRequest,
)

router = APIRouter()


@router.get("/api/actions/effects")
def list_action_effects(
    request: Request,
    effect_status: str | None = Query(default=None, alias="status"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, object]:
    try:
        return runtime.foundry.actions.list_effect_receipts(
            status=effect_status,
            cursor=cursor,
            limit=limit,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/actions/effects/{receipt_id}")
def get_action_effect(request: Request, receipt_id: str) -> dict[str, object]:
    try:
        return runtime.foundry.actions.get_effect_receipt(receipt_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/actions/effects/{receipt_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
def cancel_action_effect(
    request: Request,
    receipt_id: str,
    payload: ActionEffectCancelRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> dict[str, object]:
    try:
        return runtime.foundry.actions.cancel_effect(
            receipt_id,
            reason=payload.reason,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/actions/effects/{receipt_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_action_effect(
    request: Request,
    receipt_id: str,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> dict[str, object]:
    try:
        return runtime.foundry.actions.retry_effect(
            receipt_id,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/actions/effects/{receipt_id}/reconcile")
def reconcile_action_effect(
    request: Request,
    receipt_id: str,
    payload: ActionEffectReconcileRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> dict[str, object]:
    try:
        return runtime.foundry.actions.reconcile_effect(
            receipt_id,
            resolution=payload.resolution,
            evidence=payload.evidence.model_dump(by_alias=True, exclude_none=True),
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


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


@router.get("/api/actions/branches/{branch_id}/diff")
def action_branch_diff(request: Request, branch_id: str) -> dict[str, object]:
    try:
        return runtime.foundry.actions.branch_diff(branch_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/actions/branches/{branch_id}/objects/{object_type}/{object_id}")
def action_branch_object(request: Request, branch_id: str, object_type: str, object_id: str) -> dict[str, object]:
    try:
        return runtime.foundry.actions.branch_object(branch_id, object_type, object_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/actions/branches/{branch_id}/links/{link_type}/{from_object_id}/{to_object_id}")
def action_branch_link(
    request: Request,
    branch_id: str,
    link_type: str,
    from_object_id: str,
    to_object_id: str,
) -> dict[str, object]:
    try:
        return runtime.foundry.actions.branch_link(
            branch_id, link_type, from_object_id, to_object_id, ctx=_ctx(request)
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/actions/notification-policies")
def list_action_notification_policies(
    request: Request,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, object]:
    try:
        return runtime.foundry.actions.list_notification_policies(cursor=cursor, limit=limit, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/actions/notification-policies", status_code=status.HTTP_201_CREATED)
def create_action_notification_policy(
    request: Request,
    payload: ActionNotificationPolicyCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> dict[str, object]:
    try:
        return runtime.foundry.actions.create_notification_policy(
            payload.policy_name,
            display_name=payload.display_name,
            delivery_mode=payload.delivery_mode,
            recipients=[item.model_dump(by_alias=True) for item in payload.recipients],
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/actions/notification-policies/{policy_name}")
def get_action_notification_policy(request: Request, policy_name: str) -> dict[str, object]:
    try:
        return runtime.foundry.actions.get_notification_policy(policy_name, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.put("/api/actions/notification-policies/{policy_name}")
def update_action_notification_policy(
    request: Request,
    policy_name: str,
    payload: ActionNotificationPolicyUpdateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> dict[str, object]:
    try:
        return runtime.foundry.actions.update_notification_policy(
            policy_name,
            display_name=payload.display_name,
            delivery_mode=payload.delivery_mode,
            recipients=[item.model_dump(by_alias=True) for item in payload.recipients],
            status=payload.status,
            expected_fingerprint=payload.expected_fingerprint,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.delete("/api/actions/notification-policies/{policy_name}")
def disable_action_notification_policy(
    request: Request,
    policy_name: str,
    payload: ActionNotificationPolicyDisableRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> dict[str, object]:
    try:
        return runtime.foundry.actions.disable_notification_policy(
            policy_name,
            expected_fingerprint=payload.expected_fingerprint,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
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


@router.post("/api/actions/{action_type}/parameters/{parameter_name}/uploads")
def upload_action_parameter(
    request: Request,
    action_type: str,
    parameter_name: str,
    object_type: str = Form(..., alias="objectType"),
    object_id: str = Form(..., alias="objectId"),
    file: UploadFile = MEDIA_UPLOAD_FILE,
    supplied_mime_type: str | None = Form(default=None, alias="suppliedMimeType"),
    format: str | None = Form(default=None),
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> ActionMediaUploadResult:
    try:
        _require_action_upload_size(file)
        return runtime.foundry.actions.upload_parameter(
            action_type,
            parameter_name,
            object_type=object_type,
            object_id=object_id,
            file_name=file.filename or "upload.bin",
            source=file.file,
            supplied_mime_type=supplied_mime_type or file.content_type or "application/octet-stream",
            idempotency_key=idempotency_key,
            format=format,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/actions/{action_type}/apply")
def apply_action(
    request: Request,
    action_type: str,
    payload: ActionApplyRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> ActionApplyResponse | dict[str, object]:
    try:
        if payload.branch_id is not None:
            return runtime.foundry.actions.execute_branch(
                action_type,
                branch_id=payload.branch_id,
                object_type=payload.target.object_type,
                object_id=payload.target.object_id,
                expected_object_version=payload.expected_object_version,
                params=payload.params,
                idempotency_key=idempotency_key,
                ctx=_ctx(request),
            )
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
        if payload.branch_id is not None:
            snapshot = runtime.foundry.actions.execute_branch(
                action_type,
                branch_id=payload.branch_id,
                object_type=payload.target.object_type,
                object_id=payload.target.object_id,
                expected_object_version=payload.expected_object_version,
                params=payload.params,
                idempotency_key=idempotency_key,
                ctx=_ctx(request),
            )
            response.status_code = status.HTTP_200_OK
            return snapshot
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


@router.post("/api/actions/{action_type}/batch-runs", status_code=status.HTTP_202_ACCEPTED)
def start_action_batch_run(
    request: Request,
    response: Response,
    action_type: str,
    payload: ActionFunctionBatchRunRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    wait_seconds: int = Query(default=0, ge=0, le=30, alias="waitSeconds"),
) -> dict[str, object]:
    try:
        snapshot = runtime.foundry.actions.start_batch_run(
            action_type,
            object_type=payload.object_type,
            items=[item.model_dump(by_alias=True) for item in payload.items],
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
            branch_id=payload.branch_id,
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
            branch_id=payload.branch_id,
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


def _require_action_upload_size(file: UploadFile) -> None:
    file.file.seek(0, os.SEEK_END)
    size_bytes = file.file.tell()
    file.file.seek(0)
    maximum = max_media_upload_bytes()
    if size_bytes > maximum:
        raise ValidationFailed(
            "Action media upload exceeds the platform size limit",
            details={"sizeBytes": size_bytes, "maxBytes": maximum},
        )


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
