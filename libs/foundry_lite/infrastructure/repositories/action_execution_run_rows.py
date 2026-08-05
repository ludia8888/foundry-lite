"""Action run and step persistence operations."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, desc, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from foundry_lite.application.action_async_execution_types import (
    ActionAsyncRunRecord,
    ActionAsyncRunRow,
    ActionRunStepRecord,
    ActionRunStepRow,
)
from foundry_lite.application.state_transitions import StatusTransition
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.status_cas import cas_status_update


def insert_run(
    transaction: Any,
    record: ActionAsyncRunRecord,
    steps: tuple[ActionRunStepRecord, ...],
) -> ActionAsyncRunRow | None:
    inserted_id = transaction.execute(_run_insert_or_ignore(transaction, record)).scalar_one_or_none()
    if inserted_id is None:
        return None
    for step in steps:
        transaction.execute(insert(db.action_run_steps).values(tenant_id=step.tenant_id, **_step_values(step)))
    return required_run(transaction, record.tenant_id, record.run_id)


def run_by_id(transaction: Any, tenant_id: str, run_id: str) -> ActionAsyncRunRow | None:
    row = (
        transaction.execute(
            select(db.action_runs).where(and_(db.action_runs.c.tenant_id == tenant_id, db.action_runs.c.id == run_id))
        )
        .mappings()
        .first()
    )
    return cast(ActionAsyncRunRow, dict(row)) if row else None


def run_by_idempotency_key(
    transaction: Any,
    tenant_id: str,
    action_type_id: str,
    actor_user_id: str,
    idempotency_key: str,
) -> ActionAsyncRunRow | None:
    row = (
        transaction.execute(
            select(db.action_runs).where(
                and_(
                    db.action_runs.c.tenant_id == tenant_id,
                    db.action_runs.c.action_type_id == action_type_id,
                    db.action_runs.c.actor_user_id == actor_user_id,
                    db.action_runs.c.idempotency_key == idempotency_key,
                )
            )
        )
        .mappings()
        .first()
    )
    return cast(ActionAsyncRunRow, dict(row)) if row else None


def list_runs(
    transaction: Any,
    tenant_id: str,
    before_created_at: str | None,
    before_run_id: str | None,
    limit: int,
) -> list[ActionAsyncRunRow]:
    # The Action DB is the canonical ledger for both bounded synchronous edits
    # and Temporal-backed runs. A public history that filters to async rows
    # makes successful low-latency Actions disappear from monitoring/revert UI.
    conditions = [db.action_runs.c.tenant_id == tenant_id]
    if before_created_at is not None and before_run_id is not None:
        conditions.append(
            or_(
                db.action_runs.c.created_at < before_created_at,
                and_(db.action_runs.c.created_at == before_created_at, db.action_runs.c.id < before_run_id),
            )
        )
    rows = (
        transaction.execute(
            select(db.action_runs)
            .where(and_(*conditions))
            .order_by(desc(db.action_runs.c.created_at), desc(db.action_runs.c.id))
            .limit(limit)
        )
        .mappings()
        .all()
    )
    return [cast(ActionAsyncRunRow, dict(row)) for row in rows]


def steps_for_run(transaction: Any, tenant_id: str, run_id: str) -> list[ActionRunStepRow]:
    rows = (
        transaction.execute(
            select(db.action_run_steps)
            .where(
                and_(
                    db.action_run_steps.c.tenant_id == tenant_id,
                    db.action_run_steps.c.run_id == run_id,
                )
            )
            .order_by(db.action_run_steps.c.created_at, db.action_run_steps.c.step_key)
        )
        .mappings()
        .all()
    )
    return [cast(ActionRunStepRow, dict(row)) for row in rows]


def update_dispatch(
    transaction: Any,
    *,
    tenant_id: str,
    run_id: str,
    workflow_run_id: str,
    dispatch_status: str,
    dispatch_error: dict[str, object] | None,
) -> ActionAsyncRunRow | None:
    result = transaction.execute(
        update(db.action_runs)
        .where(
            and_(
                db.action_runs.c.tenant_id == tenant_id,
                db.action_runs.c.id == run_id,
                db.action_runs.c.execution_mode == "async",
                db.action_runs.c.dispatch_status.in_(("pending", "unknown")),
            )
        )
        .values(
            workflow_run_id=workflow_run_id,
            dispatch_status=dispatch_status,
            dispatch_attempt_count=db.action_runs.c.dispatch_attempt_count + 1,
            dispatch_error=dispatch_error,
        )
    )
    return run_by_id(transaction, tenant_id, run_id) if result.rowcount else None


def pending_dispatches(transaction: Any, tenant_id: str, limit: int) -> list[ActionAsyncRunRow]:
    rows = (
        transaction.execute(
            select(db.action_runs)
            .where(
                and_(
                    db.action_runs.c.tenant_id == tenant_id,
                    db.action_runs.c.status == "queued",
                    db.action_runs.c.dispatch_status.in_(("pending", "unknown")),
                )
            )
            .order_by(db.action_runs.c.created_at, db.action_runs.c.id)
            .limit(limit)
        )
        .mappings()
        .all()
    )
    return [cast(ActionAsyncRunRow, dict(row)) for row in rows]


def cancelling_runs(transaction: Any, tenant_id: str, limit: int) -> list[ActionAsyncRunRow]:
    rows = (
        transaction.execute(
            select(db.action_runs)
            .where(
                and_(
                    db.action_runs.c.tenant_id == tenant_id,
                    db.action_runs.c.execution_mode == "async",
                    db.action_runs.c.status == "cancelling",
                )
            )
            .order_by(db.action_runs.c.cancel_requested_at, db.action_runs.c.id)
            .limit(limit)
        )
        .mappings()
        .all()
    )
    return [cast(ActionAsyncRunRow, dict(row)) for row in rows]


def transition_run(
    transaction: Any,
    *,
    tenant_id: str,
    run_id: str,
    transition: StatusTransition,
    changed_at: str,
    error: dict[str, object] | None,
    result: dict[str, object] | None,
) -> ActionAsyncRunRow | None:
    values: dict[str, object] = {"updated_at": changed_at, "error": error}
    if transition.to_status == "running":
        values["started_at"] = changed_at
    if transition.to_status in {"succeeded", "failed", "cancelled", "conflict", "outcome_unknown"}:
        values["completed_at"] = changed_at
    if result is not None:
        values["result"] = result
    updated = cas_status_update(
        transaction, db.action_runs, tenant_id=tenant_id, row_id=run_id, transition=transition, values=values
    )
    return run_by_id(transaction, tenant_id, run_id) if updated else None


def required_run(transaction: Any, tenant_id: str, run_id: str) -> ActionAsyncRunRow:
    row = run_by_id(transaction, tenant_id, run_id)
    if row is None:
        raise RuntimeError("action run disappeared during transaction")
    return row


def _run_values(record: ActionAsyncRunRecord) -> dict[str, object]:
    return {
        "id": record.run_id,
        "tenant_id": record.tenant_id,
        "action_type_id": record.action_type_id,
        "action_type_api_name": record.action_api_name,
        "actor_user_id": record.actor_user_id,
        "target_object_type_id": record.target_object_type_id,
        "target_object_type_api_name": record.target_object_type,
        "target_object_id": record.target_object_id,
        "expected_object_version": record.expected_object_version,
        "parameters": record.parameters,
        "status": "queued",
        "idempotency_key": record.idempotency_key,
        "request_fingerprint": record.request_fingerprint,
        "result": None,
        "error": None,
        "external_writeback_uri": None,
        "definition_version": record.definition_version,
        "plan_hash": record.plan_hash,
        "execution_plan": record.execution_plan,
        "execution_mode": "async",
        "workflow_run_id": None,
        "dispatch_status": "pending",
        "dispatch_attempt_count": 0,
        "dispatch_error": None,
        "event_sequence": 0,
        "cancel_requested_at": None,
        "cancel_reason": None,
        "cancel_idempotency_key": None,
        "cancel_request_fingerprint": None,
        "started_at": None,
        "updated_at": record.created_at,
        "created_at": record.created_at,
        "completed_at": None,
    }


def _run_insert_or_ignore(transaction: Any, record: ActionAsyncRunRecord) -> Any:
    values = _run_values(record)
    conflict = ["tenant_id", "action_type_id", "actor_user_id", "idempotency_key"]
    if transaction.dialect.name == "postgresql":
        return (
            postgres_insert(db.action_runs)
            .values(**values)
            .on_conflict_do_nothing(index_elements=conflict)
            .returning(db.action_runs.c.id)
        )
    if transaction.dialect.name == "sqlite":
        return (
            sqlite_insert(db.action_runs)
            .values(**values)
            .on_conflict_do_nothing(index_elements=conflict)
            .returning(db.action_runs.c.id)
        )
    return insert(db.action_runs).values(**values).returning(db.action_runs.c.id)


def _step_values(record: ActionRunStepRecord) -> dict[str, object]:
    return {
        "id": record.step_id,
        "run_id": record.run_id,
        "step_key": record.step_key,
        "step_kind": record.step_kind,
        "status": "pending",
        "attempt_count": 0,
        "input_manifest": record.input_manifest,
        "output_manifest": {},
        "error": None,
        "started_at": None,
        "completed_at": None,
        "created_at": record.created_at,
        "updated_at": record.created_at,
    }
