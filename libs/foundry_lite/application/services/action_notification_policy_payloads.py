"""Validation, fingerprints, cursors, and views for notification policies."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Mapping, Sequence

from foundry_lite.application.ports.action_notification_policy_repository import ActionNotificationPolicyRow
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed

_POLICY_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,63}$")


def normalize_policy_input(
    *, display_name: str, delivery_mode: str, recipients: Sequence[Mapping[str, object]], status: str
) -> dict[str, object]:
    name = display_name.strip()
    if not name or len(name) > 120:
        raise ValidationFailed("notification policy displayName must contain 1..120 characters")
    if delivery_mode not in {"strict", "best_effort"}:
        raise ValidationFailed("notification policy deliveryMode must be strict or best_effort")
    if status not in {"active", "disabled"}:
        raise ValidationFailed("notification policy status must be active or disabled")
    normalized = tuple(_normalize_recipient(item) for item in recipients)
    if not normalized or len(normalized) > 500:
        raise ValidationFailed("notification policy requires 1..500 recipients")
    if len({item["userId"] for item in normalized}) != len(normalized):
        raise ValidationFailed("notification policy recipient userId values must be unique")
    return {"displayName": name, "deliveryMode": delivery_mode, "recipients": normalized, "status": status}


def require_policy_name(policy_name: str) -> str:
    name = policy_name.strip()
    if not _POLICY_NAME.fullmatch(name):
        raise ValidationFailed("policyName must match ^[A-Za-z][A-Za-z0-9_-]{1,63}$")
    return name


def require_policy_idempotency_key(value: str) -> str:
    key = value.strip()
    if not key or len(key) > 200:
        raise ValidationFailed("Idempotency-Key must contain 1..200 characters")
    return key


def policy_config_fingerprint(policy_name: str, normalized: Mapping[str, object]) -> str:
    payload = {"policyName": policy_name, **normalized}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def policy_request_fingerprint(operation: str, policy_name: str, request: Mapping[str, object]) -> str:
    canonical = json.dumps(
        {"operation": operation, "policyName": policy_name, "request": request},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def policy_view(row: ActionNotificationPolicyRow) -> dict[str, object]:
    return {
        "id": row["id"],
        "policyName": row["policy_name"],
        "targetRef": row["target_ref"],
        "displayName": row["display_name"],
        "deliveryMode": row["delivery_mode"],
        "recipients": row["recipients"],
        "status": row["status"],
        "version": row["version"],
        "configFingerprint": row["config_fingerprint"],
        "createdByUserId": row["created_by_user_id"],
        "updatedByUserId": row["updated_by_user_id"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def policy_audit_view(row: ActionNotificationPolicyRow) -> dict[str, object]:
    return {
        "policyName": row["policy_name"],
        "targetRef": row["target_ref"],
        "deliveryMode": row["delivery_mode"],
        "recipientCount": len(row["recipients"]),
        "status": row["status"],
        "version": row["version"],
        "configFingerprint": row["config_fingerprint"],
    }


def encode_policy_cursor(tenant_id: str, after_name: str) -> str:
    raw = json.dumps({"tenantId": tenant_id, "after": after_name}, sort_keys=True, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_policy_cursor(cursor: str | None, tenant_id: str) -> str | None:
    if cursor is None:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationFailed("invalid notification policy cursor") from exc
    if not isinstance(payload, Mapping) or payload.get("tenantId") != tenant_id:
        raise ValidationFailed("notification policy cursor does not match the tenant")
    after = payload.get("after")
    if not isinstance(after, str) or not after:
        raise ValidationFailed("invalid notification policy cursor")
    return after


def require_idempotency_replay(row: Mapping[str, object], request_fingerprint: str) -> dict[str, object]:
    if row["request_fingerprint"] != request_fingerprint:
        raise ConflictDetected("Idempotency-Key was already used with a different notification policy request")
    response = row["response_json"]
    if not isinstance(response, Mapping):
        raise ConflictDetected("stored notification policy idempotency result is invalid")
    return dict(response)


def _normalize_recipient(item: Mapping[str, object]) -> dict[str, object]:
    user_id = _recipient_user_id(item.get("userId"))
    roles = _recipient_roles(item.get("roles"))
    return {"userId": user_id, "roles": roles}


def _recipient_user_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 200:
        raise ValidationFailed("notification policy recipient userId is required")
    return value.strip()


def _recipient_roles(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ValidationFailed("notification policy recipient roles must be a list")
    normalized = sorted({role.strip() for role in value if isinstance(role, str) and role.strip()})
    if not normalized or len(normalized) != len(value):
        raise ValidationFailed("notification policy recipient roles must be unique non-empty strings")
    return normalized
