from __future__ import annotations

from typing import Any

from sqlalchemy import and_, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from foundry_lite.application.ports import (
    AuditEventRecord,
    LineageEdgeRecord,
    OutboxEventRecord,
    RuntimeLookupTable,
    RuntimeRowsTable,
)
from foundry_lite.infrastructure import schema as db


class SqlAlchemyRuntimeRepository:
    """SQLAlchemy implementation of runtime audit, outbox, lineage, and run reads."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def lineage_for_resource(self, *, tenant_id: str, resource_id: str) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            rows = (
                conn.execute(
                    select(db.lineage_edges).where(
                        and_(
                            db.lineage_edges.c.tenant_id == tenant_id,
                            (
                                (db.lineage_edges.c.from_resource_id == resource_id)
                                | (db.lineage_edges.c.to_resource_id == resource_id)
                            ),
                        )
                    )
                )
                .mappings()
                .all()
            )
            return [dict(row) for row in rows]

    def list_runs(self, *, tenant_id: str) -> dict[str, list[dict[str, Any]]]:
        with self.engine.begin() as transaction:
            return {
                "syncRuns": self.rows_for_tenant(transaction=transaction, table="sync_runs", tenant_id=tenant_id),
                "transformRuns": self.rows_for_tenant(
                    transaction=transaction, table="transform_runs", tenant_id=tenant_id
                ),
                "indexRuns": self.rows_for_tenant(transaction=transaction, table="index_runs", tenant_id=tenant_id),
                "actionRuns": self.rows_for_tenant(transaction=transaction, table="action_runs", tenant_id=tenant_id),
                "actionWritebacks": self.rows_for_tenant(
                    transaction=transaction,
                    table="action_writebacks",
                    tenant_id=tenant_id,
                ),
                "materializationRuns": self.rows_for_tenant(
                    transaction=transaction,
                    table="materialization_runs",
                    tenant_id=tenant_id,
                ),
                "outboxEvents": self.rows_for_tenant(
                    transaction=transaction, table="outbox_events", tenant_id=tenant_id
                ),
                "auditEvents": self.rows_for_tenant(transaction=transaction, table="audit_events", tenant_id=tenant_id),
            }

    def row_by_id(self, *, transaction: Any, table: RuntimeLookupTable, row_id: str) -> dict[str, Any] | None:
        runtime_table = _lookup_table(table)
        row = transaction.execute(select(runtime_table).where(runtime_table.c.id == row_id)).mappings().first()
        return dict(row) if row else None

    def rows_for_tenant(self, *, transaction: Any, table: RuntimeRowsTable, tenant_id: str) -> list[dict[str, Any]]:
        runtime_table = _rows_table(table)
        rows = transaction.execute(select(runtime_table).where(runtime_table.c.tenant_id == tenant_id)).mappings().all()
        return [dict(row) for row in rows]

    def insert_audit_event(self, *, transaction: Any, record: AuditEventRecord) -> None:
        transaction.execute(
            insert(db.audit_events).values(
                id=record.event_id,
                tenant_id=record.tenant_id,
                actor_user_id=record.actor_user_id,
                event_type=record.event_type,
                resource_type=record.resource_type,
                resource_id=record.resource_id,
                action=record.action,
                decision=record.decision,
                policy_decision=record.policy_decision,
                before_ref=record.before_ref,
                after_ref=record.after_ref,
                correlation_id=record.correlation_id,
                request_id=record.request_id,
                metadata=record.metadata,
                created_at=record.created_at,
            )
        )

    def insert_outbox_event(self, *, transaction: Any, record: OutboxEventRecord) -> bool:
        try:
            transaction.execute(
                insert(db.outbox_events).values(
                    id=record.event_id,
                    tenant_id=record.tenant_id,
                    event_type=record.event_type,
                    aggregate_type=record.aggregate_type,
                    aggregate_id=record.aggregate_id,
                    payload=record.payload,
                    status=record.status,
                    attempts=record.attempts,
                    idempotency_key=record.idempotency_key,
                    correlation_id=record.correlation_id,
                    created_at=record.created_at,
                    published_at=record.published_at,
                )
            )
        except IntegrityError:
            return False
        return True

    def insert_lineage_edge(self, *, transaction: Any, record: LineageEdgeRecord) -> None:
        transaction.execute(
            insert(db.lineage_edges).values(
                id=record.edge_id,
                tenant_id=record.tenant_id,
                from_resource_type=record.from_resource_type,
                from_resource_id=record.from_resource_id,
                to_resource_type=record.to_resource_type,
                to_resource_id=record.to_resource_id,
                relation=record.relation,
                created_by_run_id=record.created_by_run_id,
                created_at=record.created_at,
            )
        )


def _lookup_table(table: RuntimeLookupTable) -> Any:
    return {
        "transforms": db.transforms,
        "materializations": db.materializations,
    }[table]


def _rows_table(table: RuntimeRowsTable) -> Any:
    return {
        "sync_runs": db.sync_runs,
        "transform_runs": db.transform_runs,
        "index_runs": db.index_runs,
        "action_runs": db.action_runs,
        "action_writebacks": db.action_writebacks,
        "materialization_runs": db.materialization_runs,
        "outbox_events": db.outbox_events,
        "audit_events": db.audit_events,
        "object_edits": db.object_edits,
        "object_records": db.object_records,
    }[table]
