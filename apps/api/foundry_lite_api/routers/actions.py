"""Action apply/validate routes."""

from __future__ import annotations

from fastapi import APIRouter, Header, Request
from foundry_lite.application.action_types import ActionApplyResponse, ActionValidationResponse
from foundry_lite.domain.errors import FoundryLiteError

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.request_context import _ctx
from foundry_lite_api.schemas import ActionApplyRequest

router = APIRouter()


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
