"""SQLAlchemy repository adapter for runtime repository persistence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from sqlalchemy import and_, delete, desc, insert, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

import foundry_lite.infrastructure.repositories.runtime_workflow_runs as runtime_workflow_runs
from foundry_lite.application.ports import (
    AuditEventRecord,
    DeadLetterEventRecord,
    LineageEdgeRecord,
    LineageEdgeRow,
    ObservabilityIncident,
    OutboxEventRecord,
    RuntimeJsonObject,
    RuntimeLookupTable,
    RuntimeRow,
    RuntimeRowsTable,
    RuntimeRunPageCursor,
    RuntimeRunRelationRecord,
    RuntimeRunRelationRow,
    RuntimeRunSnapshot,
    RuntimeRunType,
    StoredObservabilityIncident,
    StoredObservabilityIncidentStatus,
)
from foundry_lite.application.ports.transaction_context import (
    StatusTransition,
)
from foundry_lite.application.ports.workflow_adapter import WorkflowLedgerStatus, WorkflowRunRecord, WorkflowRunRow
from foundry_lite.application.state_transitions import observability_incident_transition
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.runtime_backup_restore_sql import (
    backup_restore_high_watermarks,
    backup_restore_index_candidates,
)
from foundry_lite.infrastructure.repositories.runtime_observability_sql import (
    _observability_incident_by_dedupe,
    _observability_incident_db_row,
    _observability_incident_response,
    _observability_insert_values,
    _observability_status_values,
    _observability_update_values,
)
from foundry_lite.infrastructure.repositories.runtime_tables import (
    _in_or_none,
    _is_outbox_idempotency_duplicate,
    _is_run_relation_duplicate,
    _lookup_table,
    _relation_columns,
    _rows_table,
    _rows_timestamp_column,
    _run_query_conditions,
    _run_table,
    _run_timestamp_column,
)
from foundry_lite.infrastructure.repositories.status_cas import cas_status_update, cas_status_update_many


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

    def catalog_lineage_for_resource(self, *, tenant_id: str, resource_id: str) -> list[LineageEdgeRow]:
        with self.engine.begin() as conn:
            owned = self._resource_dataset_version_ids(conn, tenant_id, resource_id)
            if not owned:
                return []
            rows = (
                conn.execute(
                    select(db.lineage_edges).where(
                        and_(
                            db.lineage_edges.c.tenant_id == tenant_id,
                            (
                                db.lineage_edges.c.from_resource_id.in_(owned)
                                | db.lineage_edges.c.to_resource_id.in_(owned)
                            ),
                        )
                    )
                )
                .mappings()
                .all()
            )
            version_to_resource = self._version_resource_index(conn, tenant_id)
            return _folded_catalog_edges([dict(row) for row in rows], version_to_resource)

    def _resource_dataset_version_ids(self, conn: Any, tenant_id: str, resource_id: str) -> list[str]:
        """Every dataset version the catalog resource owns, which is what edges are keyed by."""

        dataset_ref = conn.execute(
            select(db.resources.c.source_ref).where(
                and_(
                    db.resources.c.tenant_id == tenant_id,
                    db.resources.c.id == resource_id,
                    db.resources.c.source_surface == "dataset",
                )
            )
        ).scalar_one_or_none()
        if dataset_ref is None:
            return []
        rows = conn.execute(
            select(db.dataset_versions.c.id)
            .select_from(db.dataset_versions.join(db.datasets, db.datasets.c.id == db.dataset_versions.c.dataset_id))
            .where(
                and_(
                    db.dataset_versions.c.tenant_id == tenant_id,
                    (db.datasets.c.namespace + "." + db.datasets.c.name) == dataset_ref,
                )
            )
        ).all()
        return [str(row[0]) for row in rows]

    def _version_resource_index(self, conn: Any, tenant_id: str) -> dict[str, tuple[str, str]]:
        """Map every dataset version to the catalog resource that owns it."""

        rows = conn.execute(
            select(
                db.dataset_versions.c.id,
                db.resources.c.id,
                db.resources.c.display_name,
            )
            .select_from(
                db.dataset_versions.join(db.datasets, db.datasets.c.id == db.dataset_versions.c.dataset_id).join(
                    db.resources,
                    and_(
                        db.resources.c.tenant_id == db.dataset_versions.c.tenant_id,
                        db.resources.c.source_surface == "dataset",
                        db.resources.c.source_ref == db.datasets.c.namespace + "." + db.datasets.c.name,
                    ),
                )
            )
            .where(db.dataset_versions.c.tenant_id == tenant_id)
        ).all()
        return {str(row[0]): (str(row[1]), str(row[2])) for row in rows}

    def list_runs(self, *, tenant_id: str, limit: int | None = None) -> RuntimeRunSnapshot:
        with self.engine.begin() as transaction:

            def window(table: RuntimeRowsTable) -> list[RuntimeRow]:
                return self._rows_window(transaction=transaction, table=table, tenant_id=tenant_id, limit=limit)

            return {
                "sourceExplorationRuns": window("source_exploration_runs"),
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

    def backup_restore_high_watermarks(self, *, tenant_id: str) -> RuntimeJsonObject:
        with self.engine.begin() as transaction:
            return backup_restore_high_watermarks(transaction, tenant_id)

    def backup_restore_index_candidates(self, *, tenant_id: str) -> list[RuntimeRow]:
        with self.engine.begin() as transaction:
            return backup_restore_index_candidates(transaction, tenant_id)

    def audit_event_for_resource(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        event_type: str,
        resource_type: str,
        resource_id: str,
    ) -> RuntimeRow | None:
        row = (
            transaction.execute(
                select(db.audit_events)
                .where(
                    db.audit_events.c.tenant_id == tenant_id,
                    db.audit_events.c.event_type == event_type,
                    db.audit_events.c.resource_type == resource_type,
                    db.audit_events.c.resource_id == resource_id,
                )
                .order_by(desc(db.audit_events.c.created_at), desc(db.audit_events.c.id))
                .limit(1)
            )
            .mappings()
            .first()
        )
        return cast(RuntimeRow, dict(row)) if row is not None else None

    def audit_events_for_resources(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        resource_refs: Sequence[tuple[str, str]],
        event_types: Sequence[str],
        limit: int,
    ) -> list[RuntimeRow]:
        if not resource_refs or not event_types or limit < 1:
            return []
        resource_conditions = [
            and_(
                db.audit_events.c.resource_type == resource_type,
                db.audit_events.c.resource_id == resource_id,
            )
            for resource_type, resource_id in resource_refs
        ]
        rows = (
            transaction.execute(
                select(db.audit_events)
                .where(
                    db.audit_events.c.tenant_id == tenant_id,
                    db.audit_events.c.event_type.in_(tuple(event_types)),
                    or_(*resource_conditions),
                )
                .order_by(desc(db.audit_events.c.created_at), desc(db.audit_events.c.id))
                .limit(limit)
            )
            .mappings()
            .all()
        )
        return [cast(RuntimeRow, dict(row)) for row in rows]

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
                "sourceExplorationRuns": [],
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

    def list_observability_incidents(
        self,
        *,
        tenant_id: str,
        status: StoredObservabilityIncidentStatus | None,
        limit: int,
    ) -> list[StoredObservabilityIncident]:
        conditions = [db.observability_incidents.c.tenant_id == tenant_id]
        if status is not None:
            conditions.append(db.observability_incidents.c.status == status)
        with self.engine.begin() as transaction:
            rows = (
                transaction.execute(
                    select(db.observability_incidents)
                    .where(and_(*conditions))
                    .order_by(
                        desc(db.observability_incidents.c.last_observed_at),
                        desc(db.observability_incidents.c.id),
                    )
                    .limit(limit)
                )
                .mappings()
                .all()
            )
        return [_observability_incident_response(dict(row)) for row in rows]

    def observability_incident_by_id(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        incident_id: str,
    ) -> StoredObservabilityIncident | None:
        row = _observability_incident_db_row(transaction, tenant_id=tenant_id, incident_id=incident_id)
        return _observability_incident_response(row) if row else None

    def upsert_observability_incident(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        incident: ObservabilityIncident,
    ) -> StoredObservabilityIncident:
        existing = _observability_incident_by_dedupe(transaction, tenant_id, incident["dedupeKey"])
        if existing is None:
            return self._insert_observability_incident(transaction, tenant_id, incident)
        return self._update_observability_incident(transaction, tenant_id, incident, existing)

    def _insert_observability_incident(
        self,
        transaction: Any,
        tenant_id: str,
        incident: ObservabilityIncident,
    ) -> StoredObservabilityIncident:
        values = _observability_insert_values(tenant_id, incident)
        values.pop("tenant_id", None)
        transaction.execute(insert(db.observability_incidents).values(tenant_id=tenant_id, **values))
        row = _observability_incident_db_row(transaction, tenant_id=tenant_id, incident_id=incident["id"])
        assert row is not None
        return _observability_incident_response(row)

    def _update_observability_incident(
        self,
        transaction: Any,
        tenant_id: str,
        incident: ObservabilityIncident,
        existing: Mapping[str, Any],
    ) -> StoredObservabilityIncident:
        values = _observability_update_values(incident, existing)
        transaction.execute(
            db.observability_incidents.update()
            .where(
                and_(
                    db.observability_incidents.c.tenant_id == tenant_id,
                    db.observability_incidents.c.id == existing["id"],
                )
            )
            .values(**values)
        )
        row = _observability_incident_db_row(transaction, tenant_id=tenant_id, incident_id=str(existing["id"]))
        assert row is not None
        return _observability_incident_response(row)

    def update_observability_incident_status(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        incident_id: str,
        status: StoredObservabilityIncidentStatus,
        actor_user_id: str,
        updated_at: str,
        resolution_reason: str | None,
    ) -> StoredObservabilityIncident | None:
        values = _observability_status_values(status, actor_user_id, updated_at, resolution_reason)
        # The transition owns the status column; cas_status_update sets it from the
        # transition's target and guards on the allowed source statuses, so a
        # concurrent change makes the CAS match zero rows instead of clobbering it.
        values.pop("status", None)
        updated = cas_status_update(
            transaction,
            db.observability_incidents,
            tenant_id=tenant_id,
            row_id=incident_id,
            transition=observability_incident_transition(status),
            values=values,
        )
        if not updated:
            return None
        return self.observability_incident_by_id(
            transaction=transaction,
            tenant_id=tenant_id,
            incident_id=incident_id,
        )

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

    def rows_for_tenant(
        self,
        *,
        transaction: Any,
        table: RuntimeRowsTable,
        tenant_id: str,
        event_types: Sequence[str] | None = None,
    ) -> list[RuntimeRow]:
        runtime_table = _rows_table(table)
        query = select(runtime_table).where(runtime_table.c.tenant_id == tenant_id)
        if event_types is not None:
            query = query.where(runtime_table.c.event_type.in_(tuple(event_types)))
        rows = transaction.execute(query).mappings().all()
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

    def outbox_event_by_idempotency_key(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        idempotency_key: str,
    ) -> RuntimeRow | None:
        row = (
            transaction.execute(
                select(db.outbox_events).where(
                    and_(
                        db.outbox_events.c.tenant_id == tenant_id,
                        db.outbox_events.c.idempotency_key == idempotency_key,
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(RuntimeRow, dict(row)) if row else None

    def mark_outbox_event_publishing(
        self, *, transaction: Any, tenant_id: str, event_id: str, transition: StatusTransition, claimed_at: str
    ) -> RuntimeRow | None:
        updated = cas_status_update(
            transaction,
            db.outbox_events,
            tenant_id=tenant_id,
            row_id=event_id,
            transition=transition,
            values={"attempts": db.outbox_events.c.attempts + 1, "claimed_at": claimed_at},
        )
        if not updated:
            return None
        return self._outbox_event_row(transaction=transaction, tenant_id=tenant_id, event_id=event_id)

    def reclaim_stale_publishing_events(
        self, *, transaction: Any, tenant_id: str, transition: StatusTransition, claimed_before: str
    ) -> int:
        return cas_status_update_many(
            transaction,
            db.outbox_events,
            tenant_id=tenant_id,
            transition=transition,
            values={"claimed_at": None, "published_at": None},
            conditions=[
                db.outbox_events.c.claimed_at.isnot(None),
                db.outbox_events.c.claimed_at < claimed_before,
            ],
        )

    def mark_outbox_event_published(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        event_id: str,
        transition: StatusTransition,
        published_at: str,
        claimed_at: str,
    ) -> RuntimeRow | None:
        updated = cas_status_update(
            transaction,
            db.outbox_events,
            tenant_id=tenant_id,
            row_id=event_id,
            transition=transition,
            values={"published_at": published_at, "claimed_at": None},
            conditions=[db.outbox_events.c.claimed_at == claimed_at],
        )
        if not updated:
            return None
        return self._outbox_event_row(transaction=transaction, tenant_id=tenant_id, event_id=event_id)

    def mark_outbox_event_failed(
        self, *, transaction: Any, tenant_id: str, event_id: str, transition: StatusTransition, claimed_at: str
    ) -> RuntimeRow | None:
        updated = cas_status_update(
            transaction,
            db.outbox_events,
            tenant_id=tenant_id,
            row_id=event_id,
            transition=transition,
            values={"published_at": None, "claimed_at": None},
            conditions=[db.outbox_events.c.claimed_at == claimed_at],
        )
        if not updated:
            return None
        return self._outbox_event_row(transaction=transaction, tenant_id=tenant_id, event_id=event_id)

    def mark_outbox_event_retry_pending(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        event_id: str,
        transition: StatusTransition,
        claimed_at: str,
    ) -> RuntimeRow | None:
        updated = cas_status_update(
            transaction,
            db.outbox_events,
            tenant_id=tenant_id,
            row_id=event_id,
            transition=transition,
            values={"published_at": None, "claimed_at": None},
            conditions=[db.outbox_events.c.claimed_at == claimed_at],
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
            values={"attempts": 0, "published_at": None, "claimed_at": None},
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
        return runtime_workflow_runs.workflow_run_by_id(
            transaction=transaction, tenant_id=tenant_id, workflow_run_id=workflow_run_id
        )

    def workflow_run_by_idempotency(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        workflow_name: str,
        idempotency_key: str,
    ) -> WorkflowRunRow | None:
        return runtime_workflow_runs.workflow_run_by_idempotency(
            transaction=transaction, tenant_id=tenant_id, workflow_name=workflow_name, idempotency_key=idempotency_key
        )

    def insert_workflow_run_or_get_existing(
        self, *, transaction: Any, record: WorkflowRunRecord
    ) -> WorkflowRunRow | None:
        return runtime_workflow_runs.insert_workflow_run_or_get_existing(transaction=transaction, record=record)

    def update_workflow_run_status(
        self,
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
        return runtime_workflow_runs.update_workflow_run_status(
            transaction=transaction,
            tenant_id=tenant_id,
            workflow_run_id=workflow_run_id,
            transition=transition,
            output=output,
            error=error,
            started_at=started_at,
            completed_at=completed_at,
        )

    def claim_workflow_run_lease(
        self,
        *,
        transaction: Any,
        record: WorkflowRunRecord,
        lease_owner_id: str,
        now: str,
    ) -> WorkflowRunRow | None:
        return runtime_workflow_runs.claim_workflow_run_lease(
            transaction=transaction, record=record, lease_owner_id=lease_owner_id, now=now
        )

    def release_workflow_run_lease(
        self,
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
        return runtime_workflow_runs.release_workflow_run_lease(
            transaction=transaction,
            tenant_id=tenant_id,
            workflow_run_id=workflow_run_id,
            lease_owner_id=lease_owner_id,
            lease_token=lease_token,
            status=status,
            output=output,
            error=error,
            completed_at=completed_at,
        )

    def link_workflow_audit_event(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        workflow_run_id: str,
        audit_event_id: str,
    ) -> WorkflowRunRow | None:
        return runtime_workflow_runs.link_workflow_audit_event(
            transaction=transaction, tenant_id=tenant_id, workflow_run_id=workflow_run_id, audit_event_id=audit_event_id
        )

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
        self._ensure_outbox_tenant_registered(transaction, record)
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

    def _ensure_outbox_tenant_registered(self, transaction: Any, record: OutboxEventRecord) -> None:
        """Keep the publisher's tenant inventory complete in the caller transaction."""
        statement = _tenant_insert_if_absent(record, transaction.dialect.name)
        if statement is not None:
            transaction.execute(statement)
            return
        savepoint = transaction.begin_nested()
        try:
            transaction.execute(
                insert(db.tenants).values(
                    id=record.tenant_id,
                    name=record.tenant_id,
                    created_at=record.created_at,
                )
            )
        except IntegrityError:
            savepoint.rollback()
            winner = transaction.execute(
                select(db.tenants.c.id).where(db.tenants.c.id == record.tenant_id)
            ).scalar_one_or_none()
            if winner is None:
                raise
        else:
            savepoint.commit()

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


# Run tables that lineage edges attribute work to (``created_by_run_id``).
_LINEAGE_RUN_TYPES: tuple[RuntimeRunType, ...] = ("sync", "transform", "index", "materialization", "ai")


def _tenant_insert_if_absent(record: OutboxEventRecord, dialect_name: str) -> Any | None:
    values = {"id": record.tenant_id, "name": record.tenant_id, "created_at": record.created_at}
    if dialect_name == "postgresql":
        return postgresql_insert(db.tenants).values(values).on_conflict_do_nothing(index_elements=("id",))
    if dialect_name == "sqlite":
        return sqlite_insert(db.tenants).values(values).on_conflict_do_nothing(index_elements=("id",))
    return None


def _folded_catalog_edges(
    rows: Sequence[Mapping[str, object]],
    version_to_resource: Mapping[str, tuple[str, str]],
) -> list[LineageEdgeRow]:
    """Collapse per-version edges onto the resources that own them, keeping one edge per pair.

    Two datasets are usually joined by many version edges, one per build. A catalog graph wants
    the single relationship, so the earliest edge for a resource pair wins and self-edges from
    versions of the same dataset are dropped.
    """

    folded: dict[tuple[str, str, str], LineageEdgeRow] = {}
    for row in sorted(rows, key=lambda item: str(item.get("created_at", ""))):
        origin = version_to_resource.get(str(row.get("from_resource_id")))
        target = version_to_resource.get(str(row.get("to_resource_id")))
        if origin is None or target is None or origin[0] == target[0]:
            continue
        relation = str(row.get("relation"))
        key = (origin[0], target[0], relation)
        if key in folded:
            continue
        edge = dict(row)
        edge["from_resource_type"] = "dataset"
        edge["from_resource_id"] = origin[0]
        edge["to_resource_type"] = "dataset"
        edge["to_resource_id"] = target[0]
        folded[key] = cast(LineageEdgeRow, edge)
    return list(folded.values())
