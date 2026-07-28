"""SQLAlchemy row operations for durable Pipeline scheduler state."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, insert, or_, select, update
from sqlalchemy.exc import IntegrityError

from foundry_lite.application.ports.pipeline_repository import (
    PipelineScheduleOperationRecord,
    PipelineScheduleOperationRow,
    PipelineScheduleRecord,
    PipelineScheduleRow,
)
from foundry_lite.infrastructure import schema as db


def upsert_schedule(transaction: Any, record: PipelineScheduleRecord) -> PipelineScheduleRow:
    existing = schedule_by_pipeline(transaction, record.tenant_id, record.pipeline_id)
    if existing is None:
        transaction.execute(
            insert(db.pipeline_schedules).values(
                tenant_id=record.tenant_id,
                **_new_schedule_values(record),
            )
        )
    else:
        transaction.execute(
            update(db.pipeline_schedules)
            .where(_schedule_identity(record.tenant_id, str(existing["id"])))
            .values(**_updated_schedule_values(record))
        )
    row = schedule_by_pipeline(transaction, record.tenant_id, record.pipeline_id)
    if row is None:
        raise RuntimeError("pipeline schedule row missing after upsert")
    return row


def schedule_by_pipeline(
    transaction: Any,
    tenant_id: str,
    pipeline_id: str,
) -> PipelineScheduleRow | None:
    row = (
        transaction.execute(
            select(db.pipeline_schedules).where(
                and_(
                    db.pipeline_schedules.c.tenant_id == tenant_id,
                    db.pipeline_schedules.c.pipeline_id == pipeline_id,
                )
            )
        )
        .mappings()
        .first()
    )
    return _optional_row(row, PipelineScheduleRow)


def list_due_schedules(
    transaction: Any,
    tenant_id: str,
    due_at: str,
    limit: int,
) -> list[PipelineScheduleRow]:
    claimable_lease = or_(
        db.pipeline_schedules.c.lease_expires_at.is_(None),
        db.pipeline_schedules.c.lease_expires_at <= due_at,
    )
    rows = (
        transaction.execute(
            select(db.pipeline_schedules)
            .where(
                and_(
                    db.pipeline_schedules.c.tenant_id == tenant_id,
                    db.pipeline_schedules.c.enabled.is_(True),
                    db.pipeline_schedules.c.status == "active",
                    db.pipeline_schedules.c.next_due_at.is_not(None),
                    db.pipeline_schedules.c.next_due_at <= due_at,
                    claimable_lease,
                )
            )
            .order_by(db.pipeline_schedules.c.next_due_at, db.pipeline_schedules.c.id)
            .limit(limit)
        )
        .mappings()
        .all()
    )
    return [_row(row, PipelineScheduleRow) for row in rows]


def list_schedules_needing_reconciliation(
    transaction: Any,
    tenant_id: str,
    observed_at: str,
    limit: int,
) -> list[PipelineScheduleRow]:
    rows = (
        transaction.execute(
            select(db.pipeline_schedules)
            .where(
                and_(
                    db.pipeline_schedules.c.tenant_id == tenant_id,
                    _runtime_config_is_stale(),
                    _lease_is_claimable(observed_at),
                )
            )
            .order_by(
                db.pipeline_schedules.c.enabled.desc(),
                db.pipeline_schedules.c.updated_at,
                db.pipeline_schedules.c.id,
            )
            .limit(limit)
        )
        .mappings()
        .all()
    )
    return [_row(row, PipelineScheduleRow) for row in rows]


def reconcile_schedule_runtime(
    transaction: Any,
    *,
    tenant_id: str,
    schedule_id: str,
    expected_updated_at: str,
    observed_at: str,
    values: dict[str, object],
) -> PipelineScheduleRow | None:
    result = transaction.execute(
        update(db.pipeline_schedules)
        .where(
            and_(
                db.pipeline_schedules.c.tenant_id == tenant_id,
                db.pipeline_schedules.c.id == schedule_id,
                db.pipeline_schedules.c.updated_at == expected_updated_at,
                _runtime_config_is_stale(),
                _lease_is_claimable(observed_at),
            )
        )
        .values(
            **values,
            runtime_config_updated_at=expected_updated_at,
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
        )
    )
    if not result.rowcount:
        return None
    return schedule_by_id(transaction, tenant_id, schedule_id)


def claim_due_schedule(
    transaction: Any,
    *,
    tenant_id: str,
    schedule_id: str,
    due_at: str,
    lease_owner: str,
    lease_token: str,
    lease_expires_at: str,
) -> PipelineScheduleRow | None:
    claimable_lease = or_(
        db.pipeline_schedules.c.lease_expires_at.is_(None),
        db.pipeline_schedules.c.lease_expires_at <= due_at,
    )
    result = transaction.execute(
        update(db.pipeline_schedules)
        .where(
            and_(
                db.pipeline_schedules.c.tenant_id == tenant_id,
                db.pipeline_schedules.c.id == schedule_id,
                db.pipeline_schedules.c.enabled.is_(True),
                db.pipeline_schedules.c.status == "active",
                db.pipeline_schedules.c.next_due_at.is_not(None),
                db.pipeline_schedules.c.next_due_at <= due_at,
                claimable_lease,
            )
        )
        .values(
            lease_owner=lease_owner,
            lease_token=lease_token,
            lease_expires_at=lease_expires_at,
            fencing_token=db.pipeline_schedules.c.fencing_token + 1,
        )
    )
    if not result.rowcount:
        return None
    return schedule_by_id(transaction, tenant_id, schedule_id)


def complete_schedule_tick(
    transaction: Any,
    *,
    tenant_id: str,
    schedule_id: str,
    lease_token: str,
    fencing_token: int,
    values: dict[str, object],
) -> PipelineScheduleRow | None:
    result = transaction.execute(
        update(db.pipeline_schedules)
        .where(
            and_(
                db.pipeline_schedules.c.tenant_id == tenant_id,
                db.pipeline_schedules.c.id == schedule_id,
                db.pipeline_schedules.c.lease_token == lease_token,
                db.pipeline_schedules.c.fencing_token == fencing_token,
            )
        )
        .values(**values, lease_owner=None, lease_token=None, lease_expires_at=None)
    )
    if not result.rowcount:
        return None
    return schedule_by_id(transaction, tenant_id, schedule_id)


def update_schedule_status(
    transaction: Any,
    *,
    tenant_id: str,
    pipeline_id: str,
    values: dict[str, object],
) -> PipelineScheduleRow | None:
    result = transaction.execute(
        update(db.pipeline_schedules)
        .where(
            and_(
                db.pipeline_schedules.c.tenant_id == tenant_id,
                db.pipeline_schedules.c.pipeline_id == pipeline_id,
            )
        )
        .values(**values, lease_owner=None, lease_token=None, lease_expires_at=None)
    )
    if not result.rowcount:
        return None
    return schedule_by_pipeline(transaction, tenant_id, pipeline_id)


def reserve_schedule_operation(
    transaction: Any,
    record: PipelineScheduleOperationRecord,
) -> tuple[PipelineScheduleOperationRow, bool]:
    savepoint = transaction.begin_nested()
    try:
        transaction.execute(
            insert(db.pipeline_schedule_operations).values(
                tenant_id=record.tenant_id,
                **_operation_values(record),
            )
        )
    except IntegrityError:
        savepoint.rollback()
        existing = operation_by_key(transaction, record.tenant_id, record.idempotency_key)
        if existing is None:
            raise
        return existing, False
    savepoint.commit()
    row = operation_by_key(transaction, record.tenant_id, record.idempotency_key)
    if row is None:
        raise RuntimeError("pipeline schedule operation row missing after reserve")
    return row, True


def complete_schedule_operation(
    transaction: Any,
    *,
    tenant_id: str,
    operation_id: str,
    result_payload: dict[str, object],
) -> PipelineScheduleOperationRow | None:
    result = transaction.execute(
        update(db.pipeline_schedule_operations)
        .where(
            and_(
                db.pipeline_schedule_operations.c.tenant_id == tenant_id,
                db.pipeline_schedule_operations.c.id == operation_id,
                db.pipeline_schedule_operations.c.result.is_(None),
            )
        )
        .values(result=result_payload)
    )
    if not result.rowcount:
        return operation_by_id(transaction, tenant_id, operation_id)
    return operation_by_id(transaction, tenant_id, operation_id)


def operation_by_key(
    transaction: Any,
    tenant_id: str,
    idempotency_key: str,
) -> PipelineScheduleOperationRow | None:
    row = (
        transaction.execute(
            select(db.pipeline_schedule_operations).where(
                and_(
                    db.pipeline_schedule_operations.c.tenant_id == tenant_id,
                    db.pipeline_schedule_operations.c.idempotency_key == idempotency_key,
                )
            )
        )
        .mappings()
        .first()
    )
    return _optional_row(row, PipelineScheduleOperationRow)


def schedule_by_id(transaction: Any, tenant_id: str, schedule_id: str) -> PipelineScheduleRow | None:
    row = (
        transaction.execute(select(db.pipeline_schedules).where(_schedule_identity(tenant_id, schedule_id)))
        .mappings()
        .first()
    )
    return _optional_row(row, PipelineScheduleRow)


def operation_by_id(
    transaction: Any,
    tenant_id: str,
    operation_id: str,
) -> PipelineScheduleOperationRow | None:
    row = (
        transaction.execute(
            select(db.pipeline_schedule_operations).where(
                and_(
                    db.pipeline_schedule_operations.c.tenant_id == tenant_id,
                    db.pipeline_schedule_operations.c.id == operation_id,
                )
            )
        )
        .mappings()
        .first()
    )
    return _optional_row(row, PipelineScheduleOperationRow)


def _new_schedule_values(record: PipelineScheduleRecord) -> dict[str, object]:
    return {
        **_updated_schedule_values(record),
        "id": record.schedule_id,
        "pipeline_id": record.pipeline_id,
        "last_tick_at": None,
        "last_slot_at": None,
        "lease_owner": None,
        "lease_token": None,  # nosec B105 - DB lease state, not a credential; remove if this stores secrets.
        "lease_expires_at": None,
        "fencing_token": 0,  # nosec B105 - monotonic DB fence, not a password; remove if semantics change.
        "failure_count": 0,
        "last_failure_at": None,
        "last_error": None,
    }


def _updated_schedule_values(record: PipelineScheduleRecord) -> dict[str, object]:
    return {
        "version_id": record.version_id,
        "schedule": record.schedule,
        "enabled": record.enabled,
        "status": record.status,
        "trigger_type": record.trigger_type,
        "timezone": record.timezone,
        "next_due_at": record.next_due_at,
        "runtime_config_updated_at": record.updated_at,
        "paused_reason": record.paused_reason,
        "updated_by": record.updated_by,
        "updated_at": record.updated_at,
        "lease_owner": None,
        "lease_token": None,  # nosec B105 - DB lease state, not a credential; remove if this stores secrets.
        "lease_expires_at": None,
        "failure_count": 0,
        "last_failure_at": None,
        "last_error": None,
    }


def _operation_values(record: PipelineScheduleOperationRecord) -> dict[str, object]:
    return {
        "id": record.operation_id,
        "pipeline_id": record.pipeline_id,
        "operation": record.operation,
        "idempotency_key": record.idempotency_key,
        "request_fingerprint": record.request_fingerprint,
        "created_by": record.created_by,
        "created_at": record.created_at,
    }


def _schedule_identity(tenant_id: str, schedule_id: str) -> Any:
    return and_(
        db.pipeline_schedules.c.tenant_id == tenant_id,
        db.pipeline_schedules.c.id == schedule_id,
    )


def _runtime_config_is_stale() -> Any:
    return or_(
        db.pipeline_schedules.c.runtime_config_updated_at.is_(None),
        db.pipeline_schedules.c.runtime_config_updated_at != db.pipeline_schedules.c.updated_at,
    )


def _lease_is_claimable(observed_at: str) -> Any:
    return or_(
        db.pipeline_schedules.c.lease_expires_at.is_(None),
        db.pipeline_schedules.c.lease_expires_at <= observed_at,
    )


def _row[RowT](row: Any, row_type: type[RowT]) -> RowT:
    del row_type
    return cast(RowT, dict(row))


def _optional_row[RowT](row: Any | None, row_type: type[RowT]) -> RowT | None:
    return _row(row, row_type) if row is not None else None
