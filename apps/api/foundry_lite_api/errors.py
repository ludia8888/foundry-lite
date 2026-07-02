"""FoundryLiteError to HTTP/WebSocket response mapping helpers."""

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from foundry_lite.domain.errors import FoundryLiteError

from foundry_lite_api.schemas import JsonObject, ValidationErrorPayload


def _validation_errors(exc: RequestValidationError) -> list[ValidationErrorPayload]:
    return [
        {
            "type": str(error.get("type", "validation_error")),
            "loc": [str(item) for item in error.get("loc", ())],
            "msg": str(error.get("msg", "request validation failed")),
        }
        for error in exc.errors()
    ]


def _handle_error(exc: FoundryLiteError, request: Request | None = None) -> HTTPException:
    status_by_code = {
        "NOT_FOUND": 404,
        "CONFLICT": 409,
        "PERMISSION_DENIED": 403,
        "RATE_LIMITED": 429,
    }
    status = _status_for_error(exc, status_by_code.get(exc.code, 400))
    request_id = getattr(getattr(request, "state", None), "request_id", None)
    return HTTPException(
        status_code=status,
        detail={"code": _code_for_error(exc), "message": exc.message, "details": exc.details, "request_id": request_id},
    )


def _websocket_error(exc: FoundryLiteError, request_id: str) -> JsonObject:
    return {"code": _code_for_error(exc), "message": exc.message, "details": exc.details, "request_id": request_id}


def _code_for_error(exc: FoundryLiteError) -> str:
    if exc.code != "APPROVAL_EXECUTION_ERROR":
        return exc.code
    reason = exc.details.get("reason")
    if isinstance(reason, str) and reason:
        return reason.upper()
    return exc.code


def _status_for_error(exc: FoundryLiteError, default_status: int) -> int:
    if exc.code != "APPROVAL_EXECUTION_ERROR":
        return default_status
    reason = exc.details.get("reason")
    if not isinstance(reason, str):
        return default_status
    not_found = {"action_not_found", "run_not_found", "target_not_found"}
    conflict = {
        "approval_object_version_conflict",
        "fingerprint_mismatch",
        "originating_tool_call_not_found",
        "review_expired",
        "review_not_approved",
    }
    denied = {"policy_denied", "source_access_denied"}
    if reason in not_found:
        return 404
    if reason in conflict:
        return 409
    if reason in denied:
        return 403
    return default_status
