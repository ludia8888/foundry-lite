"""Append-only Pipeline run event ledger operations."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, insert, select, update

from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineRunEventRecord,
    PipelineRunEventRow,
)
from foundry_lite.infrastructure import schema as db


def append_run_event(transaction: Any, record: PipelineRunEventRecord) -> PipelineRunEventRow:
    result = transaction.execute(
        update(db.pipeline_runs)
        .where(
            and_(
                db.pipeline_runs.c.tenant_id == record.tenant_id,
                db.pipeline_runs.c.id == record.run_id,
            )
        )
        .values(event_sequence=db.pipeline_runs.c.event_sequence + 1)
        .returning(db.pipeline_runs.c.event_sequence)
    )
    sequence = result.scalar_one()
    transaction.execute(
        insert(db.pipeline_run_events).values(
            id=record.event_id,
            tenant_id=record.tenant_id,
            run_id=record.run_id,
            sequence=sequence,
            event_type=record.event_type,
            node_id=record.node_id,
            attempt_number=record.attempt_number,
            worker_id=record.worker_id,
            fencing_token=record.fencing_token,
            payload=record.payload,
            created_at=record.created_at,
        )
    )
    return _required_event(transaction, record.tenant_id, record.event_id)


def run_events(
    transaction: Any,
    tenant_id: str,
    run_id: str,
    after_sequence: int,
    limit: int,
) -> list[PipelineRunEventRow]:
    rows = (
        transaction.execute(
            select(db.pipeline_run_events)
            .where(
                and_(
                    db.pipeline_run_events.c.tenant_id == tenant_id,
                    db.pipeline_run_events.c.run_id == run_id,
                    db.pipeline_run_events.c.sequence > after_sequence,
                )
            )
            .order_by(db.pipeline_run_events.c.sequence)
            .limit(limit)
        )
        .mappings()
        .all()
    )
    return [cast(PipelineRunEventRow, dict(row)) for row in rows]


def _required_event(transaction: Any, tenant_id: str, event_id: str) -> PipelineRunEventRow:
    row = (
        transaction.execute(
            select(db.pipeline_run_events).where(
                and_(
                    db.pipeline_run_events.c.tenant_id == tenant_id,
                    db.pipeline_run_events.c.id == event_id,
                )
            )
        )
        .mappings()
        .one()
    )
    return cast(PipelineRunEventRow, dict(row))
