"""Bounded SQL read models used by backup and restore control paths."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import and_, func, select

from foundry_lite.application.ports import RuntimeJsonObject, RuntimeRow
from foundry_lite.infrastructure import schema as db


def backup_restore_high_watermarks(transaction: Any, tenant_id: str) -> RuntimeJsonObject:
    """Aggregate commit-point evidence in SQL instead of loading full run histories."""
    return {
        "auditEvents": _table_watermark(transaction, tenant_id, db.audit_events, (db.audit_events.c.created_at,)),
        "outboxEvents": _table_watermark(
            transaction,
            tenant_id,
            db.outbox_events,
            (db.outbox_events.c.created_at, db.outbox_events.c.published_at),
        ),
        "actionRuns": _table_watermark(
            transaction,
            tenant_id,
            db.action_runs,
            (db.action_runs.c.created_at, db.action_runs.c.completed_at),
        ),
        "actionWritebacks": _table_watermark(
            transaction,
            tenant_id,
            db.action_writebacks,
            (db.action_writebacks.c.created_at, db.action_writebacks.c.completed_at),
        ),
        "materializationRuns": _table_watermark(
            transaction,
            tenant_id,
            db.materialization_runs,
            (db.materialization_runs.c.created_at, db.materialization_runs.c.completed_at),
        ),
    }


def backup_restore_index_candidates(transaction: Any, tenant_id: str) -> list[RuntimeRow]:
    rows = (
        transaction.execute(
            select(db.index_runs.c.object_type_id, db.index_runs.c.object_type_api_name)
            .where(
                and_(
                    db.index_runs.c.tenant_id == tenant_id,
                    db.index_runs.c.object_type_id.is_not(None),
                    db.index_runs.c.object_type_api_name.is_not(None),
                )
            )
            .distinct()
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


def _table_watermark(
    transaction: Any,
    tenant_id: str,
    table: Any,
    time_columns: Sequence[Any],
) -> RuntimeJsonObject:
    aggregates = [func.count(table.c.id), *(func.max(column) for column in time_columns)]
    row = transaction.execute(select(*aggregates).where(table.c.tenant_id == tenant_id)).one()
    timestamps = sorted(str(value) for value in row[1:] if value is not None)
    status_counts = _status_counts(transaction, tenant_id, table) if "status" in table.c else {}
    return {
        "count": int(row[0]),
        "maxTimestamp": timestamps[-1] if timestamps else None,
        "statusCounts": status_counts,
    }


def _status_counts(transaction: Any, tenant_id: str, table: Any) -> dict[str, int]:
    rows = transaction.execute(
        select(table.c.status, func.count(table.c.id)).where(table.c.tenant_id == tenant_id).group_by(table.c.status)
    ).all()
    return {str(status): int(count) for status, count in rows if status is not None}
