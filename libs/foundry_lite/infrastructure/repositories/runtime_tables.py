"""Runtime table lookup, query-condition, and idempotency-duplicate helpers."""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, false, func, or_, select

import foundry_lite.infrastructure.schema as db
from foundry_lite.application.ports import (
    OutboxEventRecord,
    RuntimeLookupTable,
    RuntimeRowsTable,
    RuntimeRunPageCursor,
    RuntimeRunRelationRecord,
    RuntimeRunType,
)


def _lookup_table(table: RuntimeLookupTable) -> Any:
    return {
        "transforms": db.transforms,
        "materializations": db.materializations,
    }[table]


def _is_outbox_idempotency_duplicate(transaction: Any, record: OutboxEventRecord) -> bool:
    if record.idempotency_key is None:
        return False
    row = (
        transaction.execute(
            select(db.outbox_events.c.id)
            .where(
                and_(
                    db.outbox_events.c.tenant_id == record.tenant_id,
                    db.outbox_events.c.event_type == record.event_type,
                    db.outbox_events.c.idempotency_key == record.idempotency_key,
                )
            )
            .limit(1)
        )
        .mappings()
        .first()
    )
    return row is not None


def _is_run_relation_duplicate(transaction: Any, record: RuntimeRunRelationRecord) -> bool:
    row = (
        transaction.execute(
            select(db.runtime_run_relations.c.id)
            .where(
                and_(
                    db.runtime_run_relations.c.tenant_id == record.tenant_id,
                    db.runtime_run_relations.c.source_run_type == record.source_run_type,
                    db.runtime_run_relations.c.source_run_id == record.source_run_id,
                    db.runtime_run_relations.c.target_run_type == record.target_run_type,
                    db.runtime_run_relations.c.target_run_id == record.target_run_id,
                    db.runtime_run_relations.c.relation == record.relation,
                    db.runtime_run_relations.c.resource_type == record.resource_type,
                    db.runtime_run_relations.c.resource_id == record.resource_id,
                )
            )
            .limit(1)
        )
        .mappings()
        .first()
    )
    return row is not None


def _relation_columns(runtime_table: Any) -> list[Any]:
    # Mirror the correlation logic in runtime_run_queries._is_relation_key: match on
    # *_id / id / correlation_id columns, but never the tenant_id scope column.
    return [
        column
        for column in runtime_table.c
        if column.name != "tenant_id" and (column.name.endswith("_id") or column.name in {"id", "correlation_id"})
    ]


def _in_or_none(column: Any, values: list[str]) -> Any:
    # Build an IN clause only when there are values; empty IN clauses are noise.
    return column.in_(values) if values else None


def _rows_timestamp_column(table: RuntimeRowsTable) -> Any:
    # Recency column used to window each table to its most recent rows.
    if table == "dead_letter_events":
        return db.dead_letter_events.c.failed_at
    if table == "ai_execution_runs":
        return db.ai_execution_runs.c.started_at
    return _rows_table(table).c.created_at


def _rows_table(table: RuntimeRowsTable) -> Any:
    return {
        "source_exploration_runs": db.source_exploration_runs,
        "sync_runs": db.sync_runs,
        "transform_runs": db.transform_runs,
        "index_runs": db.index_runs,
        "action_runs": db.action_runs,
        "action_writebacks": db.action_writebacks,
        "materialization_runs": db.materialization_runs,
        "outbox_events": db.outbox_events,
        "dead_letter_events": db.dead_letter_events,
        "workflow_runs": db.workflow_runs,
        "ai_execution_runs": db.ai_execution_runs,
        "audit_events": db.audit_events,
        "object_edits": db.object_edits,
        "object_records": db.object_records,
    }[table]


def _run_table(run_type: RuntimeRunType) -> Any:
    return {
        "source_exploration": db.source_exploration_runs,
        "sync": db.sync_runs,
        "transform": db.transform_runs,
        "index": db.index_runs,
        "action": db.action_runs,
        "action_writeback": db.action_writebacks,
        "materialization": db.materialization_runs,
        "outbox": db.outbox_events,
        "dead_letter": db.dead_letter_events,
        "workflow": db.workflow_runs,
        "ai": db.ai_execution_runs,
        "audit": db.audit_events,
    }[run_type]


def _run_timestamp_column(run_type: RuntimeRunType) -> Any:
    if run_type == "dead_letter":
        return db.dead_letter_events.c.failed_at
    if run_type == "ai":
        return db.ai_execution_runs.c.started_at
    return _run_table(run_type).c.created_at


def _run_query_conditions(
    runtime_table: Any,
    run_type: RuntimeRunType,
    tenant_id: str,
    status: str | None,
    since: str | None,
    until: str | None,
    cursor: RuntimeRunPageCursor | None,
) -> list[Any]:
    timestamp = _run_timestamp_column(run_type)
    conditions = [runtime_table.c.tenant_id == tenant_id, _run_status_condition(runtime_table, run_type, status)]
    if since:
        conditions.append(timestamp >= since)
    if until:
        conditions.append(timestamp <= until)
    if cursor is not None:
        conditions.append(
            or_(
                timestamp < cursor["timestamp"],
                and_(timestamp == cursor["timestamp"], runtime_table.c.id < cursor["run_id"]),
            )
        )
    return conditions


def _run_status_condition(runtime_table: Any, run_type: RuntimeRunType, status: str | None) -> Any:
    if status is None or status == "":
        return True
    normalized = status.lower()
    if run_type == "dead_letter":
        return True if normalized == "dead_lettered" else false()
    column = runtime_table.c.decision if run_type == "audit" else runtime_table.c.status
    return func.lower(column) == normalized
