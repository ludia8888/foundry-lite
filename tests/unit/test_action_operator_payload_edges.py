from __future__ import annotations

from typing import cast

import pytest
from foundry_lite.application.action_async_execution_types import ActionEffectReceiptRow
from foundry_lite.application.ports.action_notification_policy_repository import ActionNotificationPolicyRow
from foundry_lite.application.services.action_effect_operator_payloads import (
    decode_effect_cursor,
    effect_operation_fingerprint,
    effect_operator_view,
    encode_effect_cursor,
    normalize_effect_status,
    normalize_reconciliation,
    require_effect_operation_key,
    require_effect_operation_replay,
)
from foundry_lite.application.services.action_notification_policy_payloads import (
    decode_policy_cursor,
    encode_policy_cursor,
    normalize_policy_input,
    policy_audit_view,
    policy_config_fingerprint,
    policy_request_fingerprint,
    policy_view,
    require_idempotency_replay,
    require_policy_idempotency_key,
    require_policy_name,
)
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed


def _effect(*, status: str = "dead_letter", cancelled: bool = False) -> ActionEffectReceiptRow:
    return cast(
        ActionEffectReceiptRow,
        {
            "id": "receipt-1",
            "tenant_id": "tenant-a",
            "action_run_id": "run-1",
            "effect_id": "notify-ops",
            "phase": "after_commit",
            "effect_kind": "notification",
            "target_ref": "notification-policy:ops",
            "status": status,
            "idempotency_key": "effect-1",
            "attempt_count": 2,
            "max_attempts": 3,
            "worker_id": "worker-1",
            "lease_token": "must-not-leak",
            "lease_expires_at": "2026-08-13T00:01:00Z",
            "fencing_token": 4,
            "heartbeat_at": "2026-08-13T00:00:05Z",
            "dispatch_started_at": "2026-08-13T00:00:00Z",
            "cancel_requested_at": "2026-08-13T00:00:10Z" if cancelled else None,
            "cancel_reason": "superseded" if cancelled else None,
            "request": {
                "secret": "must-not-leak",
                "notificationRendering": {"title": "Order needs review"},
            },
            "response": None,
            "error": {"kind": "timeout"},
            "retry_at": None,
            "external_execution_id": None,
            "outbox_event_id": "outbox-1",
            "created_at": "2026-08-13T00:00:00Z",
            "updated_at": "2026-08-13T00:00:10Z",
            "completed_at": None,
            "reconciled_at": None,
            "reconciled_by_user_id": None,
            "reconciliation": None,
        },
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("cancelled", "cancelled_before_confirmed_delivery"),
        ("succeeded", "remote_delivery_won"),
        ("outcome_unknown", "reconciliation_required"),
        ("delivering", "in_flight_best_effort"),
    ],
)
def test_effect_operator_view_redacts_request_and_explains_cancellation(status: str, expected: str) -> None:
    view = effect_operator_view(_effect(status=status, cancelled=True))

    assert view["cancellationDisposition"] == expected
    assert view["notificationRendering"] == {"title": "Order needs review"}
    assert "request" not in view and "leaseToken" not in view
    assert "must-not-leak" not in str(view)
    assert effect_operator_view(_effect())["cancellationDisposition"] is None


@pytest.mark.parametrize("value", ["", "unknown"])
def test_effect_status_and_operation_keys_reject_noncanonical_values(value: str) -> None:
    with pytest.raises(ValidationFailed):
        normalize_effect_status(value)
    with pytest.raises(ValidationFailed):
        require_effect_operation_key(" " if not value else "x" * 201)

    assert normalize_effect_status(None) is None
    assert normalize_effect_status("succeeded") == "succeeded"
    assert normalize_effect_status(" succeeded ") == "succeeded"
    assert require_effect_operation_key(" retry-1 ") == "retry-1"


def test_effect_reconciliation_requires_provider_evidence_and_exact_replay() -> None:
    evidence = {
        "verificationMethod": "provider_query",
        "providerReference": "delivery-42",
        "verifiedAt": "2026-08-13T00:00:00Z",
        "externalExecutionId": "provider-42",
    }
    resolution, normalized = normalize_reconciliation("confirmed_delivered", evidence)
    fingerprint = effect_operation_fingerprint("reconcile", "receipt-1", normalized)

    assert resolution == "confirmed_delivered"
    assert normalized["externalExecutionId"] == "provider-42"
    assert fingerprint == effect_operation_fingerprint("reconcile", "receipt-1", normalized)
    assert require_effect_operation_replay(
        {"request_fingerprint": fingerprint, "response_json": {"status": "succeeded"}}, fingerprint
    ) == {"status": "succeeded"}
    with pytest.raises(ValidationFailed, match="requires externalExecutionId"):
        normalize_reconciliation(
            "confirmed_delivered", {key: value for key, value in evidence.items() if key != "externalExecutionId"}
        )
    with pytest.raises(ValidationFailed, match="resolution"):
        normalize_reconciliation("maybe", evidence)
    with pytest.raises(ValidationFailed, match="verificationMethod"):
        normalize_reconciliation("confirmed_not_delivered", {**evidence, "verificationMethod": "guess"})
    with pytest.raises(ValidationFailed, match="externalExecutionId"):
        normalize_reconciliation("confirmed_not_delivered", {**evidence, "externalExecutionId": " "})
    with pytest.raises(ConflictDetected):
        require_effect_operation_replay({"request_fingerprint": "sha256:different", "response_json": {}}, fingerprint)
    with pytest.raises(ConflictDetected, match="response"):
        require_effect_operation_replay({"request_fingerprint": fingerprint, "response_json": []}, fingerprint)


def test_effect_cursor_is_tenant_bound_and_malformed_values_fail_closed() -> None:
    cursor = encode_effect_cursor("tenant-a", _effect())
    assert decode_effect_cursor(cursor, "tenant-a") == ("2026-08-13T00:00:00Z", "receipt-1")
    assert decode_effect_cursor(None, "tenant-a") == (None, None)
    with pytest.raises(ValidationFailed, match="tenant"):
        decode_effect_cursor(cursor, "tenant-b")
    for malformed in ("not-base64", "W10=", "e30="):
        with pytest.raises(ValidationFailed):
            decode_effect_cursor(malformed, "tenant-a")


def _policy() -> ActionNotificationPolicyRow:
    return cast(
        ActionNotificationPolicyRow,
        {
            "id": "policy-1",
            "tenant_id": "tenant-a",
            "policy_name": "Operations",
            "target_ref": "notification-policy:Operations",
            "display_name": "Operations",
            "delivery_mode": "strict",
            "recipients": [{"userId": "operator-1", "roles": ["ops_manager"]}],
            "status": "active",
            "version": 2,
            "config_fingerprint": "sha256:config",
            "created_by_user_id": "admin-1",
            "updated_by_user_id": "admin-2",
            "created_at": "2026-08-13T00:00:00Z",
            "updated_at": "2026-08-13T01:00:00Z",
        },
    )


def test_notification_policy_normalization_views_and_fingerprints_are_deterministic() -> None:
    normalized = normalize_policy_input(
        display_name=" Operations ",
        delivery_mode="strict",
        recipients=[{"userId": " operator-1 ", "roles": ["viewer", "ops_manager"]}],
        status="active",
    )
    policy = _policy()

    assert normalized["recipients"] == ({"userId": "operator-1", "roles": ["ops_manager", "viewer"]},)
    assert require_policy_name(" Operations ") == "Operations"
    assert require_policy_idempotency_key(" policy-1 ") == "policy-1"
    assert policy_view(policy)["updatedByUserId"] == "admin-2"
    assert policy_audit_view(policy)["recipientCount"] == 1
    assert policy_config_fingerprint("Operations", normalized) == policy_config_fingerprint("Operations", normalized)
    assert policy_request_fingerprint("create", "Operations", normalized).startswith("sha256:")


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "display_name": "",
            "delivery_mode": "strict",
            "recipients": [{"userId": "u", "roles": ["ops"]}],
            "status": "active",
        },
        {
            "display_name": "Ops",
            "delivery_mode": "unsafe",
            "recipients": [{"userId": "u", "roles": ["ops"]}],
            "status": "active",
        },
        {"display_name": "Ops", "delivery_mode": "strict", "recipients": [], "status": "active"},
        {
            "display_name": "Ops",
            "delivery_mode": "strict",
            "recipients": [{"userId": "u", "roles": ["ops"]}],
            "status": "unknown",
        },
        {
            "display_name": "Ops",
            "delivery_mode": "strict",
            "recipients": [{"userId": "u", "roles": ["ops"]}, {"userId": "u", "roles": ["viewer"]}],
            "status": "active",
        },
        {
            "display_name": "Ops",
            "delivery_mode": "strict",
            "recipients": [{"userId": "", "roles": ["ops"]}],
            "status": "active",
        },
        {
            "display_name": "Ops",
            "delivery_mode": "strict",
            "recipients": [{"userId": "u", "roles": "ops"}],
            "status": "active",
        },
        {
            "display_name": "Ops",
            "delivery_mode": "strict",
            "recipients": [{"userId": "u", "roles": ["ops", "ops"]}],
            "status": "active",
        },
    ],
)
def test_notification_policy_rejects_malformed_configuration(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationFailed):
        normalize_policy_input(**kwargs)  # type: ignore[arg-type]


def test_notification_policy_cursor_and_replay_are_tenant_and_fingerprint_bound() -> None:
    cursor = encode_policy_cursor("tenant-a", "Operations")
    fingerprint = policy_request_fingerprint("create", "Operations", {"status": "active"})
    replay = {"request_fingerprint": fingerprint, "response_json": {"policyName": "Operations"}}

    assert decode_policy_cursor(None, "tenant-a") is None
    assert decode_policy_cursor(cursor, "tenant-a") == "Operations"
    assert require_idempotency_replay(replay, fingerprint) == {"policyName": "Operations"}
    with pytest.raises(ValidationFailed, match="tenant"):
        decode_policy_cursor(cursor, "tenant-b")
    for malformed in ("not-base64", "W10=", "e30="):
        with pytest.raises(ValidationFailed):
            decode_policy_cursor(malformed, "tenant-a")
    with pytest.raises(ConflictDetected):
        require_idempotency_replay(replay, "sha256:different")
    with pytest.raises(ConflictDetected, match="result"):
        require_idempotency_replay({"request_fingerprint": fingerprint, "response_json": []}, fingerprint)
    with pytest.raises(ValidationFailed):
        require_policy_name("x")
    with pytest.raises(ValidationFailed):
        require_policy_idempotency_key("x" * 201)
