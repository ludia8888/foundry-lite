"""Application proof for Action effect cancellation, DLQ retry, and reconciliation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from foundry_lite.application.action_async_execution_types import (
    ActionAsyncRunRecord,
    ActionEffectClaim,
    ActionEffectReceiptRecord,
    ActionRunStepRecord,
)
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import ConflictDetected, PermissionDenied
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from sqlalchemy import func, select


def test_effect_cancel_is_idempotent_audited_and_blocks_undispatched_worker(tmp_path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path))
    ctx = demo_admin_context()
    _seed_receipt(foundry, "receipt-cancel", "pending")

    cancelled = foundry.actions.cancel_effect(
        "receipt-cancel",
        reason="operator stopped an obsolete delivery",
        idempotency_key="cancel-effect-1",
        ctx=ctx,
    )
    replay = foundry.actions.cancel_effect(
        "receipt-cancel",
        reason="operator stopped an obsolete delivery",
        idempotency_key="cancel-effect-1",
        ctx=ctx,
    )

    assert replay == cancelled
    assert cancelled["status"] == "cancelled"
    assert cancelled["cancellationDisposition"] == "cancelled_before_confirmed_delivery"
    assert _count(foundry, db.action_effect_operation_requests) == 1
    assert _count(foundry, db.audit_events) == 1
    assert _count(foundry, db.outbox_events) == 1
    with pytest.raises(ConflictDetected):
        foundry.actions.cancel_effect(
            "receipt-cancel",
            reason="different request",
            idempotency_key="cancel-effect-1",
            ctx=ctx,
        )


def test_effect_dead_letter_retry_and_unknown_reconciliation_are_operator_gated(tmp_path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path))
    ctx = demo_admin_context()
    _seed_receipt(foundry, "receipt-retry", "dead_letter")
    _seed_receipt(foundry, "receipt-unknown", "outcome_unknown", effect_id="notify-finance")

    retried = foundry.actions.retry_effect("receipt-retry", idempotency_key="retry-effect-1", ctx=ctx)
    reconciled = foundry.actions.reconcile_effect(
        "receipt-unknown",
        resolution="confirmed_delivered",
        evidence={
            "verificationMethod": "provider_query",
            "providerReference": "case-42",
            "verifiedAt": "2026-08-05T12:00:00Z",
            "externalExecutionId": "provider-delivery-42",
        },
        idempotency_key="reconcile-effect-1",
        ctx=ctx,
    )

    assert retried["status"] == "retry_wait"
    assert retried["maxAttempts"] == retried["attemptCount"] + 1
    assert reconciled["status"] == "succeeded"
    assert reconciled["externalExecutionId"] == "provider-delivery-42"
    assert reconciled["reconciliation"]["resolution"] == "confirmed_delivered"
    listed = foundry.actions.list_effect_receipts(status="succeeded", ctx=ctx)
    assert [item["receiptId"] for item in listed["items"]] == ["receipt-unknown"]

    engineer = RequestContext(
        tenant_id=ctx.tenant_id,
        actor_user_id="engineer-1",
        roles=("data_engineer",),
    )
    assert foundry.actions.get_effect_receipt("receipt-unknown", ctx=engineer)["status"] == "succeeded"
    with pytest.raises(PermissionDenied):
        foundry.actions.retry_effect("receipt-retry", idempotency_key="retry-denied", ctx=engineer)


def _seed_receipt(
    foundry: FoundryLite,
    receipt_id: str,
    terminal_status: str,
    *,
    effect_id: str = "notify-ops",
) -> None:
    repository = foundry._services.action.effect_operations.action_execution_repository
    now = datetime.now(UTC)
    created_at = now.isoformat()
    with foundry.engine.begin() as transaction:
        if repository.run_by_id(transaction=transaction, tenant_id="tenant-demo", run_id="run-effects") is None:
            repository.insert_run(
                transaction=transaction,
                record=ActionAsyncRunRecord(
                    "run-effects",
                    "tenant-demo",
                    "action-1",
                    "ApproveOrder",
                    "user-demo",
                    "otype-1",
                    "Order",
                    "O-1",
                    1,
                    {},
                    "run-idempotency",
                    "sha256:request",
                    "sha256:definition",
                    "sha256:plan",
                    {},
                    created_at,
                ),
                steps=(
                    ActionRunStepRecord(
                        "step-effects", "tenant-demo", "run-effects", "commit", "commit", {}, created_at
                    ),
                ),
            )
        receipt = repository.insert_effect_receipt(
            transaction=transaction,
            record=ActionEffectReceiptRecord(
                receipt_id,
                "tenant-demo",
                "run-effects",
                effect_id,
                "after_commit",
                "notification",
                "notification-policy:operations",
                f"action-effect:run-effects:{effect_id}",
                3,
                {"effect": {"effectId": effect_id}},
                created_at,
            ),
        )
        assert receipt is not None
        if terminal_status != "pending":
            claimed = repository.claim_effect_receipt(
                transaction=transaction,
                claim=ActionEffectClaim(
                    "tenant-demo",
                    receipt_id,
                    "worker-seed",
                    f"lease-{receipt_id}",
                    (now + timedelta(minutes=5)).isoformat(),
                    created_at,
                ),
            )
            assert claimed is not None
            completed = repository.complete_effect_receipt(
                transaction=transaction,
                tenant_id="tenant-demo",
                receipt_id=receipt_id,
                worker_id="worker-seed",
                lease_token=f"lease-{receipt_id}",
                fencing_token=claimed["fencing_token"],
                status=terminal_status,
                response=None,
                error={"kind": terminal_status},
                retry_at=None,
                external_execution_id=None,
                completed_at=(now + timedelta(seconds=1)).isoformat(),
            )
            assert completed is not None


def _count(foundry: FoundryLite, table) -> int:
    with foundry.engine.begin() as transaction:
        return int(transaction.execute(select(func.count()).select_from(table)).scalar_one())
