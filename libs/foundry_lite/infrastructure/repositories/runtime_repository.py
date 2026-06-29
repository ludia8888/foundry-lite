from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import and_, delete, desc, false, func, insert, or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from foundry_lite.application.ports import (
    AuditEventRecord,
    DeadLetterEventRecord,
    LineageEdgeRecord,
    LineageEdgeRow,
    OutboxEventRecord,
    RuntimeLookupTable,
    RuntimeRow,
    RuntimeRowsTable,
    RuntimeRunPageCursor,
    RuntimeRunRelationRecord,
    RuntimeRunRelationRow,
    RuntimeRunSnapshot,
    RuntimeRunType,
)
from foundry_lite.application.ports.transaction_context import StatusTransition
from foundry_lite.application.ports.workflow_adapter import WorkflowRunRecord, WorkflowRunRow
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.status_cas import cas_status_update


class SqlAlchemyRuntimeRepository:
    """SQLAlchemy implementation of runtime audit, outbox, lineage, and run reads."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def lineage_for_resource(self, *, tenant_id: str, resource_id: str) -> list[LineageEdgeRow]:
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
            return [cast(LineageEdgeRow, dict(row)) for row in rows]

    def list_runs(self, *, tenant_id: str, limit: int | None = None) -> RuntimeRunSnapshot:
        with self.engine.begin() as transaction:

            def window(table: RuntimeRowsTable) -> list[RuntimeRow]:
                return self._rows_window(transaction=transaction, table=table, tenant_id=tenant_id, limit=limit)

            return {
                "syncRuns": window("sync_runs"),
                "transformRuns": window("transform_runs"),
                "indexRuns": window("index_runs"),
                "actionRuns": window("action_runs"),
                "actionWritebacks": window("action_writebacks"),
                "materializationRuns": window("materialization_runs"),
                "outboxEvents": window("outbox_events"),
                "deadLetterEvents": window("dead_letter_events"),
                "workflowRuns": window("workflow_runs"),
                "aiRuns": window("ai_execution_runs"),
                "auditEvents": window("audit_events"),
                "objectEdits": window("object_edits"),
            }

    def _rows_window(
        self, *, transaction: Any, table: RuntimeRowsTable, tenant_id: str, limit: int | None
    ) -> list[RuntimeRow]:
        runtime_table = _rows_table(table)
        query = select(runtime_table).where(runtime_table.c.tenant_id == tenant_id)
        if limit is not None:
            timestamp = _rows_timestamp_column(table)
            query = query.order_by(desc(timestamp), desc(runtime_table.c.id)).limit(limit)
        rows = transaction.execute(query).mappings().all()
        return [cast(RuntimeRow, dict(row)) for row in rows]

    def runs_for_source_chain(
        self,
        *,
        tenant_id: str,
        object_type_api_name: str,
        resource_ids: Sequence[str],
        run_ids: Sequence[str],
        limit: int,
    ) -> RuntimeRunSnapshot:
        rids = list(resource_ids)
        runids = list(run_ids)
        with self.engine.begin() as transaction:
            return {
                "syncRuns": self._scoped_rows(
                    transaction,
                    db.sync_runs,
                    tenant_id,
                    limit,
                    _in_or_none(db.sync_runs.c.committed_version_id, rids),
                    run_ids=runids,
                ),
                "transformRuns": self._scoped_rows(
                    transaction,
                    db.transform_runs,
                    tenant_id,
                    limit,
                    _in_or_none(db.transform_runs.c.output_version_id, rids),
                    run_ids=runids,
                ),
                "indexRuns": self._scoped_rows(
                    transaction,
                    db.index_runs,
                    tenant_id,
                    limit,
                    db.index_runs.c.object_type_api_name == object_type_api_name,
                    run_ids=runids,
                ),
                "actionRuns": [],
                "actionWritebacks": [],
                "materializationRuns": self._scoped_rows(
                    transaction,
                    db.materialization_runs,
                    tenant_id,
                    limit,
                    _in_or_none(db.materialization_runs.c.target_dataset_version_id, rids),
                    run_ids=runids,
                ),
                "outboxEvents": [],
                "deadLetterEvents": [],
                "workflowRuns": self._scoped_rows(
                    transaction, db.workflow_runs, tenant_id, limit, None, run_ids=runids
                ),
                "aiRuns": self._scoped_rows(
                    transaction,
                    db.ai_execution_runs,
                    tenant_id,
                    limit,
                    None,
                    run_ids=runids,
                    timestamp_column=db.ai_execution_runs.c.started_at,
                ),
                "auditEvents": [],
                "objectEdits": [],
            }

    def _scoped_rows(
        self,
        transaction: Any,
        table: Any,
        tenant_id: str,
        limit: int,
        *match: Any,
        run_ids: Sequence[str],
        timestamp_column: Any | None = None,
    ) -> list[RuntimeRow]:
        conditions = [condition for condition in match if condition is not None]
        if run_ids:
            conditions.append(table.c.id.in_(list(run_ids)))
        if not conditions:
            return []
        timestamp = timestamp_column if timestamp_column is not None else table.c.created_at
        query = (
            select(table)
            .where(and_(table.c.tenant_id == tenant_id, or_(*conditions)))
            .order_by(desc(timestamp), desc(table.c.id))
            .limit(limit)
        )
        rows = transaction.execute(query).mappings().all()
        return [cast(RuntimeRow, dict(row)) for row in rows]

    def query_run_rows(
        self,
        *,
        tenant_id: str,
        run_type: RuntimeRunType,
        status: str | None,
        since: str | None,
        until: str | None,
        cursor: RuntimeRunPageCursor | None,
        limit: int,
    ) -> list[RuntimeRow]:
        runtime_table = _run_table(run_type)
        timestamp = _run_timestamp_column(run_type)
        conditions = _run_query_conditions(runtime_table, run_type, tenant_id, status, since, until, cursor)
        with self.engine.begin() as transaction:
            rows = (
                transaction.execute(
                    select(runtime_table)
                    .where(and_(*conditions))
                    .order_by(desc(timestamp), desc(runtime_table.c.id))
                    .limit(limit)
                )
                .mappings()
                .all()
            )
        return [cast(RuntimeRow, dict(row)) for row in rows]

    def run_row(self, *, tenant_id: str, run_type: RuntimeRunType, run_id: str) -> RuntimeRow | None:
        runtime_table = _run_table(run_type)
        with self.engine.begin() as transaction:
            row = (
                transaction.execute(
                    select(runtime_table).where(
                        and_(runtime_table.c.tenant_id == tenant_id, runtime_table.c.id == run_id)
                    )
                )
                .mappings()
                .first()
            )
        return cast(RuntimeRow, dict(row)) if row else None

    def run_row_any_type(self, *, tenant_id: str, run_id: str) -> tuple[RuntimeRunType, RuntimeRow] | None:
        with self.engine.begin() as transaction:
            for run_type in _LINEAGE_RUN_TYPES:
                runtime_table = _run_table(run_type)
                row = (
                    transaction.execute(
                        select(runtime_table).where(
                            and_(runtime_table.c.tenant_id == tenant_id, runtime_table.c.id == run_id)
                        )
                    )
                    .mappings()
                    .first()
                )
                if row:
                    return run_type, cast(RuntimeRow, dict(row))
        return None

    def related_evidence_rows(
        self, *, tenant_id: str, table: RuntimeRowsTable, relation_ids: Sequence[str], limit: int
    ) -> list[RuntimeRow]:
        if not relation_ids:
            return []
        runtime_table = _rows_table(table)
        ids = list(relation_ids)
        match = or_(*(column.in_(ids) for column in _relation_columns(runtime_table)))
        with self.engine.begin() as transaction:
            rows = (
                transaction.execute(
                    select(runtime_table).where(and_(runtime_table.c.tenant_id == tenant_id, match)).limit(limit)
                )
                .mappings()
                .all()
            )
        return [cast(RuntimeRow, dict(row)) for row in rows]

    def row_by_id(self, *, transaction: Any, table: RuntimeLookupTable, row_id: str) -> RuntimeRow | None:
        runtime_table = _lookup_table(table)
        row = transaction.execute(select(runtime_table).where(runtime_table.c.id == row_id)).mappings().first()
        return cast(RuntimeRow, dict(row)) if row else None

    def dead_letter_event_by_id(self, *, transaction: Any, tenant_id: str, event_id: str) -> RuntimeRow | None:
        row = (
            transaction.execute(
                select(db.dead_letter_events).where(
                    and_(db.dead_letter_events.c.tenant_id == tenant_id, db.dead_letter_events.c.id == event_id)
                )
            )
            .mappings()
            .first()
        )
        return cast(RuntimeRow, dict(row)) if row else None

    def rows_for_tenant(self, *, transaction: Any, table: RuntimeRowsTable, tenant_id: str) -> list[RuntimeRow]:
        runtime_table = _rows_table(table)
        rows = transaction.execute(select(runtime_table).where(runtime_table.c.tenant_id == tenant_id)).mappings().all()
        return [cast(RuntimeRow, dict(row)) for row in rows]

    def pending_outbox_events(self, *, transaction: Any, tenant_id: str, limit: int) -> list[RuntimeRow]:
        rows = (
            transaction.execute(
                select(db.outbox_events)
                .where(and_(db.outbox_events.c.tenant_id == tenant_id, db.outbox_events.c.status == "pending"))
                .order_by(db.outbox_events.c.created_at, db.outbox_events.c.id)
                .limit(limit)
            )
            .mappings()
            .all()
        )
        return [cast(RuntimeRow, dict(row)) for row in rows]

    def mark_outbox_event_publishing(
        self, *, transaction: Any, tenant_id: str, event_id: str, transition: StatusTransition
    ) -> RuntimeRow | None:
        updated = cas_status_update(
            transaction,
            db.outbox_events,
            tenant_id=tenant_id,
            row_id=event_id,
            transition=transition,
            values={"attempts": db.outbox_events.c.attempts + 1},
        )
        if not updated:
            return None
        return self._outbox_event_row(transaction=transaction, tenant_id=tenant_id, event_id=event_id)

    def mark_outbox_event_published(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        event_id: str,
        transition: StatusTransition,
        published_at: str,
    ) -> RuntimeRow | None:
        updated = cas_status_update(
            transaction,
            db.outbox_events,
            tenant_id=tenant_id,
            row_id=event_id,
            transition=transition,
            values={"published_at": published_at},
        )
        if not updated:
            return None
        return self._outbox_event_row(transaction=transaction, tenant_id=tenant_id, event_id=event_id)

    def mark_outbox_event_failed(
        self, *, transaction: Any, tenant_id: str, event_id: str, transition: StatusTransition
    ) -> RuntimeRow | None:
        updated = cas_status_update(
            transaction,
            db.outbox_events,
            tenant_id=tenant_id,
            row_id=event_id,
            transition=transition,
            values={"published_at": None},
        )
        if not updated:
            return None
        return self._outbox_event_row(transaction=transaction, tenant_id=tenant_id, event_id=event_id)

    def _outbox_event_row(self, *, transaction: Any, tenant_id: str, event_id: str) -> RuntimeRow | None:
        row = (
            transaction.execute(
                select(db.outbox_events).where(
                    and_(db.outbox_events.c.tenant_id == tenant_id, db.outbox_events.c.id == event_id)
                )
            )
            .mappings()
            .first()
        )
        return cast(RuntimeRow, dict(row)) if row else None

    def update_outbox_event_for_retry(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        event_id: str,
        transition: StatusTransition,
    ) -> RuntimeRow | None:
        updated = cas_status_update(
            transaction,
            db.outbox_events,
            tenant_id=tenant_id,
            row_id=event_id,
            transition=transition,
            values={"attempts": 0, "published_at": None},
        )
        if not updated:
            return None
        row = (
            transaction.execute(
                select(db.outbox_events).where(
                    and_(db.outbox_events.c.tenant_id == tenant_id, db.outbox_events.c.id == event_id)
                )
            )
            .mappings()
            .first()
        )
        return cast(RuntimeRow, dict(row)) if row else None

    def workflow_run_by_id(self, *, transaction: Any, tenant_id: str, workflow_run_id: str) -> WorkflowRunRow | None:
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
        self,
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

    def insert_workflow_run_or_get_existing(
        self, *, transaction: Any, record: WorkflowRunRecord
    ) -> WorkflowRunRow | None:
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
            existing = self.workflow_run_by_idempotency(
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
        self,
        *,
        transaction: Any,
        tenant_id: str,
        workflow_run_id: str,
        transition: StatusTransition,
        output: RuntimeRow,
        error: RuntimeRow | None,
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
        return self.workflow_run_by_id(transaction=transaction, tenant_id=tenant_id, workflow_run_id=workflow_run_id)

    def link_workflow_audit_event(
        self,
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
        return self.workflow_run_by_id(transaction=transaction, tenant_id=tenant_id, workflow_run_id=workflow_run_id)

    def delete_dead_letter_event(self, *, transaction: Any, tenant_id: str, event_id: str) -> bool:
        result = transaction.execute(
            delete(db.dead_letter_events).where(
                and_(db.dead_letter_events.c.tenant_id == tenant_id, db.dead_letter_events.c.id == event_id)
            )
        )
        return result.rowcount == 1

    def delete_object_record(self, *, transaction: Any, tenant_id: str, record_id: str) -> bool:
        # Tombstone the record (keep the row so rebuilds do not resurrect it) and
        # clear the subject-bearing property payloads. The deleted=False predicate
        # makes the erasure idempotent: a replay touches no row and returns False.
        result = transaction.execute(
            db.object_records.update()
            .where(
                and_(
                    db.object_records.c.tenant_id == tenant_id,
                    db.object_records.c.id == record_id,
                    db.object_records.c.deleted.is_(False),
                )
            )
            .values(
                deleted=True,
                is_active=False,
                deletion_reason="subject_erasure",
                properties={},
                base_properties={},
                edit_properties={},
                property_versions={},
            )
        )
        return result.rowcount == 1

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
                policy_decision=dict(record.policy_decision),
                before_ref=dict(record.before_ref),
                after_ref=dict(record.after_ref),
                correlation_id=record.correlation_id,
                request_id=record.request_id,
                metadata=dict(record.metadata),
                created_at=record.created_at,
            )
        )

    def insert_outbox_event(self, *, transaction: Any, record: OutboxEventRecord) -> bool:
        # PostgreSQL aborts the whole transaction on IntegrityError, unlike
        # SQLite which lets the caller keep using the same connection. To make
        # the duplicate-insert path safe on both backends we wrap the attempt
        # in a SAVEPOINT (begin_nested) so a unique-violation only rolls back
        # the savepoint and leaves the outer transaction usable. This is what
        # the Postgres contract-test pairing (Sprint 9.4) caught.
        savepoint = transaction.begin_nested()
        try:
            transaction.execute(
                insert(db.outbox_events).values(
                    id=record.event_id,
                    tenant_id=record.tenant_id,
                    event_type=record.event_type,
                    aggregate_type=record.aggregate_type,
                    aggregate_id=record.aggregate_id,
                    payload=dict(record.payload),
                    status=record.status,
                    attempts=record.attempts,
                    idempotency_key=record.idempotency_key,
                    correlation_id=record.correlation_id,
                    created_at=record.created_at,
                    published_at=record.published_at,
                )
            )
        except IntegrityError:
            savepoint.rollback()
            if _is_outbox_idempotency_duplicate(transaction, record):
                return False
            raise
        savepoint.commit()
        return True

    def insert_dead_letter_event(self, *, transaction: Any, record: DeadLetterEventRecord) -> None:
        transaction.execute(
            insert(db.dead_letter_events).values(
                id=record.event_id,
                tenant_id=record.tenant_id,
                source_event_id=record.source_event_id,
                event_type=record.event_type,
                payload=dict(record.payload),
                error=dict(record.error),
                failed_at=record.failed_at,
                retry_after=record.retry_after,
            )
        )

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

    def insert_run_relation(self, *, transaction: Any, record: RuntimeRunRelationRecord) -> bool:
        savepoint = transaction.begin_nested()
        try:
            transaction.execute(
                insert(db.runtime_run_relations).values(
                    id=record.relation_id,
                    tenant_id=record.tenant_id,
                    source_run_type=record.source_run_type,
                    source_run_id=record.source_run_id,
                    target_run_type=record.target_run_type,
                    target_run_id=record.target_run_id,
                    relation=record.relation,
                    resource_type=record.resource_type,
                    resource_id=record.resource_id,
                    metadata=dict(record.metadata),
                    created_at=record.created_at,
                )
            )
        except IntegrityError:
            savepoint.rollback()
            if _is_run_relation_duplicate(transaction, record):
                return False
            raise
        savepoint.commit()
        return True

    def run_relations_for_run(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        run_type: RuntimeRunType,
        run_id: str,
    ) -> list[RuntimeRunRelationRow]:
        rows = (
            transaction.execute(
                select(db.runtime_run_relations)
                .where(
                    and_(
                        db.runtime_run_relations.c.tenant_id == tenant_id,
                        or_(
                            and_(
                                db.runtime_run_relations.c.source_run_type == run_type,
                                db.runtime_run_relations.c.source_run_id == run_id,
                            ),
                            and_(
                                db.runtime_run_relations.c.target_run_type == run_type,
                                db.runtime_run_relations.c.target_run_id == run_id,
                            ),
                        ),
                    )
                )
                .order_by(db.runtime_run_relations.c.created_at, db.runtime_run_relations.c.id)
            )
            .mappings()
            .all()
        )
        return [cast(RuntimeRunRelationRow, dict(row)) for row in rows]


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


# Run tables that lineage edges attribute work to (``created_by_run_id``).
_LINEAGE_RUN_TYPES: tuple[RuntimeRunType, ...] = ("sync", "transform", "index", "materialization", "ai")


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
