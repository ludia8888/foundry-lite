"""Append-only Action run events and cancellation ledger operations."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, insert, select, update

from foundry_lite.application.action_async_execution_types import (
    ActionAsyncRunRow,
    ActionRunEventRecord,
    ActionRunEventRow,
)
from foundry_lite.application.state_transitions import ACTION_RUN_CANCELLING, ACTION_STEP_CANCEL_UNSTARTED
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.status_cas import cas_status_update, cas_status_update_many


def append_event(transaction: Any, record: ActionRunEventRecord) -> ActionRunEventRow:
    sequence = transaction.execute(
        update(db.action_runs)
        .where(
            and_(
                db.action_runs.c.tenant_id == record.tenant_id,
                db.action_runs.c.id == record.run_id,
            )
        )
        .values(event_sequence=db.action_runs.c.event_sequence + 1)
        .returning(db.action_runs.c.event_sequence)
    ).scalar_one()
    transaction.execute(
        insert(db.action_run_events).values(
            id=record.event_id,
            tenant_id=record.tenant_id,
            run_id=record.run_id,
            sequence=sequence,
            event_type=record.event_type,
            step_key=record.step_key,
            attempt_number=record.attempt_number,
            worker_id=record.worker_id,
            fencing_token=record.fencing_token,
            payload=record.payload,
            created_at=record.created_at,
        )
    )
    row = (
        transaction.execute(
            select(db.action_run_events).where(
                and_(
                    db.action_run_events.c.tenant_id == record.tenant_id,
                    db.action_run_events.c.id == record.event_id,
                )
            )
        )
        .mappings()
        .one()
    )
    return cast(ActionRunEventRow, dict(row))


def run_events(
    transaction: Any,
    tenant_id: str,
    run_id: str,
    after_sequence: int,
    limit: int,
) -> list[ActionRunEventRow]:
    rows = (
        transaction.execute(
            select(db.action_run_events)
            .where(
                and_(
                    db.action_run_events.c.tenant_id == tenant_id,
                    db.action_run_events.c.run_id == run_id,
                    db.action_run_events.c.sequence > after_sequence,
                )
            )
            .order_by(db.action_run_events.c.sequence)
            .limit(limit)
        )
        .mappings()
        .all()
    )
    return [cast(ActionRunEventRow, dict(row)) for row in rows]


def request_cancel(
    transaction: Any,
    *,
    tenant_id: str,
    run_id: str,
    idempotency_key: str,
    request_fingerprint: str,
    reason: str | None,
    requested_at: str,
) -> ActionAsyncRunRow | None:
    existing = _run(transaction, tenant_id, run_id)
    if existing is None:
        return None
    if existing["cancel_idempotency_key"] is not None:
        return existing if _same_cancel(existing, idempotency_key, request_fingerprint) else None
    if existing["status"] in _TERMINAL_STATUSES:
        return existing
    updated = cas_status_update(
        transaction,
        db.action_runs,
        tenant_id=tenant_id,
        row_id=run_id,
        transition=ACTION_RUN_CANCELLING,
        conditions=(db.action_runs.c.cancel_idempotency_key.is_(None),),
        values={
            "cancel_requested_at": requested_at,
            "cancel_reason": reason,
            "cancel_idempotency_key": idempotency_key,
            "cancel_request_fingerprint": request_fingerprint,
            "updated_at": requested_at,
        },
    )
    if not updated:
        return _run(transaction, tenant_id, run_id)
    _cancel_unstarted_steps(transaction, tenant_id, run_id, requested_at)
    return _run(transaction, tenant_id, run_id)


def _cancel_unstarted_steps(transaction: Any, tenant_id: str, run_id: str, changed_at: str) -> None:
    cas_status_update_many(
        transaction,
        db.action_run_steps,
        tenant_id=tenant_id,
        transition=ACTION_STEP_CANCEL_UNSTARTED,
        conditions=(db.action_run_steps.c.run_id == run_id,),
        values={"completed_at": changed_at, "updated_at": changed_at},
    )


def _run(transaction: Any, tenant_id: str, run_id: str) -> ActionAsyncRunRow | None:
    row = (
        transaction.execute(
            select(db.action_runs).where(
                and_(
                    db.action_runs.c.tenant_id == tenant_id,
                    db.action_runs.c.id == run_id,
                )
            )
        )
        .mappings()
        .first()
    )
    return cast(ActionAsyncRunRow, dict(row)) if row else None


def _same_cancel(row: ActionAsyncRunRow, idempotency_key: str, request_fingerprint: str) -> bool:
    return row["cancel_idempotency_key"] == idempotency_key and row["cancel_request_fingerprint"] == request_fingerprint


_TERMINAL_STATUSES = frozenset(
    {"succeeded", "failed", "cancelled", "conflict", "outcome_unknown", "compensation_required", "reconciled"}
)
