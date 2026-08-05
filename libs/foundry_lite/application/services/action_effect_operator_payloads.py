"""Validation, cursors, fingerprints, and safe effect-operator projections."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping

from foundry_lite.application.action_async_execution_types import ActionEffectReceiptRow
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed

EFFECT_STATUSES = frozenset(
    {"pending", "delivering", "retry_wait", "succeeded", "dead_letter", "outcome_unknown", "cancelled"}
)
EFFECT_RECONCILIATION_RESOLUTIONS = frozenset({"confirmed_delivered", "confirmed_not_delivered"})
_VERIFICATION_METHODS = frozenset({"provider_query", "provider_dashboard", "support_confirmation"})


def effect_operator_view(row: ActionEffectReceiptRow) -> dict[str, object]:
    """Project operational evidence without exposing effect parameters or lease tokens."""
    return {
        "receiptId": row["id"],
        "actionRunId": row["action_run_id"],
        "effectId": row["effect_id"],
        "phase": row["phase"],
        "kind": row["effect_kind"],
        "targetRef": row["target_ref"],
        "status": row["status"],
        "idempotencyKey": row["idempotency_key"],
        "attemptCount": row["attempt_count"],
        "maxAttempts": row["max_attempts"],
        "workerId": row["worker_id"],
        "fencingToken": row["fencing_token"],
        "heartbeatAt": row["heartbeat_at"],
        "dispatchStartedAt": row["dispatch_started_at"],
        "retryAt": row["retry_at"],
        "cancelRequestedAt": row["cancel_requested_at"],
        "cancelReason": row["cancel_reason"],
        "externalExecutionId": row["external_execution_id"],
        "response": row["response"],
        "error": row["error"],
        "reconciledAt": row["reconciled_at"],
        "reconciledByUserId": row["reconciled_by_user_id"],
        "reconciliation": row["reconciliation"],
        "notificationRendering": _notification_rendering(row),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "completedAt": row["completed_at"],
        "cancellationDisposition": _cancellation_disposition(row),
    }


def _notification_rendering(row: ActionEffectReceiptRow) -> dict[str, object] | None:
    value = row["request"].get("notificationRendering")
    return dict(value) if isinstance(value, dict) else None


def normalize_effect_status(value: str | None) -> str | None:
    if value is None:
        return None
    status = value.strip()
    if status not in EFFECT_STATUSES:
        raise ValidationFailed("Action effect status filter is invalid")
    return status


def normalize_reconciliation(resolution: str, evidence: Mapping[str, object]) -> tuple[str, dict[str, object]]:
    if resolution not in EFFECT_RECONCILIATION_RESOLUTIONS:
        raise ValidationFailed("effect reconciliation resolution is invalid")
    method = _required_text(evidence, "verificationMethod", 80)
    if method not in _VERIFICATION_METHODS:
        raise ValidationFailed("effect reconciliation verificationMethod is invalid")
    normalized: dict[str, object] = {
        "verificationMethod": method,
        "providerReference": _required_text(evidence, "providerReference", 200),
        "verifiedAt": _required_text(evidence, "verifiedAt", 80),
    }
    external_id = evidence.get("externalExecutionId")
    if external_id is not None:
        if not isinstance(external_id, str) or not external_id.strip() or len(external_id.strip()) > 200:
            raise ValidationFailed("effect reconciliation externalExecutionId is invalid")
        normalized["externalExecutionId"] = external_id.strip()
    if resolution == "confirmed_delivered" and "externalExecutionId" not in normalized:
        raise ValidationFailed("confirmed delivery requires externalExecutionId")
    return resolution, normalized


def require_effect_operation_key(value: str) -> str:
    key = value.strip()
    if not key or len(key) > 200:
        raise ValidationFailed("Idempotency-Key must contain 1..200 characters")
    return key


def effect_operation_fingerprint(operation: str, receipt_id: str, request: Mapping[str, object]) -> str:
    canonical = json.dumps(
        {"operation": operation, "receiptId": receipt_id, "request": request},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def require_effect_operation_replay(row: Mapping[str, object], request_fingerprint: str) -> dict[str, object]:
    if row["request_fingerprint"] != request_fingerprint:
        raise ConflictDetected("Idempotency-Key was already used with a different effect operation")
    response = row["response_json"]
    if not isinstance(response, Mapping):
        raise ConflictDetected("stored effect operation response is invalid")
    return dict(response)


def encode_effect_cursor(tenant_id: str, row: ActionEffectReceiptRow) -> str:
    payload = {"tenantId": tenant_id, "createdAt": row["created_at"], "receiptId": row["id"]}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_effect_cursor(cursor: str | None, tenant_id: str) -> tuple[str | None, str | None]:
    if cursor is None:
        return None, None
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationFailed("invalid Action effect cursor") from exc
    if not isinstance(payload, Mapping) or payload.get("tenantId") != tenant_id:
        raise ValidationFailed("Action effect cursor does not match the tenant")
    created_at = payload.get("createdAt")
    receipt_id = payload.get("receiptId")
    if not isinstance(created_at, str) or not isinstance(receipt_id, str):
        raise ValidationFailed("invalid Action effect cursor")
    return created_at, receipt_id


def _required_text(value: Mapping[str, object], key: str, max_length: int) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip() or len(item.strip()) > max_length:
        raise ValidationFailed(f"effect reconciliation {key} is required")
    return item.strip()


def _cancellation_disposition(row: ActionEffectReceiptRow) -> str | None:
    if row["cancel_requested_at"] is None:
        return None
    if row["status"] == "cancelled":
        return "cancelled_before_confirmed_delivery"
    if row["status"] == "succeeded":
        return "remote_delivery_won"
    if row["status"] == "outcome_unknown":
        return "reconciliation_required"
    return "in_flight_best_effort"
