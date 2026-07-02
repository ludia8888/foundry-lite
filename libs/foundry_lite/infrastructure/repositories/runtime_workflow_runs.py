"""Workflow run ledger operations (transaction-scoped).

Extracted verbatim from SqlAlchemyRuntimeRepository, which delegates here;
every operation receives the caller's transaction, so these are stateless."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, insert, select
from sqlalchemy.exc import IntegrityError

import foundry_lite.infrastructure.schema as db
from foundry_lite.application.ports import RuntimeJsonObject, RuntimeRow
from foundry_lite.application.ports.transaction_context import WORKFLOW_RUN_LEASE_RUNNING, StatusTransition
from foundry_lite.application.ports.workflow_adapter import WorkflowLedgerStatus, WorkflowRunRecord, WorkflowRunRow
from foundry_lite.infrastructure.repositories.runtime_workflow_lease import (
    _workflow_lease_attempts,
    _workflow_lease_claim_values,
    _workflow_lease_claimable,
    _workflow_lease_matches,
    _workflow_lease_release_transition,
)
from foundry_lite.infrastructure.repositories.status_cas import cas_status_update


def workflow_run_by_id(*, transaction: Any, tenant_id: str, workflow_run_id: str) -> WorkflowRunRow | None:
    row = (
        transaction.execute(
            select(db.workflow_runs).where(
                and_(db.workflow_runs.c.tenant_id == tenant_id, db.workflow_runs.c.id == workflow_run_id)
            )
        )
        .mappings()
        .first()
    )
    return cast(WorkflowRunRow, dict(row)) if row else None


def workflow_run_by_idempotency(
    *,
    transaction: Any,
    tenant_id: str,
    workflow_name: str,
    idempotency_key: str,
) -> WorkflowRunRow | None:
    row = (
        transaction.execute(
            select(db.workflow_runs).where(
                and_(
                    db.workflow_runs.c.tenant_id == tenant_id,
                    db.workflow_runs.c.workflow_name == workflow_name,
                    db.workflow_runs.c.idempotency_key == idempotency_key,
                )
            )
        )
        .mappings()
        .first()
    )
    return cast(WorkflowRunRow, dict(row)) if row else None


def insert_workflow_run_or_get_existing(*, transaction: Any, record: WorkflowRunRecord) -> WorkflowRunRow | None:
    savepoint = transaction.begin_nested()
    try:
        transaction.execute(
            insert(db.workflow_runs).values(
                id=record.workflow_run_id,
                tenant_id=record.tenant_id,
                workflow_name=record.workflow_name,
                workflow_profile=record.workflow_profile,
                status=record.status,
                idempotency_key=record.idempotency_key,
                request_fingerprint=record.request_fingerprint,
                input=dict(record.input),
                output=dict(record.output),
                error=dict(record.error) if record.error is not None else None,
                dataset_id=record.dataset_id,
                audit_event_id=record.audit_event_id,
                attempts=record.attempts,
                created_at=record.created_at,
                started_at=record.started_at,
                completed_at=record.completed_at,
            )
        )
    except IntegrityError:
        savepoint.rollback()
        existing = workflow_run_by_idempotency(
            transaction=transaction,
            tenant_id=record.tenant_id,
            workflow_name=record.workflow_name,
            idempotency_key=record.idempotency_key,
        )
        if existing is not None:
            return existing
        raise
    savepoint.commit()
    return None


def update_workflow_run_status(
    *,
    transaction: Any,
    tenant_id: str,
    workflow_run_id: str,
    transition: StatusTransition,
    output: RuntimeJsonObject,
    error: RuntimeJsonObject | None,
    started_at: str | None,
    completed_at: str | None,
) -> WorkflowRunRow | None:
    values: dict[str, object] = {"output": dict(output), "error": dict(error) if error is not None else None}
    if started_at is not None:
        values["started_at"] = started_at
    if completed_at is not None:
        values["completed_at"] = completed_at
    if transition.to_status == "starting":
        values["attempts"] = db.workflow_runs.c.attempts + 1
    updated = cas_status_update(
        transaction,
        db.workflow_runs,
        tenant_id=tenant_id,
        row_id=workflow_run_id,
        transition=transition,
        values=values,
    )
    if not updated:
        return None
    return workflow_run_by_id(transaction=transaction, tenant_id=tenant_id, workflow_run_id=workflow_run_id)


def claim_workflow_run_lease(
    *,
    transaction: Any,
    record: WorkflowRunRecord,
    lease_owner_id: str,
    now: str,
) -> WorkflowRunRow | None:
    existing = insert_workflow_run_or_get_existing(transaction=transaction, record=record)
    row = existing or workflow_run_by_id(
        transaction=transaction,
        tenant_id=record.tenant_id,
        workflow_run_id=record.workflow_run_id,
    )
    if row is None:
        return None
    if existing is None:
        return row
    if not _workflow_lease_claimable(row, lease_owner_id, now):
        return None
    attempts = _workflow_lease_attempts(row, lease_owner_id)
    updated = cas_status_update(
        transaction,
        db.workflow_runs,
        tenant_id=record.tenant_id,
        row_id=row["id"],
        transition=WORKFLOW_RUN_LEASE_RUNNING,
        values=_workflow_lease_claim_values(record, attempts),
    )
    if not updated:
        return None
    return workflow_run_by_id(transaction=transaction, tenant_id=record.tenant_id, workflow_run_id=row["id"])


def release_workflow_run_lease(
    *,
    transaction: Any,
    tenant_id: str,
    workflow_run_id: str,
    lease_owner_id: str,
    lease_token: str,
    status: WorkflowLedgerStatus,
    output: RuntimeRow,
    error: RuntimeRow | None,
    completed_at: str,
) -> WorkflowRunRow | None:
    row = workflow_run_by_id(
        transaction=transaction,
        tenant_id=tenant_id,
        workflow_run_id=workflow_run_id,
    )
    if row is None or not _workflow_lease_matches(row, lease_owner_id, lease_token):
        return None
    # The Python guard above is only a fast pre-check: the row can be taken over
    # between that read and this CAS. Fence the UPDATE on the lease owner/token so
    # a superseded worker cannot overwrite the live owner's status and cursor.
    lease = db.workflow_runs.c.output["workerLease"]
    updated = cas_status_update(
        transaction,
        db.workflow_runs,
        tenant_id=tenant_id,
        row_id=workflow_run_id,
        transition=_workflow_lease_release_transition(status),
        values={
            "output": dict(output),
            "error": dict(error) if error is not None else None,
            "completed_at": completed_at,
        },
        conditions=[
            lease["ownerId"].as_string() == lease_owner_id,
            lease["leaseToken"].as_string() == lease_token,
        ],
    )
    if not updated:
        return None
    return workflow_run_by_id(transaction=transaction, tenant_id=tenant_id, workflow_run_id=workflow_run_id)


def link_workflow_audit_event(
    *,
    transaction: Any,
    tenant_id: str,
    workflow_run_id: str,
    audit_event_id: str,
) -> WorkflowRunRow | None:
    result = transaction.execute(
        db.workflow_runs.update()
        .where(and_(db.workflow_runs.c.tenant_id == tenant_id, db.workflow_runs.c.id == workflow_run_id))
        .values(audit_event_id=audit_event_id)
    )
    if result.rowcount != 1:
        return None
    return workflow_run_by_id(transaction=transaction, tenant_id=tenant_id, workflow_run_id=workflow_run_id)
