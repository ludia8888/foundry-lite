"""Lease, takeover, heartbeat, and fencing operations for Action steps."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, desc, insert, select, update
from sqlalchemy.sql.elements import ColumnElement

from foundry_lite.application.action_async_execution_types import (
    ActionRunStepRow,
    ActionStepAttemptClaim,
    ActionStepAttemptRow,
)
from foundry_lite.application.state_transitions import (
    ACTION_ATTEMPT_CANCELLED,
    ACTION_ATTEMPT_FAILED,
    ACTION_ATTEMPT_LOST,
    ACTION_ATTEMPT_SUCCEEDED,
    ACTION_STEP_CANCELLED,
    ACTION_STEP_FAILED,
    ACTION_STEP_RETRY_WAIT,
    ACTION_STEP_RUNNING,
    ACTION_STEP_SUCCEEDED,
    StatusTransition,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.status_cas import cas_status_update


def claim_step(transaction: Any, claim: ActionStepAttemptClaim) -> ActionStepAttemptRow | None:
    step = _locked_step(transaction, claim)
    if step is None:
        return None
    latest = _latest_attempt(transaction, claim.tenant_id, str(step["id"]))
    if latest is not None and not _can_takeover(latest, claim.claimed_at, claim.is_cancellation):
        return None
    if latest is not None and latest["status"] == "running":
        _mark_lost(transaction, latest, claim.claimed_at)
    number = int(latest["attempt_number"]) + 1 if latest else 1
    fencing = int(latest["fencing_token"]) + 1 if latest else 1
    _mark_step_running(transaction, step, claim.claimed_at, number)
    attempt_id = f"{step['id']}:attempt:{number}"
    transaction.execute(
        insert(db.action_step_attempts).values(
            id=attempt_id,
            tenant_id=claim.tenant_id,
            step_id=step["id"],
            attempt_number=number,
            status="running",
            worker_id=claim.worker_id,
            lease_token=claim.lease_token,
            lease_expires_at=claim.lease_expires_at,
            fencing_token=fencing,
            heartbeat_at=claim.claimed_at,
            retry_at=None,
            error_kind=None,
            external_execution_id=None,
            input_manifest=claim.input_manifest,
            output_manifest={},
            error=None,
            started_at=claim.claimed_at,
            completed_at=None,
        )
    )
    return required_attempt(transaction, claim.tenant_id, attempt_id)


def heartbeat_attempt(
    transaction: Any,
    *,
    tenant_id: str,
    attempt_id: str,
    worker_id: str,
    lease_token: str,
    fencing_token: int,
    lease_expires_at: str,
    heartbeat_at: str,
) -> ActionStepAttemptRow | None:
    result = transaction.execute(
        update(db.action_step_attempts)
        .where(
            and_(
                db.action_step_attempts.c.tenant_id == tenant_id,
                *_owner_conditions(tenant_id, attempt_id, worker_id, lease_token, fencing_token),
                db.action_step_attempts.c.lease_expires_at > heartbeat_at,
            )
        )
        .values(lease_expires_at=lease_expires_at, heartbeat_at=heartbeat_at)
    )
    return required_attempt(transaction, tenant_id, attempt_id) if result.rowcount else None


def lock_attempt_owner(
    transaction: Any,
    *,
    tenant_id: str,
    attempt_id: str,
    worker_id: str,
    lease_token: str,
    fencing_token: int,
    owned_at: str,
) -> ActionStepAttemptRow | None:
    row = (
        transaction.execute(
            select(db.action_step_attempts)
            .where(
                and_(
                    *_owner_conditions(tenant_id, attempt_id, worker_id, lease_token, fencing_token),
                    db.action_step_attempts.c.lease_expires_at >= owned_at,
                )
            )
            .with_for_update()
        )
        .mappings()
        .first()
    )
    return cast(ActionStepAttemptRow, dict(row)) if row else None


def complete_attempt(
    transaction: Any,
    *,
    tenant_id: str,
    attempt_id: str,
    worker_id: str,
    lease_token: str,
    fencing_token: int,
    status: str,
    output_manifest: dict[str, object],
    error: dict[str, object] | None,
    error_kind: str | None,
    completed_at: str,
    retry_at: str | None,
    external_execution_id: str | None,
) -> ActionStepAttemptRow | None:
    if status not in {"succeeded", "failed", "cancelled"}:
        raise ValueError(f"unsupported Action attempt terminal status: {status}")
    updated = cas_status_update(
        transaction,
        db.action_step_attempts,
        tenant_id=tenant_id,
        row_id=attempt_id,
        transition=_attempt_transition(status),
        conditions=(
            db.action_step_attempts.c.worker_id == worker_id,
            db.action_step_attempts.c.lease_token == lease_token,
            db.action_step_attempts.c.fencing_token == fencing_token,
            db.action_step_attempts.c.lease_expires_at >= completed_at,
        ),
        values={
            "output_manifest": output_manifest,
            "error": error,
            "error_kind": error_kind,
            "retry_at": retry_at,
            "external_execution_id": external_execution_id,
            "heartbeat_at": completed_at,
            "completed_at": completed_at,
        },
    )
    if not updated:
        return None
    attempt = required_attempt(transaction, tenant_id, attempt_id)
    _complete_step(transaction, attempt, status, output_manifest, error, completed_at, retry_at)
    return attempt


def required_attempt(transaction: Any, tenant_id: str, attempt_id: str) -> ActionStepAttemptRow:
    row = (
        transaction.execute(
            select(db.action_step_attempts).where(
                and_(
                    db.action_step_attempts.c.tenant_id == tenant_id,
                    db.action_step_attempts.c.id == attempt_id,
                )
            )
        )
        .mappings()
        .one()
    )
    return cast(ActionStepAttemptRow, dict(row))


def attempts_for_run(transaction: Any, tenant_id: str, run_id: str) -> list[ActionStepAttemptRow]:
    rows = (
        transaction.execute(
            select(db.action_step_attempts)
            .select_from(
                db.action_step_attempts.join(
                    db.action_run_steps, db.action_step_attempts.c.step_id == db.action_run_steps.c.id
                )
            )
            .where(
                and_(
                    db.action_step_attempts.c.tenant_id == tenant_id,
                    db.action_run_steps.c.tenant_id == tenant_id,
                    db.action_run_steps.c.run_id == run_id,
                )
            )
            .order_by(db.action_run_steps.c.created_at, db.action_step_attempts.c.attempt_number)
        )
        .mappings()
        .all()
    )
    return [cast(ActionStepAttemptRow, dict(row)) for row in rows]


def _locked_step(transaction: Any, claim: ActionStepAttemptClaim) -> ActionRunStepRow | None:
    row = (
        transaction.execute(
            select(db.action_run_steps)
            .where(
                and_(
                    db.action_run_steps.c.tenant_id == claim.tenant_id,
                    db.action_run_steps.c.run_id == claim.run_id,
                    db.action_run_steps.c.step_key == claim.step_key,
                    db.action_run_steps.c.status.in_(("pending", "running", "retry_wait")),
                )
            )
            .with_for_update()
        )
        .mappings()
        .first()
    )
    return cast(ActionRunStepRow, dict(row)) if row else None


def _latest_attempt(transaction: Any, tenant_id: str, step_id: str) -> ActionStepAttemptRow | None:
    row = (
        transaction.execute(
            select(db.action_step_attempts)
            .where(
                and_(
                    db.action_step_attempts.c.tenant_id == tenant_id,
                    db.action_step_attempts.c.step_id == step_id,
                )
            )
            .order_by(desc(db.action_step_attempts.c.attempt_number))
            .limit(1)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    return cast(ActionStepAttemptRow, dict(row)) if row else None


def _owner_conditions(
    tenant_id: str, attempt_id: str, worker_id: str, lease_token: str, fencing_token: int
) -> tuple[ColumnElement[bool], ...]:
    return (
        db.action_step_attempts.c.tenant_id == tenant_id,
        db.action_step_attempts.c.id == attempt_id,
        db.action_step_attempts.c.status == "running",
        db.action_step_attempts.c.worker_id == worker_id,
        db.action_step_attempts.c.lease_token == lease_token,
        db.action_step_attempts.c.fencing_token == fencing_token,
    )


def _can_takeover(attempt: ActionStepAttemptRow, claimed_at: str, is_cancellation: bool) -> bool:
    if attempt["status"] == "running":
        return attempt["lease_expires_at"] <= claimed_at
    retry_at = None if is_cancellation else attempt["retry_at"]
    return retry_at is None or retry_at <= claimed_at


def _mark_lost(transaction: Any, attempt: ActionStepAttemptRow, lost_at: str) -> None:
    cas_status_update(
        transaction,
        db.action_step_attempts,
        tenant_id=attempt["tenant_id"],
        row_id=attempt["id"],
        transition=ACTION_ATTEMPT_LOST,
        conditions=(db.action_step_attempts.c.fencing_token == attempt["fencing_token"],),
        values={
            "error_kind": "worker_lost",
            "error": {"kind": "worker_lost", "message": "Action step lease expired"},
            "completed_at": lost_at,
        },
    )


def _mark_step_running(transaction: Any, step: ActionRunStepRow, changed_at: str, attempt_number: int) -> None:
    cas_status_update(
        transaction,
        db.action_run_steps,
        tenant_id=step["tenant_id"],
        row_id=step["id"],
        transition=ACTION_STEP_RUNNING,
        conditions=(db.action_run_steps.c.attempt_count == step["attempt_count"],),
        values={
            "attempt_count": attempt_number,
            "started_at": step["started_at"] or changed_at,
            "completed_at": None,
            "updated_at": changed_at,
        },
    )


def _complete_step(
    transaction: Any,
    attempt: ActionStepAttemptRow,
    status: str,
    output_manifest: dict[str, object],
    error: dict[str, object] | None,
    changed_at: str,
    retry_at: str | None,
) -> None:
    transition = ACTION_STEP_RETRY_WAIT if retry_at else _step_transition(status)
    cas_status_update(
        transaction,
        db.action_run_steps,
        tenant_id=attempt["tenant_id"],
        row_id=attempt["step_id"],
        transition=transition,
        conditions=(db.action_run_steps.c.attempt_count == attempt["attempt_number"],),
        values={
            "output_manifest": output_manifest,
            "error": error,
            "completed_at": None if retry_at else changed_at,
            "updated_at": changed_at,
        },
    )


def _attempt_transition(status: str) -> StatusTransition:
    return {
        "succeeded": ACTION_ATTEMPT_SUCCEEDED,
        "failed": ACTION_ATTEMPT_FAILED,
        "cancelled": ACTION_ATTEMPT_CANCELLED,
    }[status]


def _step_transition(status: str) -> StatusTransition:
    return {
        "succeeded": ACTION_STEP_SUCCEEDED,
        "failed": ACTION_STEP_FAILED,
        "cancelled": ACTION_STEP_CANCELLED,
    }[status]
