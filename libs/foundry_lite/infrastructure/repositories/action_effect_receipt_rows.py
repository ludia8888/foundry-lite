"""Durable Action effect receipt claims, fencing, retry, and terminal writes."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from foundry_lite.application.action_async_execution_types import (
    ActionEffectClaim,
    ActionEffectOperationRecord,
    ActionEffectOperationRow,
    ActionEffectReceiptRecord,
    ActionEffectReceiptRow,
)
from foundry_lite.application.state_transitions import (
    ACTION_EFFECT_CANCELLED,
    ACTION_EFFECT_DEAD_LETTER,
    ACTION_EFFECT_DELIVERING,
    ACTION_EFFECT_MANUAL_RETRY,
    ACTION_EFFECT_OUTCOME_UNKNOWN,
    ACTION_EFFECT_RECONCILED_DEAD_LETTER,
    ACTION_EFFECT_RECONCILED_SUCCEEDED,
    ACTION_EFFECT_RETRY_WAIT,
    ACTION_EFFECT_SUCCEEDED,
    StatusTransition,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.status_cas import cas_status_update


def insert_receipt(transaction: Any, record: ActionEffectReceiptRecord) -> ActionEffectReceiptRow | None:
    """Insert a receipt once and report whether this call won idempotency."""
    inserted = transaction.execute(_insert_or_ignore(transaction, record)).scalar_one_or_none()
    return required_receipt(transaction, record.tenant_id, record.receipt_id) if inserted else None


def receipts_for_run(transaction: Any, tenant_id: str, run_id: str) -> list[ActionEffectReceiptRow]:
    """List ordered effect receipts for an Action run."""
    rows = (
        transaction.execute(
            select(db.action_effect_receipts)
            .where(
                and_(
                    db.action_effect_receipts.c.tenant_id == tenant_id,
                    db.action_effect_receipts.c.action_run_id == run_id,
                )
            )
            .order_by(db.action_effect_receipts.c.created_at, db.action_effect_receipts.c.effect_id)
        )
        .mappings()
        .all()
    )
    return [cast(ActionEffectReceiptRow, dict(row)) for row in rows]


def receipt_by_id(transaction: Any, tenant_id: str, receipt_id: str) -> ActionEffectReceiptRow | None:
    """Load one tenant-scoped receipt for operator and worker checks."""
    row = (
        transaction.execute(
            select(db.action_effect_receipts).where(
                and_(
                    db.action_effect_receipts.c.tenant_id == tenant_id,
                    db.action_effect_receipts.c.id == receipt_id,
                )
            )
        )
        .mappings()
        .first()
    )
    return cast(ActionEffectReceiptRow, dict(row)) if row else None


def list_receipts(
    transaction: Any,
    tenant_id: str,
    status: str | None,
    before_created_at: str | None,
    before_receipt_id: str | None,
    limit: int,
) -> list[ActionEffectReceiptRow]:
    """List an operator queue with stable descending keyset pagination."""
    conditions = [db.action_effect_receipts.c.tenant_id == tenant_id]
    if status is not None:
        conditions.append(db.action_effect_receipts.c.status == status)
    if before_created_at is not None and before_receipt_id is not None:
        conditions.append(
            or_(
                db.action_effect_receipts.c.created_at < before_created_at,
                and_(
                    db.action_effect_receipts.c.created_at == before_created_at,
                    db.action_effect_receipts.c.id < before_receipt_id,
                ),
            )
        )
    rows = (
        transaction.execute(
            select(db.action_effect_receipts)
            .where(and_(*conditions))
            .order_by(db.action_effect_receipts.c.created_at.desc(), db.action_effect_receipts.c.id.desc())
            .limit(limit)
        )
        .mappings()
        .all()
    )
    return [cast(ActionEffectReceiptRow, dict(row)) for row in rows]


def pending_receipts(transaction: Any, tenant_id: str, limit: int, due_at: str) -> list[ActionEffectReceiptRow]:
    """List due after-commit receipts, including expired delivery leases."""
    rows = (
        transaction.execute(
            select(db.action_effect_receipts)
            .where(
                and_(
                    db.action_effect_receipts.c.tenant_id == tenant_id,
                    db.action_effect_receipts.c.phase == "after_commit",
                    db.action_effect_receipts.c.attempt_count < db.action_effect_receipts.c.max_attempts,
                    or_(
                        db.action_effect_receipts.c.status == "pending",
                        and_(
                            db.action_effect_receipts.c.status == "retry_wait",
                            db.action_effect_receipts.c.retry_at <= due_at,
                        ),
                        and_(
                            db.action_effect_receipts.c.status == "delivering",
                            db.action_effect_receipts.c.lease_expires_at < due_at,
                        ),
                    ),
                )
            )
            .order_by(db.action_effect_receipts.c.created_at, db.action_effect_receipts.c.id)
            .limit(limit)
        )
        .mappings()
        .all()
    )
    return [cast(ActionEffectReceiptRow, dict(row)) for row in rows]


def status_counts(transaction: Any, tenant_id: str) -> dict[str, int]:
    """Aggregate durable effect backlog and terminal dispositions for monitoring."""
    rows = transaction.execute(
        select(db.action_effect_receipts.c.status, func.count())
        .where(db.action_effect_receipts.c.tenant_id == tenant_id)
        .group_by(db.action_effect_receipts.c.status)
    ).all()
    return {str(status): int(count) for status, count in rows}


def claim_receipt(transaction: Any, claim: ActionEffectClaim) -> ActionEffectReceiptRow | None:
    """Claim one receipt with a monotonically increasing fencing token."""
    row = _locked_receipt(transaction, claim.tenant_id, claim.receipt_id)
    if row is None or not _is_claimable(row, claim.claimed_at, claim.is_reconciliation):
        return None
    fencing_token = int(row["fencing_token"]) + 1
    updated = cas_status_update(
        transaction,
        db.action_effect_receipts,
        tenant_id=claim.tenant_id,
        row_id=claim.receipt_id,
        transition=ACTION_EFFECT_DELIVERING,
        conditions=(db.action_effect_receipts.c.fencing_token == row["fencing_token"],),
        values={
            "attempt_count": db.action_effect_receipts.c.attempt_count + 1,
            "worker_id": claim.worker_id,
            "lease_token": claim.lease_token,
            "lease_expires_at": claim.lease_expires_at,
            "fencing_token": fencing_token,
            "heartbeat_at": claim.claimed_at,
            "retry_at": None,
            "updated_at": claim.claimed_at,
        },
    )
    return required_receipt(transaction, claim.tenant_id, claim.receipt_id) if updated else None


def start_dispatch(
    transaction: Any,
    *,
    tenant_id: str,
    receipt_id: str,
    worker_id: str,
    lease_token: str,
    fencing_token: int,
    started_at: str,
) -> ActionEffectReceiptRow | None:
    """Seal the cancellation race immediately before an external dispatch."""
    result = transaction.execute(
        update(db.action_effect_receipts)
        .where(
            and_(
                db.action_effect_receipts.c.tenant_id == tenant_id,
                db.action_effect_receipts.c.id == receipt_id,
                db.action_effect_receipts.c.status == "delivering",
                db.action_effect_receipts.c.worker_id == worker_id,
                db.action_effect_receipts.c.lease_token == lease_token,
                db.action_effect_receipts.c.fencing_token == fencing_token,
                db.action_effect_receipts.c.cancel_requested_at.is_(None),
                db.action_effect_receipts.c.dispatch_started_at.is_(None),
            )
        )
        .values(dispatch_started_at=started_at, heartbeat_at=started_at, updated_at=started_at)
    )
    return required_receipt(transaction, tenant_id, receipt_id) if result.rowcount == 1 else None


def request_cancel(
    transaction: Any,
    *,
    tenant_id: str,
    receipt_id: str,
    reason: str | None,
    requested_at: str,
) -> ActionEffectReceiptRow | None:
    """Cancel undispatched work or durably mark a best-effort in-flight request."""
    row = _locked_receipt(transaction, tenant_id, receipt_id)
    if row is None:
        return None
    if row["status"] in {"succeeded", "dead_letter", "outcome_unknown", "cancelled"}:
        return row
    if row["status"] == "delivering" and row["dispatch_started_at"] is not None:
        transaction.execute(
            update(db.action_effect_receipts)
            .where(
                and_(
                    db.action_effect_receipts.c.tenant_id == tenant_id,
                    db.action_effect_receipts.c.id == receipt_id,
                    db.action_effect_receipts.c.fencing_token == row["fencing_token"],
                )
            )
            .values(cancel_requested_at=requested_at, cancel_reason=reason, updated_at=requested_at)
        )
        return required_receipt(transaction, tenant_id, receipt_id)
    updated = cas_status_update(
        transaction,
        db.action_effect_receipts,
        tenant_id=tenant_id,
        row_id=receipt_id,
        transition=ACTION_EFFECT_CANCELLED,
        conditions=(db.action_effect_receipts.c.fencing_token == row["fencing_token"],),
        values={
            "cancel_requested_at": requested_at,
            "cancel_reason": reason,
            "worker_id": None,
            "lease_token": None,  # nosec B105 -- DB lease state, not a credential; remove if field semantics change.
            "lease_expires_at": None,
            "fencing_token": int(row["fencing_token"]) + 1,
            "updated_at": requested_at,
            "completed_at": requested_at,
        },
    )
    return required_receipt(transaction, tenant_id, receipt_id) if updated else None


def retry_receipt(
    transaction: Any, *, tenant_id: str, receipt_id: str, requested_at: str
) -> ActionEffectReceiptRow | None:
    """Schedule one explicit operator retry using the original stable idempotency key."""
    row = _locked_receipt(transaction, tenant_id, receipt_id)
    if row is None or row["phase"] != "after_commit":
        return None
    updated = cas_status_update(
        transaction,
        db.action_effect_receipts,
        tenant_id=tenant_id,
        row_id=receipt_id,
        transition=ACTION_EFFECT_MANUAL_RETRY,
        conditions=(db.action_effect_receipts.c.fencing_token == row["fencing_token"],),
        values={
            "max_attempts": int(row["attempt_count"]) + 1,
            "retry_at": requested_at,
            "worker_id": None,
            "lease_token": None,  # nosec B105 -- DB lease state, not a credential; remove if field semantics change.
            "lease_expires_at": None,
            "dispatch_started_at": None,
            "completed_at": None,
            "updated_at": requested_at,
        },
    )
    return required_receipt(transaction, tenant_id, receipt_id) if updated else None


def reconcile_receipt(
    transaction: Any,
    *,
    tenant_id: str,
    receipt_id: str,
    resolution: str,
    evidence: dict[str, object],
    actor_user_id: str,
    reconciled_at: str,
) -> ActionEffectReceiptRow | None:
    """Resolve an ambiguous remote result only from explicit operator evidence."""
    if resolution not in {"confirmed_delivered", "confirmed_not_delivered"}:
        raise ValueError(f"unsupported Action effect reconciliation resolution: {resolution}")
    transition = (
        ACTION_EFFECT_RECONCILED_SUCCEEDED
        if resolution == "confirmed_delivered"
        else ACTION_EFFECT_RECONCILED_DEAD_LETTER
    )
    row = _locked_receipt(transaction, tenant_id, receipt_id)
    if row is None:
        return None
    external_id = evidence.get("externalExecutionId")
    values: dict[str, object] = {
        "reconciled_at": reconciled_at,
        "reconciled_by_user_id": actor_user_id,
        "reconciliation": {"resolution": resolution, **evidence},
        "updated_at": reconciled_at,
        "completed_at": reconciled_at,
    }
    if resolution == "confirmed_delivered":
        values["response"] = {"reconciled": True, "evidence": evidence}
        values["error"] = None
        if isinstance(external_id, str) and external_id:
            values["external_execution_id"] = external_id
    updated = cas_status_update(
        transaction,
        db.action_effect_receipts,
        tenant_id=tenant_id,
        row_id=receipt_id,
        transition=transition,
        conditions=(db.action_effect_receipts.c.fencing_token == row["fencing_token"],),
        values=values,
    )
    return required_receipt(transaction, tenant_id, receipt_id) if updated else None


def operation_by_idempotency(
    transaction: Any,
    *,
    tenant_id: str,
    actor_user_id: str,
    operation: str,
    idempotency_key: str,
) -> ActionEffectOperationRow | None:
    row = (
        transaction.execute(
            select(db.action_effect_operation_requests).where(
                and_(
                    db.action_effect_operation_requests.c.tenant_id == tenant_id,
                    db.action_effect_operation_requests.c.actor_user_id == actor_user_id,
                    db.action_effect_operation_requests.c.operation == operation,
                    db.action_effect_operation_requests.c.idempotency_key == idempotency_key,
                )
            )
        )
        .mappings()
        .first()
    )
    return cast(ActionEffectOperationRow, dict(row)) if row else None


def insert_operation_or_existing(
    transaction: Any, record: ActionEffectOperationRecord
) -> ActionEffectOperationRow | None:
    statement = _insert_operation_or_ignore(transaction, record)
    inserted = transaction.execute(statement).scalar_one_or_none()
    if inserted:
        return None
    return operation_by_idempotency(
        transaction,
        tenant_id=record.tenant_id,
        actor_user_id=record.actor_user_id,
        operation=record.operation,
        idempotency_key=record.idempotency_key,
    )


def complete_receipt(
    transaction: Any,
    *,
    tenant_id: str,
    receipt_id: str,
    worker_id: str,
    lease_token: str,
    fencing_token: int,
    status: str,
    response: dict[str, object] | None,
    error: dict[str, object] | None,
    retry_at: str | None,
    external_execution_id: str | None,
    completed_at: str,
) -> ActionEffectReceiptRow | None:
    """Write a receipt outcome only for its current fenced lease owner."""
    transition = _terminal_transition(status)
    updated = cas_status_update(
        transaction,
        db.action_effect_receipts,
        tenant_id=tenant_id,
        row_id=receipt_id,
        transition=transition,
        conditions=(
            db.action_effect_receipts.c.worker_id == worker_id,
            db.action_effect_receipts.c.lease_token == lease_token,
            db.action_effect_receipts.c.fencing_token == fencing_token,
            db.action_effect_receipts.c.lease_expires_at >= completed_at,
        ),
        values={
            "response": response,
            "error": error,
            "retry_at": retry_at,
            "dispatch_started_at": None if status == "retry_wait" else db.action_effect_receipts.c.dispatch_started_at,
            "external_execution_id": external_execution_id,
            "heartbeat_at": completed_at,
            "updated_at": completed_at,
            "completed_at": None if status == "retry_wait" else completed_at,
        },
    )
    return required_receipt(transaction, tenant_id, receipt_id) if updated else None


def required_receipt(transaction: Any, tenant_id: str, receipt_id: str) -> ActionEffectReceiptRow:
    """Load an existing receipt or raise the database row expectation error."""
    row = (
        transaction.execute(
            select(db.action_effect_receipts).where(
                and_(
                    db.action_effect_receipts.c.tenant_id == tenant_id,
                    db.action_effect_receipts.c.id == receipt_id,
                )
            )
        )
        .mappings()
        .one()
    )
    return cast(ActionEffectReceiptRow, dict(row))


def _locked_receipt(transaction: Any, tenant_id: str, receipt_id: str) -> ActionEffectReceiptRow | None:
    row = (
        transaction.execute(
            select(db.action_effect_receipts)
            .where(
                and_(
                    db.action_effect_receipts.c.tenant_id == tenant_id,
                    db.action_effect_receipts.c.id == receipt_id,
                )
            )
            .with_for_update()
        )
        .mappings()
        .first()
    )
    return cast(ActionEffectReceiptRow, dict(row)) if row else None


def _is_claimable(row: ActionEffectReceiptRow, claimed_at: str, is_reconciliation: bool) -> bool:
    if row["attempt_count"] >= row["max_attempts"] and not is_reconciliation:
        return False
    if row["status"] == "pending":
        return True
    if row["status"] == "retry_wait":
        return row["retry_at"] is not None and row["retry_at"] <= claimed_at
    return (
        row["status"] == "delivering" and row["lease_expires_at"] is not None and row["lease_expires_at"] < claimed_at
    )


def _terminal_transition(status: str) -> StatusTransition:
    transitions = {
        "succeeded": ACTION_EFFECT_SUCCEEDED,
        "retry_wait": ACTION_EFFECT_RETRY_WAIT,
        "dead_letter": ACTION_EFFECT_DEAD_LETTER,
        "outcome_unknown": ACTION_EFFECT_OUTCOME_UNKNOWN,
        "cancelled": ACTION_EFFECT_CANCELLED,
    }
    try:
        return transitions[status]
    except KeyError as exc:
        raise ValueError(f"unsupported Action effect status: {status}") from exc


def _insert_or_ignore(transaction: Any, record: ActionEffectReceiptRecord) -> Any:
    values = _record_values(record)
    dialect = transaction.dialect.name
    if dialect == "postgresql":
        return (
            postgres_insert(db.action_effect_receipts)
            .values(**values)
            .on_conflict_do_nothing()
            .returning(db.action_effect_receipts.c.id)
        )
    if dialect == "sqlite":
        return (
            sqlite_insert(db.action_effect_receipts)
            .values(**values)
            .on_conflict_do_nothing()
            .returning(db.action_effect_receipts.c.id)
        )
    return insert(db.action_effect_receipts).values(**values).returning(db.action_effect_receipts.c.id)


def _record_values(record: ActionEffectReceiptRecord) -> dict[str, object]:
    return {
        "id": record.receipt_id,
        "tenant_id": record.tenant_id,
        "action_run_id": record.action_run_id,
        "effect_id": record.effect_id,
        "phase": record.phase,
        "effect_kind": record.effect_kind,
        "target_ref": record.target_ref,
        "status": "pending",
        "idempotency_key": record.idempotency_key,
        "attempt_count": 0,
        "max_attempts": record.max_attempts,
        "worker_id": None,
        "lease_token": None,  # nosec B105 - DB lease state, not a credential; remove if semantics change.
        "lease_expires_at": None,
        "fencing_token": 0,  # nosec B105 - monotonic DB fence, not a password; remove if semantics change.
        "heartbeat_at": None,
        "dispatch_started_at": None,
        "cancel_requested_at": None,
        "cancel_reason": None,
        "request": record.request,
        "response": None,
        "error": None,
        "retry_at": None,
        "external_execution_id": None,
        "outbox_event_id": record.outbox_event_id,
        "created_at": record.created_at,
        "updated_at": record.created_at,
        "completed_at": None,
        "reconciled_at": None,
        "reconciled_by_user_id": None,
        "reconciliation": None,
    }


def _insert_operation_or_ignore(transaction: Any, record: ActionEffectOperationRecord) -> Any:
    values = {
        "id": record.operation_id,
        "tenant_id": record.tenant_id,
        "actor_user_id": record.actor_user_id,
        "receipt_id": record.receipt_id,
        "operation": record.operation,
        "idempotency_key": record.idempotency_key,
        "request_fingerprint": record.request_fingerprint,
        "response_json": record.response_json,
        "created_at": record.created_at,
    }
    dialect = transaction.dialect.name
    if dialect == "postgresql":
        return (
            postgres_insert(db.action_effect_operation_requests)
            .values(**values)
            .on_conflict_do_nothing()
            .returning(db.action_effect_operation_requests.c.id)
        )
    if dialect == "sqlite":
        return (
            sqlite_insert(db.action_effect_operation_requests)
            .values(**values)
            .on_conflict_do_nothing()
            .returning(db.action_effect_operation_requests.c.id)
        )
    return (
        insert(db.action_effect_operation_requests).values(**values).returning(db.action_effect_operation_requests.c.id)
    )
