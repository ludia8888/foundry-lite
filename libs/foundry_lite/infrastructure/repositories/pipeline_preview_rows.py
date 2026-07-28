"""Tenant-fenced persistence helpers for recoverable Pipeline preview runs."""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, insert, or_, select
from sqlalchemy.exc import IntegrityError

from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelinePreviewRunRecord,
    PipelinePreviewRunRow,
)
from foundry_lite.application.state_transitions import (
    PIPELINE_PREVIEW_CANCEL_REQUESTED,
    PIPELINE_PREVIEW_CANCELLED,
    PIPELINE_PREVIEW_FAILED,
    PIPELINE_PREVIEW_RUNNING,
    PIPELINE_PREVIEW_SUCCEEDED,
    StatusTransition,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.status_cas import (
    cas_status_guarded_update,
    cas_status_update,
)


def insert_preview(transaction: Any, record: PipelinePreviewRunRecord) -> PipelinePreviewRunRow:
    """Insert a preview or return the row owning the same idempotency key."""
    savepoint = transaction.begin_nested()
    try:
        transaction.execute(
            insert(db.pipeline_preview_runs).values(
                tenant_id=record.tenant_id,
                **_preview_insert_values(record),
            )
        )
    except IntegrityError:
        savepoint.rollback()
        existing = preview_by_idempotency_key(transaction, record.tenant_id, record.idempotency_key)
        if existing is not None:
            return existing
        raise
    savepoint.commit()
    row = preview_by_id(transaction, record.tenant_id, record.preview_run_id)
    if row is None:
        raise RuntimeError("inserted pipeline preview row could not be read back")
    return row


def preview_by_id(
    transaction: Any,
    tenant_id: str,
    preview_run_id: str,
) -> PipelinePreviewRunRow | None:
    """Read one tenant-owned preview by its durable identifier."""
    row = (
        transaction.execute(
            select(db.pipeline_preview_runs).where(
                and_(
                    db.pipeline_preview_runs.c.tenant_id == tenant_id,
                    db.pipeline_preview_runs.c.id == preview_run_id,
                )
            )
        )
        .mappings()
        .first()
    )
    return _row(row)


def preview_by_idempotency_key(
    transaction: Any,
    tenant_id: str,
    idempotency_key: str,
) -> PipelinePreviewRunRow | None:
    """Read one tenant-owned preview by its idempotency key."""
    row = (
        transaction.execute(
            select(db.pipeline_preview_runs).where(
                and_(
                    db.pipeline_preview_runs.c.tenant_id == tenant_id,
                    db.pipeline_preview_runs.c.idempotency_key == idempotency_key,
                )
            )
        )
        .mappings()
        .first()
    )
    return _row(row)


def claim_preview(
    transaction: Any,
    tenant_id: str,
    preview_run_id: str,
    started_at: str,
    execution_lease_token: str,
    execution_lease_expires_at: str,
    execution_heartbeat_at: str,
) -> PipelinePreviewRunRow | None:
    """Claim a queued preview and attach the caller's renewable lease."""
    updated = cas_status_update(
        transaction,
        db.pipeline_preview_runs,
        tenant_id=tenant_id,
        row_id=preview_run_id,
        transition=PIPELINE_PREVIEW_RUNNING,
        values=_lease_values(
            execution_lease_token,
            execution_lease_expires_at,
            execution_heartbeat_at,
            started_at=started_at,
        ),
    )
    return preview_by_id(transaction, tenant_id, preview_run_id) if updated else None


def reclaim_expired_preview(
    transaction: Any,
    tenant_id: str,
    preview_run_id: str,
    reclaim_before: str,
    execution_lease_token: str,
    execution_lease_expires_at: str,
    execution_heartbeat_at: str,
) -> PipelinePreviewRunRow | None:
    """Replace an expired running-preview lease using guarded compare-and-set."""
    lease_expiry = db.pipeline_preview_runs.c.execution_lease_expires_at
    updated = cas_status_guarded_update(
        transaction,
        db.pipeline_preview_runs,
        tenant_id=tenant_id,
        row_id=preview_run_id,
        allowed_statuses=("RUNNING",),
        conditions=(or_(lease_expiry.is_(None), lease_expiry <= reclaim_before),),
        values=_lease_values(execution_lease_token, execution_lease_expires_at, execution_heartbeat_at),
    )
    return preview_by_id(transaction, tenant_id, preview_run_id) if updated else None


def renew_preview_execution_lease(
    transaction: Any,
    tenant_id: str,
    preview_run_id: str,
    execution_lease_token: str,
    execution_lease_expires_at: str,
    execution_heartbeat_at: str,
) -> PipelinePreviewRunRow | None:
    """Renew a lease only while the same executor still owns the preview."""
    updated = cas_status_guarded_update(
        transaction,
        db.pipeline_preview_runs,
        tenant_id=tenant_id,
        row_id=preview_run_id,
        allowed_statuses=("RUNNING", "CANCEL_REQUESTED"),
        conditions=(db.pipeline_preview_runs.c.execution_lease_token == execution_lease_token,),
        values={
            "execution_lease_expires_at": execution_lease_expires_at,
            "execution_heartbeat_at": execution_heartbeat_at,
        },
    )
    return preview_by_id(transaction, tenant_id, preview_run_id) if updated else None


def recoverable_previews(
    transaction: Any,
    tenant_id: str,
    as_of: str,
    limit: int,
) -> list[PipelinePreviewRunRow]:
    """List bounded queued, cancel-requested, and expired running previews."""
    lease_expiry = db.pipeline_preview_runs.c.execution_lease_expires_at
    rows = (
        transaction.execute(
            select(db.pipeline_preview_runs)
            .where(
                and_(
                    db.pipeline_preview_runs.c.tenant_id == tenant_id,
                    or_(
                        db.pipeline_preview_runs.c.status.in_(("QUEUED", "CANCEL_REQUESTED")),
                        and_(
                            db.pipeline_preview_runs.c.status == "RUNNING",
                            or_(lease_expiry.is_(None), lease_expiry <= as_of),
                        ),
                    ),
                ),
            )
            .order_by(db.pipeline_preview_runs.c.created_at, db.pipeline_preview_runs.c.id)
            .limit(limit)
        )
        .mappings()
        .all()
    )
    return [_cast_row(row) for row in rows]


def complete_preview_success(
    transaction: Any,
    tenant_id: str,
    preview_run_id: str,
    execution_lease_token: str,
    outputs: list[dict[str, object]],
    artifacts: list[dict[str, object]],
    completed_at: str,
) -> PipelinePreviewRunRow | None:
    """Complete success or atomically honor a concurrent cancellation request."""
    values = _terminal_values(outputs, artifacts, None, completed_at)
    condition = (db.pipeline_preview_runs.c.execution_lease_token == execution_lease_token,)
    succeeded = cas_status_update(
        transaction,
        db.pipeline_preview_runs,
        tenant_id=tenant_id,
        row_id=preview_run_id,
        transition=PIPELINE_PREVIEW_SUCCEEDED,
        conditions=condition,
        values=values,
    )
    cancelled = False
    if not succeeded:
        cancelled = cas_status_update(
            transaction,
            db.pipeline_preview_runs,
            tenant_id=tenant_id,
            row_id=preview_run_id,
            transition=PIPELINE_PREVIEW_CANCELLED,
            conditions=condition,
            values=values,
        )
    return preview_by_id(transaction, tenant_id, preview_run_id) if succeeded or cancelled else None


def complete_preview_failure(
    transaction: Any,
    tenant_id: str,
    preview_run_id: str,
    execution_lease_token: str,
    error: dict[str, object],
    completed_at: str,
) -> PipelinePreviewRunRow | None:
    """Complete failure or atomically honor a cancellation requested first."""
    condition = (db.pipeline_preview_runs.c.execution_lease_token == execution_lease_token,)
    failed = cas_status_update(
        transaction,
        db.pipeline_preview_runs,
        tenant_id=tenant_id,
        row_id=preview_run_id,
        transition=PIPELINE_PREVIEW_FAILED,
        conditions=condition,
        values=_terminal_values([], [], error, completed_at),
    )
    cancelled = False
    if not failed:
        cancelled = cas_status_update(
            transaction,
            db.pipeline_preview_runs,
            tenant_id=tenant_id,
            row_id=preview_run_id,
            transition=PIPELINE_PREVIEW_CANCELLED,
            conditions=condition,
            values=_terminal_values([], [], None, completed_at),
        )
    return preview_by_id(transaction, tenant_id, preview_run_id) if failed or cancelled else None


def update_preview_terminal(
    transaction: Any,
    tenant_id: str,
    preview_run_id: str,
    transition: StatusTransition,
    outputs: list[dict[str, object]],
    artifacts: list[dict[str, object]],
    error: dict[str, object] | None,
    completed_at: str,
    execution_lease_token: str | None,
) -> PipelinePreviewRunRow | None:
    """Apply a fenced terminal transition and return the updated preview."""
    conditions = (
        (db.pipeline_preview_runs.c.execution_lease_token == execution_lease_token,)
        if execution_lease_token is not None
        else ()
    )
    updated = cas_status_update(
        transaction,
        db.pipeline_preview_runs,
        tenant_id=tenant_id,
        row_id=preview_run_id,
        transition=transition,
        conditions=conditions,
        values=_terminal_values(outputs, artifacts, error, completed_at),
    )
    return preview_by_id(transaction, tenant_id, preview_run_id) if updated else None


def request_preview_cancel(
    transaction: Any,
    tenant_id: str,
    preview_run_id: str,
    requested_at: str,
) -> PipelinePreviewRunRow | None:
    """Request cancellation without overwriting an already terminal preview."""
    updated = cas_status_update(
        transaction,
        db.pipeline_preview_runs,
        tenant_id=tenant_id,
        row_id=preview_run_id,
        transition=PIPELINE_PREVIEW_CANCEL_REQUESTED,
        values={"cancel_requested_at": requested_at},
    )
    return preview_by_id(transaction, tenant_id, preview_run_id) if updated else None


def _preview_insert_values(record: PipelinePreviewRunRecord) -> dict[str, object]:
    return {
        "id": record.preview_run_id,
        "pipeline_id": record.pipeline_id,
        "branch_id": record.branch_id,
        "status": "QUEUED",
        "graph": record.graph,
        "graph_fingerprint": record.graph_fingerprint,
        "target_node_id": record.target_node_id,
        "limits": record.limits,
        "outputs": [],
        "artifacts": [],
        "idempotency_key": record.idempotency_key,
        "request_fingerprint": record.request_fingerprint,
        "is_commit_forbidden": True,
        "execution_context": record.execution_context,
        "execution_lease_token": None,  # nosec B105 -- Lease ownership state, not a credential.
        "execution_lease_expires_at": None,
        "execution_heartbeat_at": None,
        "cancel_requested_at": None,
        "error": None,
        "created_by": record.created_by,
        "created_at": record.created_at,
        "started_at": None,
        "completed_at": None,
    }


def _lease_values(
    token: str,
    expires_at: str,
    heartbeat_at: str,
    *,
    started_at: str | None = None,
) -> dict[str, object]:
    values: dict[str, object] = {
        "execution_lease_token": token,
        "execution_lease_expires_at": expires_at,
        "execution_heartbeat_at": heartbeat_at,
    }
    if started_at is not None:
        values["started_at"] = started_at
    return values


def _terminal_values(
    outputs: list[dict[str, object]],
    artifacts: list[dict[str, object]],
    error: dict[str, object] | None,
    completed_at: str,
) -> dict[str, object]:
    return {
        "outputs": outputs,
        "artifacts": artifacts,
        "error": error,
        "completed_at": completed_at,
    }


def _row(value: Any) -> PipelinePreviewRunRow | None:
    return None if value is None else _cast_row(value)


def _cast_row(value: Any) -> PipelinePreviewRunRow:
    return dict(value)  # type: ignore[return-value]
