from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from foundry_lite.application.ports import (
    AuditEventRecord,
    DeadLetterEventRecord,
    LineageEdgeRecord,
    LineageEdgeRow,
    ObservabilityIncident,
    OutboxEventRecord,
    RuntimeJsonObject,
    RuntimeLookupTable,
    RuntimeRepository,
    RuntimeRow,
    RuntimeRowsTable,
    RuntimeRunPageCursor,
    RuntimeRunRelationRecord,
    RuntimeRunRelationRow,
    RuntimeRunSnapshot,
    RuntimeRunType,
    StoredObservabilityIncident,
    StoredObservabilityIncidentStatus,
    WorkflowLedgerStatus,
    WorkflowRunRecord,
    WorkflowRunRow,
)
from foundry_lite.application.ports.transaction_context import (
    OUTBOX_PUBLISH_FAILED,
    OUTBOX_PUBLISHED,
    OUTBOX_PUBLISHING,
    OUTBOX_PUBLISHING_RECLAIM,
    OUTBOX_RETRY_PENDING,
    WORKFLOW_RUN_FAILED,
    WORKFLOW_RUN_STARTING,
    WORKFLOW_RUN_SUCCEEDED,
    StatusTransition,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyRuntimeRepository
from sqlalchemy import create_engine, insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError


class RuntimeRepositoryHarness(Protocol):
    repository: RuntimeRepository

    def transaction(self) -> AbstractContextManager[Any]: ...

    def add_transform(self, *, transform_id: str, tenant_id: str) -> None: ...

    def add_materialization(self, *, materialization_id: str, tenant_id: str) -> None: ...

    def add_action_run(
        self,
        *,
        run_id: str,
        tenant_id: str,
        status: str = "SUCCEEDED",
        created_at: str = "2026-06-10T00:00:00Z",
    ) -> None: ...

    def add_dead_letter_event(self, *, event_id: str, tenant_id: str) -> None: ...

    def add_index_run(self, *, run_id: str, tenant_id: str, object_type_api_name: str = "Order") -> None: ...

    def add_ai_run(self, *, run_id: str, tenant_id: str, started_at: str = "2026-06-10T00:00:00Z") -> None: ...

    def add_object_record(self, *, record_id: str, tenant_id: str) -> None: ...


@dataclass
class FakeRuntimeRepository:
    tables: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))

    def lineage_for_resource(self, *, tenant_id: str, resource_id: str) -> list[LineageEdgeRow]:
        return [
            cast(LineageEdgeRow, dict(row))
            for row in self.tables["lineage_edges"]
            if row["tenant_id"] == tenant_id
            and (row["from_resource_id"] == resource_id or row["to_resource_id"] == resource_id)
        ]

    def catalog_lineage_for_resource(self, *, tenant_id: str, resource_id: str) -> list[LineageEdgeRow]:
        """Fold version edges onto owning resources; this fake stores no dataset catalog."""

        return []

    def list_runs(self, *, tenant_id: str, limit: int | None = None) -> RuntimeRunSnapshot:
        def window(table: RuntimeRowsTable) -> list[RuntimeRow]:
            rows = self.rows_for_tenant(transaction=None, table=table, tenant_id=tenant_id)
            if limit is None:
                return rows
            timestamp = _runtime_rows_timestamp(table)
            ordered = sorted(
                rows, key=lambda row: (str(row.get(timestamp) or ""), str(row.get("id") or "")), reverse=True
            )
            return ordered[:limit]

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
        rows = [row for row in self.tables[_run_table_name(run_type)] if row["tenant_id"] == tenant_id]
        rows = [row for row in rows if _matches_run_query(row, run_type, status, since, until, cursor)]
        rows.sort(key=lambda row: (_runtime_timestamp(row, run_type), str(row["id"])), reverse=True)
        return [cast(RuntimeRow, dict(row)) for row in rows[:limit]]

    def row_by_id(self, *, transaction: Any, table: RuntimeLookupTable, row_id: str) -> RuntimeRow | None:
        del transaction
        for row in self.tables[table]:
            if row["id"] == row_id:
                return cast(RuntimeRow, dict(row))
        return None

    def dead_letter_event_by_id(self, *, transaction: Any, tenant_id: str, event_id: str) -> RuntimeRow | None:
        del transaction
        for row in self.tables["dead_letter_events"]:
            if row["tenant_id"] == tenant_id and row["id"] == event_id:
                return cast(RuntimeRow, dict(row))
        return None

    def rows_for_tenant(
        self,
        *,
        transaction: Any,
        table: RuntimeRowsTable,
        tenant_id: str,
        event_types: Sequence[str] | None = None,
    ) -> list[RuntimeRow]:
        del transaction
        rows = [row for row in self.tables[table] if row["tenant_id"] == tenant_id]
        if event_types is not None:
            rows = [row for row in rows if row.get("event_type") in event_types]
        return [cast(RuntimeRow, dict(row)) for row in rows]

    def audit_event_for_resource(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        event_type: str,
        resource_type: str,
        resource_id: str,
    ) -> RuntimeRow | None:
        rows = self.audit_events_for_resources(
            transaction=transaction,
            tenant_id=tenant_id,
            resource_refs=[(resource_type, resource_id)],
            event_types=[event_type],
            limit=1,
        )
        return rows[0] if rows else None

    def audit_events_for_resources(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        resource_refs: Sequence[tuple[str, str]],
        event_types: Sequence[str],
        limit: int,
    ) -> list[RuntimeRow]:
        del transaction
        refs = set(resource_refs)
        types = set(event_types)
        if not refs or not types or limit < 1:
            return []
        rows = [
            row
            for row in self.tables["audit_events"]
            if row["tenant_id"] == tenant_id
            and row["event_type"] in types
            and (str(row["resource_type"]), str(row["resource_id"])) in refs
        ]
        rows.sort(key=lambda row: (str(row["created_at"]), str(row["id"])), reverse=True)
        return [cast(RuntimeRow, dict(row)) for row in rows[:limit]]

    def pending_outbox_events(self, *, transaction: Any, tenant_id: str, limit: int) -> list[RuntimeRow]:
        del transaction
        pending = [
            row for row in self.tables["outbox_events"] if row["tenant_id"] == tenant_id and row["status"] == "pending"
        ]
        pending.sort(key=lambda row: (str(row["created_at"]), str(row["id"])))
        return [cast(RuntimeRow, dict(row)) for row in pending[:limit]]

    def runs_for_source_chain(
        self,
        *,
        tenant_id: str,
        object_type_api_name: str,
        resource_ids: Sequence[str],
        run_ids: Sequence[str],
        limit: int,
    ) -> RuntimeRunSnapshot:
        rids = set(resource_ids)
        runids = set(run_ids)

        def scoped(table: RuntimeRowsTable, predicate: Callable[[dict[str, Any]], bool]) -> list[RuntimeRow]:
            matched = [
                row
                for row in self.tables[table]
                if row["tenant_id"] == tenant_id and (predicate(row) or row.get("id") in runids)
            ]
            timestamp = _runtime_rows_timestamp(table)
            ordered = sorted(
                matched, key=lambda row: (str(row.get(timestamp) or ""), str(row.get("id") or "")), reverse=True
            )
            return [cast(RuntimeRow, dict(row)) for row in ordered[:limit]]

        return {
            "syncRuns": scoped("sync_runs", lambda row: row.get("committed_version_id") in rids),
            "transformRuns": scoped("transform_runs", lambda row: row.get("output_version_id") in rids),
            "indexRuns": scoped("index_runs", lambda row: row.get("object_type_api_name") == object_type_api_name),
            "actionRuns": [],
            "actionWritebacks": [],
            "materializationRuns": scoped(
                "materialization_runs", lambda row: row.get("target_dataset_version_id") in rids
            ),
            "outboxEvents": [],
            "deadLetterEvents": [],
            "workflowRuns": scoped("workflow_runs", lambda _row: False),
            "aiRuns": scoped("ai_execution_runs", lambda _row: False),
            "auditEvents": [],
            "objectEdits": [],
        }

    def run_row(self, *, tenant_id: str, run_type: RuntimeRunType, run_id: str) -> RuntimeRow | None:
        for row in self.tables[_run_table_name(run_type)]:
            if row["tenant_id"] == tenant_id and row["id"] == run_id:
                return cast(RuntimeRow, dict(row))
        return None

    def run_row_any_type(self, *, tenant_id: str, run_id: str) -> tuple[RuntimeRunType, RuntimeRow] | None:
        run_types: tuple[RuntimeRunType, ...] = ("sync", "transform", "index", "materialization", "ai")
        for run_type in run_types:
            row = self.run_row(tenant_id=tenant_id, run_type=run_type, run_id=run_id)
            if row is not None:
                return run_type, row
        return None

    def related_evidence_rows(
        self, *, tenant_id: str, table: RuntimeRowsTable, relation_ids: Sequence[str], limit: int
    ) -> list[RuntimeRow]:
        ids = set(relation_ids)
        matches = [
            cast(RuntimeRow, dict(row))
            for row in self.tables[table]
            if row["tenant_id"] == tenant_id and _row_has_relation(row, ids)
        ]
        return matches[:limit]

    def list_observability_incidents(
        self,
        *,
        tenant_id: str,
        status: StoredObservabilityIncidentStatus | None,
        limit: int,
    ) -> list[StoredObservabilityIncident]:
        rows = [row for row in self.tables["observability_incidents"] if row["tenant_id"] == tenant_id]
        if status is not None:
            rows = [row for row in rows if row["status"] == status]
        rows.sort(key=lambda row: (str(row["lastObservedAt"]), str(row["id"])), reverse=True)
        return [cast(StoredObservabilityIncident, dict(row)) for row in rows[:limit]]

    def observability_incident_by_id(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        incident_id: str,
    ) -> StoredObservabilityIncident | None:
        del transaction
        row = _fake_observability_incident(self.tables["observability_incidents"], tenant_id, incident_id)
        return cast(StoredObservabilityIncident, dict(row)) if row else None

    def upsert_observability_incident(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        incident: ObservabilityIncident,
    ) -> StoredObservabilityIncident:
        del transaction
        existing = _fake_observability_incident_by_dedupe(
            self.tables["observability_incidents"],
            tenant_id,
            incident["dedupeKey"],
        )
        if existing is None:
            row = _stored_observability_row(tenant_id, incident)
            self.tables["observability_incidents"].append(row)
            return cast(StoredObservabilityIncident, dict(row))
        existing.update(_stored_observability_update(incident, existing))
        return cast(StoredObservabilityIncident, dict(existing))

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
        del transaction
        row = _fake_observability_incident(self.tables["observability_incidents"], tenant_id, incident_id)
        if row is None:
            return None
        row.update(_stored_observability_status_update(status, actor_user_id, updated_at, resolution_reason))
        return cast(StoredObservabilityIncident, dict(row))

    def update_outbox_event_for_retry(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        event_id: str,
        transition: StatusTransition,
    ) -> RuntimeRow | None:
        del transaction
        for row in self.tables["outbox_events"]:
            if row["tenant_id"] == tenant_id and row["id"] == event_id and row["status"] in transition.from_statuses:
                row.update(status=transition.to_status, attempts=0, published_at=None)
                return cast(RuntimeRow, dict(row))
        return None

    def mark_outbox_event_publishing(
        self, *, transaction: Any, tenant_id: str, event_id: str, transition: StatusTransition, claimed_at: str
    ) -> RuntimeRow | None:
        del transaction
        for row in self.tables["outbox_events"]:
            if row["tenant_id"] == tenant_id and row["id"] == event_id and row["status"] in transition.from_statuses:
                row.update(status=transition.to_status, attempts=int(row["attempts"]) + 1, claimed_at=claimed_at)
                return cast(RuntimeRow, dict(row))
        return None

    def reclaim_stale_publishing_events(
        self, *, transaction: Any, tenant_id: str, transition: StatusTransition, claimed_before: str
    ) -> int:
        del transaction
        reclaimed = 0
        for row in self.tables["outbox_events"]:
            claimed_at = row.get("claimed_at")
            if (
                row["tenant_id"] == tenant_id
                and row["status"] in transition.from_statuses
                and claimed_at is not None
                and str(claimed_at) < claimed_before
            ):
                row.update(status=transition.to_status, claimed_at=None, published_at=None)
                reclaimed += 1
        return reclaimed

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
        del transaction
        for row in self.tables["outbox_events"]:
            if (
                row["tenant_id"] == tenant_id
                and row["id"] == event_id
                and row["status"] in transition.from_statuses
                and row["claimed_at"] == claimed_at
            ):
                row.update(status=transition.to_status, published_at=published_at, claimed_at=None)
                return cast(RuntimeRow, dict(row))
        return None

    def mark_outbox_event_failed(
        self, *, transaction: Any, tenant_id: str, event_id: str, transition: StatusTransition, claimed_at: str
    ) -> RuntimeRow | None:
        del transaction
        for row in self.tables["outbox_events"]:
            if (
                row["tenant_id"] == tenant_id
                and row["id"] == event_id
                and row["status"] in transition.from_statuses
                and row["claimed_at"] == claimed_at
            ):
                row.update(status=transition.to_status, published_at=None, claimed_at=None)
                return cast(RuntimeRow, dict(row))
        return None

    def workflow_run_by_id(self, *, transaction: Any, tenant_id: str, workflow_run_id: str) -> WorkflowRunRow | None:
        del transaction
        for row in self.tables["workflow_runs"]:
            if row["tenant_id"] == tenant_id and row["id"] == workflow_run_id:
                return cast(WorkflowRunRow, dict(row))
        return None

    def workflow_run_by_idempotency(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        workflow_name: str,
        idempotency_key: str,
    ) -> WorkflowRunRow | None:
        del transaction
        for row in self.tables["workflow_runs"]:
            if _workflow_key_matches(row, tenant_id, workflow_name, idempotency_key):
                return cast(WorkflowRunRow, dict(row))
        return None

    def insert_workflow_run_or_get_existing(
        self, *, transaction: Any, record: WorkflowRunRecord
    ) -> WorkflowRunRow | None:
        del transaction
        existing = self.workflow_run_by_idempotency(
            transaction=None,
            tenant_id=record.tenant_id,
            workflow_name=record.workflow_name,
            idempotency_key=record.idempotency_key,
        )
        if existing is not None:
            return existing
        self.tables["workflow_runs"].append(_workflow_row(record))
        return None

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
        del transaction
        for row in self.tables["workflow_runs"]:
            if (
                row["tenant_id"] == tenant_id
                and row["id"] == workflow_run_id
                and row["status"] in transition.from_statuses
            ):
                row.update(status=transition.to_status, output=dict(output), error=dict(error) if error else None)
                if started_at is not None:
                    row["started_at"] = started_at
                if completed_at is not None:
                    row["completed_at"] = completed_at
                if transition.to_status == "starting":
                    row["attempts"] += 1
                return cast(WorkflowRunRow, dict(row))
        return None

    def claim_workflow_run_lease(
        self,
        *,
        transaction: Any,
        record: WorkflowRunRecord,
        lease_owner_id: str,
        now: str,
    ) -> WorkflowRunRow | None:
        del transaction
        row = self.workflow_run_by_idempotency(
            transaction=None,
            tenant_id=record.tenant_id,
            workflow_name=record.workflow_name,
            idempotency_key=record.idempotency_key,
        )
        if row is None:
            self.tables["workflow_runs"].append(_workflow_row(record))
            return self.workflow_run_by_id(
                transaction=None,
                tenant_id=record.tenant_id,
                workflow_run_id=record.workflow_run_id,
            )
        mutable = _fake_workflow_row(self.tables["workflow_runs"], str(row["id"]))
        if mutable is None or not _workflow_lease_claimable(mutable, lease_owner_id, now):
            return None
        mutable.update(
            status=record.status,
            output=dict(record.output),
            error=dict(record.error) if record.error is not None else None,
            attempts=_workflow_lease_attempts(mutable, lease_owner_id),
            started_at=record.started_at,
            completed_at=None,
        )
        return cast(WorkflowRunRow, dict(mutable))

    def release_workflow_run_lease(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        workflow_run_id: str,
        lease_owner_id: str,
        lease_token: str,
        status: WorkflowLedgerStatus,
        output: RuntimeJsonObject,
        error: RuntimeJsonObject | None,
        completed_at: str,
    ) -> WorkflowRunRow | None:
        del transaction
        row = _fake_workflow_row(self.tables["workflow_runs"], workflow_run_id)
        if (
            row is None
            or row["tenant_id"] != tenant_id
            or not _workflow_lease_matches(row, lease_owner_id, lease_token)
        ):
            return None
        row.update(status=status, output=dict(output), error=dict(error) if error else None, completed_at=completed_at)
        return cast(WorkflowRunRow, dict(row))

    def link_workflow_audit_event(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        workflow_run_id: str,
        audit_event_id: str,
    ) -> WorkflowRunRow | None:
        del transaction
        for row in self.tables["workflow_runs"]:
            if row["tenant_id"] == tenant_id and row["id"] == workflow_run_id:
                row["audit_event_id"] = audit_event_id
                return cast(WorkflowRunRow, dict(row))
        return None

    def delete_dead_letter_event(self, *, transaction: Any, tenant_id: str, event_id: str) -> bool:
        del transaction
        before_count = len(self.tables["dead_letter_events"])
        self.tables["dead_letter_events"] = [
            row for row in self.tables["dead_letter_events"] if row["tenant_id"] != tenant_id or row["id"] != event_id
        ]
        return len(self.tables["dead_letter_events"]) == before_count - 1

    def delete_object_record(self, *, transaction: Any, tenant_id: str, record_id: str) -> bool:
        del transaction
        for row in self.tables["object_records"]:
            if row["tenant_id"] == tenant_id and row["id"] == record_id and not row["deleted"]:
                row.update(
                    deleted=True,
                    is_active=False,
                    deletion_reason="subject_erasure",
                    properties={},
                    base_properties={},
                    edit_properties={},
                    property_versions={},
                )
                return True
        return False

    def insert_audit_event(self, *, transaction: Any, record: AuditEventRecord) -> None:
        del transaction
        self.tables["audit_events"].append(_audit_row(record))

    def insert_outbox_event(self, *, transaction: Any, record: OutboxEventRecord) -> bool:
        del transaction
        duplicate = any(
            row["tenant_id"] == record.tenant_id
            and row["event_type"] == record.event_type
            and row["idempotency_key"] == record.idempotency_key
            for row in self.tables["outbox_events"]
        )
        if duplicate:
            return False
        self.tables["outbox_events"].append(_outbox_row(record))
        return True

    def insert_dead_letter_event(self, *, transaction: Any, record: DeadLetterEventRecord) -> None:
        del transaction
        self.tables["dead_letter_events"].append(_dead_letter_record_row(record))

    def insert_lineage_edge(self, *, transaction: Any, record: LineageEdgeRecord) -> None:
        del transaction
        self.tables["lineage_edges"].append(_lineage_row(record))

    def insert_run_relation(self, *, transaction: Any, record: RuntimeRunRelationRecord) -> bool:
        del transaction
        if any(_relation_key(row) == _relation_record_key(record) for row in self.tables["runtime_run_relations"]):
            return False
        self.tables["runtime_run_relations"].append(_runtime_run_relation_row(record))
        return True

    def run_relations_for_run(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        run_type: RuntimeRunType,
        run_id: str,
    ) -> list[RuntimeRunRelationRow]:
        del transaction
        rows = [
            row
            for row in self.tables["runtime_run_relations"]
            if row["tenant_id"] == tenant_id
            and (
                (row["source_run_type"] == run_type and row["source_run_id"] == run_id)
                or (row["target_run_type"] == run_type and row["target_run_id"] == run_id)
            )
        ]
        rows.sort(key=lambda row: (row["created_at"], row["id"]))
        return [cast(RuntimeRunRelationRow, dict(row)) for row in rows]


@dataclass
class FakeRuntimeRepositoryHarness:
    repository: RuntimeRepository

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        yield None

    def add_transform(self, *, transform_id: str, tenant_id: str) -> None:
        assert isinstance(self.repository, FakeRuntimeRepository)
        self.repository.tables["transforms"].append(_transform_row(transform_id=transform_id, tenant_id=tenant_id))

    def add_materialization(self, *, materialization_id: str, tenant_id: str) -> None:
        assert isinstance(self.repository, FakeRuntimeRepository)
        self.repository.tables["materializations"].append(
            _materialization_row(materialization_id=materialization_id, tenant_id=tenant_id)
        )

    def add_action_run(
        self,
        *,
        run_id: str,
        tenant_id: str,
        status: str = "SUCCEEDED",
        created_at: str = "2026-06-10T00:00:00Z",
    ) -> None:
        assert isinstance(self.repository, FakeRuntimeRepository)
        self.repository.tables["action_runs"].append(
            _action_run_row(run_id=run_id, tenant_id=tenant_id, status=status, created_at=created_at)
        )

    def add_dead_letter_event(self, *, event_id: str, tenant_id: str) -> None:
        assert isinstance(self.repository, FakeRuntimeRepository)
        self.repository.tables["dead_letter_events"].append(_dead_letter_row(event_id=event_id, tenant_id=tenant_id))

    def add_index_run(self, *, run_id: str, tenant_id: str, object_type_api_name: str = "Order") -> None:
        assert isinstance(self.repository, FakeRuntimeRepository)
        self.repository.tables["index_runs"].append(
            _index_run_row(run_id=run_id, tenant_id=tenant_id, object_type_api_name=object_type_api_name)
        )

    def add_ai_run(self, *, run_id: str, tenant_id: str, started_at: str = "2026-06-10T00:00:00Z") -> None:
        assert isinstance(self.repository, FakeRuntimeRepository)
        self.repository.tables["ai_execution_runs"].append(
            _ai_run_row(run_id=run_id, tenant_id=tenant_id, started_at=started_at)
        )

    def add_object_record(self, *, record_id: str, tenant_id: str) -> None:
        assert isinstance(self.repository, FakeRuntimeRepository)
        self.repository.tables["object_records"].append(_object_record_row(record_id=record_id, tenant_id=tenant_id))


@dataclass
class SqlAlchemyRuntimeRepositoryHarness:
    repository: RuntimeRepository
    engine: Engine

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self.engine.begin() as conn:
            yield conn

    def add_transform(self, *, transform_id: str, tenant_id: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(db.transforms).values(**_transform_row(transform_id=transform_id, tenant_id=tenant_id)))

    def add_materialization(self, *, materialization_id: str, tenant_id: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                insert(db.materializations).values(
                    **_materialization_row(materialization_id=materialization_id, tenant_id=tenant_id)
                )
            )

    def add_action_run(
        self,
        *,
        run_id: str,
        tenant_id: str,
        status: str = "SUCCEEDED",
        created_at: str = "2026-06-10T00:00:00Z",
    ) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                insert(db.action_runs).values(
                    **_action_run_row(run_id=run_id, tenant_id=tenant_id, status=status, created_at=created_at)
                )
            )

    def add_dead_letter_event(self, *, event_id: str, tenant_id: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                insert(db.dead_letter_events).values(**_dead_letter_row(event_id=event_id, tenant_id=tenant_id))
            )

    def add_index_run(self, *, run_id: str, tenant_id: str, object_type_api_name: str = "Order") -> None:
        with self.engine.begin() as conn:
            conn.execute(
                insert(db.index_runs).values(
                    **_index_run_row(run_id=run_id, tenant_id=tenant_id, object_type_api_name=object_type_api_name)
                )
            )

    def add_ai_run(self, *, run_id: str, tenant_id: str, started_at: str = "2026-06-10T00:00:00Z") -> None:
        with self.engine.begin() as conn:
            conn.execute(
                insert(db.ai_execution_runs).values(
                    **_ai_run_row(run_id=run_id, tenant_id=tenant_id, started_at=started_at)
                )
            )

    def add_object_record(self, *, record_id: str, tenant_id: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                insert(db.object_records).values(**_object_record_row(record_id=record_id, tenant_id=tenant_id))
            )


def _audit_record(
    *,
    event_id: str = "audit_1",
    tenant_id: str = "tenant-demo",
    event_type: str = "permission.denied",
    resource_type: str = "dataset",
    resource_id: str = "raw.orders",
    created_at: str = "2026-06-10T00:00:00Z",
) -> AuditEventRecord:
    return AuditEventRecord(
        event_id=event_id,
        tenant_id=tenant_id,
        actor_user_id="user-demo",
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        action="dataset:write",
        decision="deny",
        policy_decision={},
        before_ref={},
        after_ref={"reason": "missing role"},
        correlation_id="req-demo",
        request_id="req-demo",
        metadata={},
        created_at=created_at,
    )


def _outbox_record(
    *,
    event_id: str = "outbox_1",
    tenant_id: str = "tenant-demo",
    status: str = "pending",
    attempts: int = 0,
    published_at: str | None = None,
    idempotency_key: str = "dsv_1",
    created_at: str = "2026-06-10T00:00:00Z",
) -> OutboxEventRecord:
    return OutboxEventRecord(
        event_id=event_id,
        tenant_id=tenant_id,
        event_type="dataset.version.committed",
        aggregate_type="dataset_version",
        aggregate_id="dsv_1",
        payload={"versionId": "dsv_1"},
        status=status,
        attempts=attempts,
        idempotency_key=idempotency_key,
        correlation_id="run_1",
        created_at=created_at,
        published_at=published_at,
    )


def _lineage_record(*, edge_id: str = "lineage_1", tenant_id: str = "tenant-demo") -> LineageEdgeRecord:
    return LineageEdgeRecord(
        edge_id=edge_id,
        tenant_id=tenant_id,
        from_resource_type="dataset_version",
        from_resource_id="dsv_input",
        to_resource_type="dataset_version",
        to_resource_id="dsv_output",
        relation="input_to",
        created_by_run_id="run_1",
        created_at="2026-06-10T00:00:00Z",
    )


def _run_relation_record(
    *,
    relation_id: str = "run_relation_1",
    tenant_id: str = "tenant-demo",
    source_run_id: str = "action_run_1",
    target_run_id: str = "outbox_1",
) -> RuntimeRunRelationRecord:
    return RuntimeRunRelationRecord(
        relation_id=relation_id,
        tenant_id=tenant_id,
        source_run_type="action",
        source_run_id=source_run_id,
        target_run_type="outbox",
        target_run_id=target_run_id,
        relation="emitted",
        resource_type="object",
        resource_id="Order:O-1",
        metadata={"reason": "action commit outbox"},
        created_at="2026-06-10T00:00:00Z",
    )


def _observability_incident(
    *,
    incident_id: str = "incident:raw-orders-flow:flow_interruption:dataset:raw.orders",
    observed_at: str = "2026-06-19T00:15:00Z",
    dedupe_key: str = "raw-orders-flow:flow_interruption:dataset:raw.orders",
) -> ObservabilityIncident:
    return {
        "id": incident_id,
        "detectorId": "raw-orders-flow",
        "detectorType": "flow_interruption",
        "configVersion": "s56-v1",
        "status": "active",
        "severity": "warning",
        "owner": "data-platform",
        "message": "missing data beyond expected cadence",
        "dedupeKey": dedupe_key,
        "firstObservedAt": observed_at,
        "lastObservedAt": observed_at,
        "evidence": {"failedRunCount": 0},
        "evidenceLinks": [],
        "threshold": {"expectedCadenceSeconds": 300},
    }


def _stored_observability_row(tenant_id: str, incident: ObservabilityIncident) -> dict[str, Any]:
    return {
        "id": incident["id"],
        "tenant_id": tenant_id,
        "detectorId": incident["detectorId"],
        "detectorType": incident["detectorType"],
        "configVersion": incident["configVersion"],
        "status": "open",
        "severity": incident["severity"],
        "owner": incident["owner"],
        "message": incident["message"],
        "dedupeKey": incident["dedupeKey"],
        "firstObservedAt": incident["firstObservedAt"],
        "lastObservedAt": incident["lastObservedAt"],
        "occurrenceCount": 1,
        "evidence": dict(incident["evidence"]),
        "evidenceLinks": list(incident["evidenceLinks"]),
        "threshold": dict(incident["threshold"]),
        "acknowledgedAt": None,
        "acknowledgedBy": None,
        "resolvedAt": None,
        "resolvedBy": None,
        "resolutionReason": None,
    }


def _stored_observability_update(
    incident: ObservabilityIncident,
    existing: dict[str, Any],
) -> dict[str, Any]:
    increment = 0 if existing["lastObservedAt"] == incident["lastObservedAt"] else 1
    values = {
        "lastObservedAt": incident["lastObservedAt"],
        "occurrenceCount": int(existing["occurrenceCount"]) + increment,
        "evidence": dict(incident["evidence"]),
        "evidenceLinks": list(incident["evidenceLinks"]),
        "threshold": dict(incident["threshold"]),
        "status": existing["status"],
    }
    if existing["status"] == "resolved":
        values.update(status="open", resolvedAt=None, resolvedBy=None, resolutionReason=None)
    return values


def _stored_observability_status_update(
    status: StoredObservabilityIncidentStatus,
    actor_user_id: str,
    updated_at: str,
    resolution_reason: str | None,
) -> dict[str, Any]:
    values: dict[str, Any] = {"status": status}
    if status == "acknowledged":
        values.update(acknowledgedAt=updated_at, acknowledgedBy=actor_user_id)
    if status == "resolved":
        values.update(resolvedAt=updated_at, resolvedBy=actor_user_id, resolutionReason=resolution_reason)
    return values


def _fake_observability_incident(
    rows: list[dict[str, Any]],
    tenant_id: str,
    incident_id: str,
) -> dict[str, Any] | None:
    return next((row for row in rows if row["tenant_id"] == tenant_id and row["id"] == incident_id), None)


def _fake_observability_incident_by_dedupe(
    rows: list[dict[str, Any]],
    tenant_id: str,
    dedupe_key: str,
) -> dict[str, Any] | None:
    return next((row for row in rows if row["tenant_id"] == tenant_id and row["dedupeKey"] == dedupe_key), None)


def _workflow_record_obj(
    *,
    workflow_run_id: str = "flite:workflow:run:1",
    tenant_id: str = "tenant-demo",
    idempotency_key: str = "workflow-key-1",
    workflow_name: str = "ConnectorSyncWorkflow",
    status: WorkflowLedgerStatus = "requested",
    output: RuntimeJsonObject | None = None,
    attempts: int = 0,
    started_at: str | None = None,
) -> WorkflowRunRecord:
    return WorkflowRunRecord(
        workflow_run_id=workflow_run_id,
        tenant_id=tenant_id,
        workflow_name=workflow_name,
        workflow_profile="local",
        status=status,
        idempotency_key=idempotency_key,
        request_fingerprint=f"fingerprint-{idempotency_key}",
        input={"datasetRef": "raw.orders"},
        output=output or {},
        error=None,
        dataset_id="ds_orders",
        audit_event_id=None,
        attempts=attempts,
        created_at="2026-06-10T00:00:00Z",
        started_at=started_at,
        completed_at=None,
    )


def _lease_output(owner_id: str, lease_token: str, lease_expires_at: str) -> RuntimeJsonObject:
    return {
        "workerLease": {
            "ownerId": owner_id,
            "leaseToken": lease_token,
            "leaseExpiresAt": lease_expires_at,
            "lastHeartbeatAt": "2026-06-10T00:00:00Z",
        }
    }


def _audit_row(record: AuditEventRecord) -> dict[str, Any]:
    return {
        "id": record.event_id,
        "tenant_id": record.tenant_id,
        "actor_user_id": record.actor_user_id,
        "event_type": record.event_type,
        "resource_type": record.resource_type,
        "resource_id": record.resource_id,
        "action": record.action,
        "decision": record.decision,
        "policy_decision": record.policy_decision,
        "before_ref": record.before_ref,
        "after_ref": record.after_ref,
        "correlation_id": record.correlation_id,
        "request_id": record.request_id,
        "metadata": record.metadata,
        "created_at": record.created_at,
    }


def _outbox_row(record: OutboxEventRecord) -> dict[str, Any]:
    return {
        "id": record.event_id,
        "tenant_id": record.tenant_id,
        "event_type": record.event_type,
        "aggregate_type": record.aggregate_type,
        "aggregate_id": record.aggregate_id,
        "payload": record.payload,
        "status": record.status,
        "attempts": record.attempts,
        "idempotency_key": record.idempotency_key,
        "correlation_id": record.correlation_id,
        "created_at": record.created_at,
        "published_at": record.published_at,
    }


def _lineage_row(record: LineageEdgeRecord) -> dict[str, Any]:
    return {
        "id": record.edge_id,
        "tenant_id": record.tenant_id,
        "from_resource_type": record.from_resource_type,
        "from_resource_id": record.from_resource_id,
        "to_resource_type": record.to_resource_type,
        "to_resource_id": record.to_resource_id,
        "relation": record.relation,
        "created_by_run_id": record.created_by_run_id,
        "created_at": record.created_at,
    }


def _runtime_run_relation_row(record: RuntimeRunRelationRecord) -> dict[str, Any]:
    return {
        "id": record.relation_id,
        "tenant_id": record.tenant_id,
        "source_run_type": record.source_run_type,
        "source_run_id": record.source_run_id,
        "target_run_type": record.target_run_type,
        "target_run_id": record.target_run_id,
        "relation": record.relation,
        "resource_type": record.resource_type,
        "resource_id": record.resource_id,
        "metadata": dict(record.metadata),
        "created_at": record.created_at,
    }


def _relation_key(row: dict[str, Any]) -> tuple[object, ...]:
    return (
        row["tenant_id"],
        row["source_run_type"],
        row["source_run_id"],
        row["target_run_type"],
        row["target_run_id"],
        row["relation"],
        row["resource_type"],
        row["resource_id"],
    )


def _relation_record_key(record: RuntimeRunRelationRecord) -> tuple[object, ...]:
    return (
        record.tenant_id,
        record.source_run_type,
        record.source_run_id,
        record.target_run_type,
        record.target_run_id,
        record.relation,
        record.resource_type,
        record.resource_id,
    )


def _workflow_row(record: WorkflowRunRecord) -> dict[str, Any]:
    return {
        "id": record.workflow_run_id,
        "tenant_id": record.tenant_id,
        "workflow_name": record.workflow_name,
        "workflow_profile": record.workflow_profile,
        "status": record.status,
        "idempotency_key": record.idempotency_key,
        "request_fingerprint": record.request_fingerprint,
        "input": record.input,
        "output": record.output,
        "error": record.error,
        "dataset_id": record.dataset_id,
        "audit_event_id": record.audit_event_id,
        "attempts": record.attempts,
        "created_at": record.created_at,
        "started_at": record.started_at,
        "completed_at": record.completed_at,
    }


def _workflow_key_matches(row: dict[str, Any], tenant_id: str, workflow_name: str, idempotency_key: str) -> bool:
    return (
        row["tenant_id"] == tenant_id
        and row["workflow_name"] == workflow_name
        and row["idempotency_key"] == idempotency_key
    )


def _fake_workflow_row(rows: list[dict[str, Any]], workflow_run_id: str) -> dict[str, Any] | None:
    for row in rows:
        if row["id"] == workflow_run_id:
            return row
    return None


def _workflow_lease_claimable(row: dict[str, Any], lease_owner_id: str, now: str) -> bool:
    if row["status"] in {"succeeded", "failed", "cancelled", "start_unknown"}:
        return True
    lease = _workflow_lease(row)
    if lease is None:
        return False
    if lease.get("ownerId") == lease_owner_id:
        return True
    expires_at = lease.get("leaseExpiresAt")
    return isinstance(expires_at, str) and expires_at <= now


def _workflow_lease_matches(row: dict[str, Any], lease_owner_id: str, lease_token: str) -> bool:
    lease = _workflow_lease(row)
    return lease is not None and lease.get("ownerId") == lease_owner_id and lease.get("leaseToken") == lease_token


def _workflow_lease_attempts(row: dict[str, Any], lease_owner_id: str) -> int:
    attempts = row.get("attempts")
    current = attempts if isinstance(attempts, int) and not isinstance(attempts, bool) else 0
    lease = _workflow_lease(row)
    if row["status"] == "running" and lease is not None and lease.get("ownerId") == lease_owner_id:
        return current
    return current + 1


def _workflow_lease(row: dict[str, Any]) -> RuntimeJsonObject | None:
    output = row.get("output")
    if not isinstance(output, dict):
        return None
    lease = output.get("workerLease")
    return cast(RuntimeJsonObject, lease) if isinstance(lease, dict) else None


def _ai_run_row(*, run_id: str, tenant_id: str, started_at: str) -> dict[str, Any]:
    return {
        "id": run_id,
        "tenant_id": tenant_id,
        "session_id": "ai-session-1",
        "agent_version_id": "agent-v1",
        "actor_user_id": "ops-user",
        "request_id": "request-1",
        "trace_id": "trace-1",
        "status": "succeeded",
        "ontology_version_id": "ontology-v1",
        "model_alias_version": "alias-v1",
        "resolved_model_id": "model-1",
        "resolved_model_revision": "revision-1",
        "prompt_version_id": "prompt-v1",
        "compiled_prompt_hash": "sha256:compiled",
        "tool_manifest_hash": "sha256:tools",
        "context_manifest_hash": "sha256:context",
        "state_snapshot_hash": "sha256:state",
        "policy_snapshot_hash": "sha256:policy",
        "budget_json": {"maxInputTokens": 1024},
        "usage_json": {"inputTokens": 12},
        "error_json": None,
        "started_at": started_at,
        "completed_at": started_at,
    }


def _dead_letter_row(*, event_id: str, tenant_id: str) -> dict[str, Any]:
    return {
        "id": event_id,
        "tenant_id": tenant_id,
        "source_event_id": "outbox_1",
        "event_type": "dataset.version.committed",
        "payload": {"versionId": "dsv_1"},
        "error": {"message": "publisher failed"},
        "failed_at": "2026-06-10T00:00:02Z",
        "retry_after": None,
    }


def _dead_letter_record_row(record: DeadLetterEventRecord) -> dict[str, Any]:
    return {
        "id": record.event_id,
        "tenant_id": record.tenant_id,
        "source_event_id": record.source_event_id,
        "event_type": record.event_type,
        "payload": dict(record.payload),
        "error": dict(record.error),
        "failed_at": record.failed_at,
        "retry_after": record.retry_after,
    }


def _object_record_row(*, record_id: str, tenant_id: str) -> dict[str, Any]:
    return {
        "id": record_id,
        "tenant_id": tenant_id,
        "object_type_id": "ot_order",
        "object_type_api_name": "Order",
        "object_id": "O-1",
        "index_version": "active",
        "is_active": True,
        "properties": {"email": "ada@example.com"},
        "base_properties": {"email": "ada@example.com"},
        "edit_properties": {"email": "ada@example.com"},
        "property_versions": {"email": 1},
        "source_dataset_version_id": "dsv_1",
        "source_hash": "hash_1",
        "object_version": 1,
        "object_change_sequence": 1,
        "deleted": False,
        "deletion_reason": None,
        "created_at": "2026-06-10T00:00:00Z",
        "updated_at": "2026-06-10T00:00:00Z",
    }


def _row_has_relation(row: dict[str, Any], ids: set[str]) -> bool:
    return any(
        value in ids
        for key, value in row.items()
        if key != "tenant_id" and (key.endswith("_id") or key in {"id", "correlation_id"}) and isinstance(value, str)
    )


def _run_table_name(run_type: RuntimeRunType) -> str:
    return {
        "sync": "sync_runs",
        "transform": "transform_runs",
        "index": "index_runs",
        "action": "action_runs",
        "action_writeback": "action_writebacks",
        "materialization": "materialization_runs",
        "outbox": "outbox_events",
        "dead_letter": "dead_letter_events",
        "workflow": "workflow_runs",
        "ai": "ai_execution_runs",
        "audit": "audit_events",
    }[run_type]


def _runtime_rows_timestamp(table: RuntimeRowsTable) -> str:
    if table == "dead_letter_events":
        return "failed_at"
    if table == "ai_execution_runs":
        return "started_at"
    return "created_at"


def _matches_run_query(
    row: dict[str, Any],
    run_type: RuntimeRunType,
    status: str | None,
    since: str | None,
    until: str | None,
    cursor: RuntimeRunPageCursor | None,
) -> bool:
    timestamp = _runtime_timestamp(row, run_type)
    if status and _runtime_status(row, run_type).lower() != status.lower():
        return False
    if since and timestamp < since:
        return False
    if until and timestamp > until:
        return False
    if cursor is not None:
        return (timestamp, str(row["id"])) < (cursor["timestamp"], cursor["run_id"])
    return True


def _runtime_timestamp(row: dict[str, Any], run_type: RuntimeRunType) -> str:
    if run_type == "ai":
        return str(row["started_at"])
    key = "failed_at" if run_type == "dead_letter" else "created_at"
    return str(row[key])


def _runtime_status(row: dict[str, Any], run_type: RuntimeRunType) -> str:
    if run_type == "dead_letter":
        return "dead_lettered"
    if run_type == "audit":
        return str(row["decision"])
    return str(row["status"])


def _transform_row(*, transform_id: str, tenant_id: str) -> dict[str, Any]:
    return {
        "id": transform_id,
        "tenant_id": tenant_id,
        "api_name": "clean_orders",
        "language": "sql",
        "entrypoint": "transforms/clean_orders.sql",
        "mode": "snapshot",
        "inputs": {"orders": "raw.orders"},
        "output_dataset_ref": "clean.orders",
        "checks": [],
    }


def _materialization_row(*, materialization_id: str, tenant_id: str) -> dict[str, Any]:
    return {
        "id": materialization_id,
        "tenant_id": tenant_id,
        "api_name": "action_log",
        "materialization_type": "action_log",
        "source_ref": {"type": "action_runs"},
        "target_ref": {"dataset": "ops.action_log"},
        "trigger_config": {"type": "manual"},
        "enabled": True,
    }


def _action_run_row(
    *,
    run_id: str,
    tenant_id: str,
    status: str = "SUCCEEDED",
    created_at: str = "2026-06-10T00:00:00Z",
) -> dict[str, Any]:
    return {
        "id": run_id,
        "tenant_id": tenant_id,
        "action_type_id": "act_1",
        "action_type_api_name": "ApproveOrder",
        "actor_user_id": "user-demo",
        "target_object_type_id": "ot_1",
        "target_object_type_api_name": "Order",
        "target_object_id": "O-1",
        "expected_object_version": 1,
        "parameters": {"reason": "ok"},
        "status": status,
        "idempotency_key": run_id,
        "request_fingerprint": f"fingerprint-{run_id}",
        "error": None,
        "created_at": created_at,
        "completed_at": "2026-06-10T00:00:01Z",
    }


def _index_run_row(
    *,
    run_id: str,
    tenant_id: str,
    object_type_api_name: str = "Order",
    created_at: str = "2026-06-10T00:00:00Z",
) -> dict[str, Any]:
    return {
        "id": run_id,
        "tenant_id": tenant_id,
        "object_type_id": "ot_1",
        "object_type_api_name": object_type_api_name,
        "trigger_type": "manual",
        "source_ref": {"dataset": "clean.orders"},
        "status": "SUCCEEDED",
        "rows_read": 0,
        "objects_upserted": 0,
        "objects_deleted": 0,
        "links_upserted": 0,
        "created_at": created_at,
    }


@pytest.fixture(params=["sqlalchemy", "fake", "postgres"])
def harness(request: pytest.FixtureRequest, tmp_path: Path) -> RuntimeRepositoryHarness:
    if request.param == "fake":
        return FakeRuntimeRepositoryHarness(FakeRuntimeRepository())
    if request.param == "sqlalchemy":
        engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}", future=True)
        db.create_database(engine)
        return SqlAlchemyRuntimeRepositoryHarness(SqlAlchemyRuntimeRepository(engine), engine)
    postgres_fixture = request.getfixturevalue("postgres_fixture")
    return SqlAlchemyRuntimeRepositoryHarness(
        SqlAlchemyRuntimeRepository(postgres_fixture.engine),
        postgres_fixture.engine,
    )


def test_runtime_repository_contract_audit_outbox_idempotency_and_list_runs(
    harness: RuntimeRepositoryHarness,
) -> None:
    harness.add_action_run(run_id="action_run_1", tenant_id="tenant-demo")
    harness.add_dead_letter_event(event_id="dlq_1", tenant_id="tenant-demo")
    harness.add_dead_letter_event(event_id="dlq_other", tenant_id="tenant-other")
    with harness.transaction() as transaction:
        harness.repository.insert_audit_event(transaction=transaction, record=_audit_record())
        first_insert = harness.repository.insert_outbox_event(transaction=transaction, record=_outbox_record())
        duplicate_insert = harness.repository.insert_outbox_event(
            transaction=transaction,
            record=_outbox_record(event_id="outbox_2"),
        )

    runs = harness.repository.list_runs(tenant_id="tenant-demo")

    assert first_insert is True
    assert duplicate_insert is False
    assert [row["id"] for row in runs["actionRuns"]] == ["action_run_1"]
    assert [row["id"] for row in runs["auditEvents"]] == ["audit_1"]
    assert [row["id"] for row in runs["outboxEvents"]] == ["outbox_1"]
    assert [row["id"] for row in runs["deadLetterEvents"]] == ["dlq_1"]


def test_runtime_repository_contract_audit_resource_window_is_exact_bounded_and_tenant_scoped(
    harness: RuntimeRepositoryHarness,
) -> None:
    records = [
        _audit_record(
            event_id="target-old",
            event_type="governed_release.action.started",
            resource_type="governed_release_proposal",
            resource_id="proposal-1",
            created_at="2026-06-10T00:00:01Z",
        ),
        _audit_record(
            event_id="target-new",
            event_type="governed_release.action.succeeded",
            resource_type="governed_release_proposal",
            resource_id="proposal-1",
            created_at="2026-06-10T00:00:02Z",
        ),
        _audit_record(
            event_id="wrong-resource",
            event_type="governed_release.action.succeeded",
            resource_type="pipeline_release_proposal",
            resource_id="proposal-1",
            created_at="2026-06-10T00:00:03Z",
        ),
        _audit_record(
            event_id="wrong-event",
            event_type="permission.denied",
            resource_type="governed_release_proposal",
            resource_id="proposal-1",
            created_at="2026-06-10T00:00:04Z",
        ),
        _audit_record(
            event_id="wrong-tenant",
            tenant_id="tenant-other",
            event_type="governed_release.action.succeeded",
            resource_type="governed_release_proposal",
            resource_id="proposal-1",
            created_at="2026-06-10T00:00:05Z",
        ),
    ]
    with harness.transaction() as transaction:
        for record in records:
            harness.repository.insert_audit_event(transaction=transaction, record=record)
        rows = harness.repository.audit_events_for_resources(
            transaction=transaction,
            tenant_id="tenant-demo",
            resource_refs=[("governed_release_proposal", "proposal-1")],
            event_types=["governed_release.action.started", "governed_release.action.succeeded"],
            limit=1,
        )

    assert [row["id"] for row in rows] == ["target-new"]


def test_runtime_repository_contract_list_runs_windows_to_recent_rows(
    harness: RuntimeRepositoryHarness,
) -> None:
    harness.add_action_run(run_id="run-old", tenant_id="tenant-demo", created_at="2026-06-10T00:00:01Z")
    harness.add_action_run(run_id="run-mid", tenant_id="tenant-demo", created_at="2026-06-10T00:00:02Z")
    harness.add_action_run(run_id="run-new", tenant_id="tenant-demo", created_at="2026-06-10T00:00:03Z")

    # A bounded window returns only the most recent rows, newest first.
    windowed = harness.repository.list_runs(tenant_id="tenant-demo", limit=2)
    assert [row["id"] for row in windowed["actionRuns"]] == ["run-new", "run-mid"]

    # Without a limit the full per-tenant history is returned.
    full = harness.repository.list_runs(tenant_id="tenant-demo")
    assert {row["id"] for row in full["actionRuns"]} == {"run-old", "run-mid", "run-new"}


def test_runtime_repository_contract_runs_for_source_chain_scopes_rows(
    harness: RuntimeRepositoryHarness,
) -> None:
    harness.add_index_run(run_id="idx-order", tenant_id="tenant-demo", object_type_api_name="Order")
    harness.add_index_run(run_id="idx-prod", tenant_id="tenant-demo", object_type_api_name="Product")
    harness.add_index_run(run_id="idx-other", tenant_id="tenant-other", object_type_api_name="Order")
    harness.add_ai_run(run_id="ai-run-source", tenant_id="tenant-demo", started_at="2026-06-10T00:00:03Z")

    scoped = harness.repository.runs_for_source_chain(
        tenant_id="tenant-demo",
        object_type_api_name="Order",
        resource_ids=["dataset-version-1"],
        run_ids=["idx-prod", "ai-run-source"],
        limit=50,
    )

    index_ids = {row["id"] for row in scoped["indexRuns"]}
    # "Order" index run by object type; "Product" index run pulled in by run_ids.
    assert index_ids == {"idx-order", "idx-prod"}
    # Other tenants never leak into a scoped snapshot.
    assert "idx-other" not in index_ids
    # Producer/lineage tables resolve (no matching rows here) and unrelated tables stay empty.
    assert scoped["syncRuns"] == []
    assert scoped["workflowRuns"] == []
    assert [row["id"] for row in scoped["aiRuns"]] == ["ai-run-source"]
    assert scoped["auditEvents"] == []
    assert scoped["outboxEvents"] == []


def test_runtime_repository_contract_run_row_and_evidence_pushdown(
    harness: RuntimeRepositoryHarness,
) -> None:
    harness.add_action_run(run_id="action_run_1", tenant_id="tenant-demo")
    harness.add_index_run(run_id="index_run_1", tenant_id="tenant-demo")
    with harness.transaction() as transaction:
        harness.repository.insert_audit_event(transaction=transaction, record=_audit_record())

    found = harness.repository.run_row(tenant_id="tenant-demo", run_type="action", run_id="action_run_1")
    assert found is not None and found["id"] == "action_run_1"
    assert harness.repository.run_row(tenant_id="tenant-demo", run_type="action", run_id="missing") is None
    assert harness.repository.run_row(tenant_id="tenant-other", run_type="action", run_id="action_run_1") is None

    resolved = harness.repository.run_row_any_type(tenant_id="tenant-demo", run_id="index_run_1")
    assert resolved is not None and resolved[0] == "index" and resolved[1]["id"] == "index_run_1"
    # ``action`` is not a lineage-producing run table, so it never resolves here.
    assert harness.repository.run_row_any_type(tenant_id="tenant-demo", run_id="action_run_1") is None

    by_correlation = harness.repository.related_evidence_rows(
        tenant_id="tenant-demo", table="audit_events", relation_ids=["req-demo"], limit=10
    )
    assert [row["id"] for row in by_correlation] == ["audit_1"]
    # The tenant scope must never correlate evidence, even though every row shares it.
    assert (
        harness.repository.related_evidence_rows(
            tenant_id="tenant-demo", table="audit_events", relation_ids=["tenant-demo"], limit=10
        )
        == []
    )
    assert (
        harness.repository.related_evidence_rows(
            tenant_id="tenant-demo", table="audit_events", relation_ids=[], limit=10
        )
        == []
    )


def test_runtime_repository_contract_outbox_reraises_unrelated_integrity_errors(
    harness: RuntimeRepositoryHarness,
) -> None:
    if isinstance(harness, FakeRuntimeRepositoryHarness):
        return
    with harness.transaction() as transaction:
        assert harness.repository.insert_outbox_event(transaction=transaction, record=_outbox_record())
        with pytest.raises(IntegrityError):
            harness.repository.insert_outbox_event(
                transaction=transaction,
                record=_outbox_record(event_id="outbox_1", idempotency_key="dsv_2"),
            )


def test_runtime_repository_contract_queries_run_rows_with_keyset_page(
    harness: RuntimeRepositoryHarness,
) -> None:
    harness.add_action_run(
        run_id="action_a",
        tenant_id="tenant-demo",
        created_at="2026-06-10T00:00:01Z",
    )
    harness.add_action_run(
        run_id="action_b",
        tenant_id="tenant-demo",
        created_at="2026-06-10T00:00:02Z",
    )
    harness.add_action_run(
        run_id="action_c",
        tenant_id="tenant-demo",
        created_at="2026-06-10T00:00:02Z",
    )
    harness.add_action_run(
        run_id="action_failed",
        tenant_id="tenant-demo",
        status="FAILED",
        created_at="2026-06-10T00:00:03Z",
    )
    harness.add_action_run(run_id="action_other", tenant_id="tenant-other")

    first = harness.repository.query_run_rows(
        tenant_id="tenant-demo",
        run_type="action",
        status="succeeded",
        since="2026-06-10T00:00:00Z",
        until="2026-06-10T00:00:03Z",
        cursor=None,
        limit=1,
    )
    second = harness.repository.query_run_rows(
        tenant_id="tenant-demo",
        run_type="action",
        status="succeeded",
        since="2026-06-10T00:00:00Z",
        until="2026-06-10T00:00:03Z",
        cursor={"timestamp": str(first[-1]["created_at"]), "run_id": str(first[-1]["id"])},
        limit=2,
    )

    assert [row["id"] for row in first] == ["action_c"]
    assert [row["id"] for row in second] == ["action_b", "action_a"]


def test_runtime_repository_contract_lineage_lookup_is_tenant_scoped(
    harness: RuntimeRepositoryHarness,
) -> None:
    with harness.transaction() as transaction:
        harness.repository.insert_lineage_edge(transaction=transaction, record=_lineage_record())
        harness.repository.insert_lineage_edge(
            transaction=transaction,
            record=_lineage_record(edge_id="lineage_other", tenant_id="tenant-other"),
        )

    by_input = harness.repository.lineage_for_resource(tenant_id="tenant-demo", resource_id="dsv_input")
    by_output = harness.repository.lineage_for_resource(tenant_id="tenant-demo", resource_id="dsv_output")
    other_tenant = harness.repository.lineage_for_resource(tenant_id="tenant-other", resource_id="dsv_input")

    assert [row["id"] for row in by_input] == ["lineage_1"]
    assert [row["id"] for row in by_output] == ["lineage_1"]
    assert [row["id"] for row in other_tenant] == ["lineage_other"]


def test_runtime_repository_contract_catalog_lineage_is_empty_without_a_dataset_resource(
    harness: RuntimeRepositoryHarness,
) -> None:
    """Catalog lineage is keyed by the resource that owns a version, not by the version id.

    Regression: `resource.search` hands back catalog resource ids while edges are stored per
    dataset version, so passing a search result straight to the graph returned nothing. An id
    that owns no dataset version must fold to an empty graph rather than leaking raw version
    edges.
    """

    with harness.transaction() as transaction:
        harness.repository.insert_lineage_edge(transaction=transaction, record=_lineage_record())

    version_level = harness.repository.lineage_for_resource(tenant_id="tenant-demo", resource_id="dsv_input")
    catalog_level = harness.repository.catalog_lineage_for_resource(
        tenant_id="tenant-demo", resource_id="resource_unknown"
    )

    assert [row["id"] for row in version_level] == ["lineage_1"]
    assert catalog_level == []


def test_runtime_repository_contract_run_relations_are_tenant_scoped_and_idempotent(
    harness: RuntimeRepositoryHarness,
) -> None:
    with harness.transaction() as transaction:
        inserted = harness.repository.insert_run_relation(transaction=transaction, record=_run_relation_record())
        duplicate = harness.repository.insert_run_relation(
            transaction=transaction,
            record=_run_relation_record(relation_id="run_relation_duplicate"),
        )
        other_tenant = harness.repository.insert_run_relation(
            transaction=transaction,
            record=_run_relation_record(relation_id="run_relation_other", tenant_id="tenant-other"),
        )

    with harness.transaction() as transaction:
        by_source = harness.repository.run_relations_for_run(
            transaction=transaction,
            tenant_id="tenant-demo",
            run_type="action",
            run_id="action_run_1",
        )
        by_target = harness.repository.run_relations_for_run(
            transaction=transaction,
            tenant_id="tenant-demo",
            run_type="outbox",
            run_id="outbox_1",
        )
        hidden_other_tenant = harness.repository.run_relations_for_run(
            transaction=transaction,
            tenant_id="tenant-demo",
            run_type="action",
            run_id="action_run_1",
        )
        visible_other_tenant = harness.repository.run_relations_for_run(
            transaction=transaction,
            tenant_id="tenant-other",
            run_type="action",
            run_id="action_run_1",
        )

    assert inserted is True
    assert duplicate is False
    assert other_tenant is True
    assert [row["id"] for row in by_source] == ["run_relation_1"]
    assert by_source == by_target == hidden_other_tenant
    assert [row["id"] for row in visible_other_tenant] == ["run_relation_other"]


def test_runtime_repository_contract_observability_incident_lifecycle(
    harness: RuntimeRepositoryHarness,
) -> None:
    with harness.transaction() as transaction:
        first = harness.repository.upsert_observability_incident(
            transaction=transaction,
            tenant_id="tenant-demo",
            incident=_observability_incident(),
        )
        replay = harness.repository.upsert_observability_incident(
            transaction=transaction,
            tenant_id="tenant-demo",
            incident=_observability_incident(),
        )
        acknowledged = harness.repository.update_observability_incident_status(
            transaction=transaction,
            tenant_id="tenant-demo",
            incident_id=first["id"],
            status="acknowledged",
            actor_user_id="ops-user",
            updated_at="2026-06-19T00:16:00Z",
            resolution_reason=None,
        )
        resolved = harness.repository.update_observability_incident_status(
            transaction=transaction,
            tenant_id="tenant-demo",
            incident_id=first["id"],
            status="resolved",
            actor_user_id="ops-user",
            updated_at="2026-06-19T00:20:00Z",
            resolution_reason="source recovered",
        )
        reopened = harness.repository.upsert_observability_incident(
            transaction=transaction,
            tenant_id="tenant-demo",
            incident=_observability_incident(observed_at="2026-06-19T00:30:00Z"),
        )

    assert first["status"] == "open"
    assert replay["occurrenceCount"] == 1
    assert acknowledged is not None
    assert acknowledged["acknowledgedBy"] == "ops-user"
    assert resolved is not None
    assert resolved["status"] == "resolved"
    assert resolved["resolutionReason"] == "source recovered"
    assert reopened["status"] == "open"
    assert reopened["occurrenceCount"] == 2
    assert harness.repository.list_observability_incidents(tenant_id="tenant-demo", status="open", limit=10) == [
        reopened
    ]
    assert harness.repository.list_observability_incidents(tenant_id="tenant-other", status=None, limit=10) == []


def test_runtime_repository_contract_requeues_dead_letter_event(
    harness: RuntimeRepositoryHarness,
) -> None:
    harness.add_dead_letter_event(event_id="dlq_1", tenant_id="tenant-demo")
    with harness.transaction() as transaction:
        harness.repository.insert_outbox_event(
            transaction=transaction,
            record=_outbox_record(status="failed", attempts=3, published_at="2026-06-10T00:00:01Z"),
        )
        dead_letter = harness.repository.dead_letter_event_by_id(
            transaction=transaction,
            tenant_id="tenant-demo",
            event_id="dlq_1",
        )
        assert dead_letter is not None
        outbox = harness.repository.update_outbox_event_for_retry(
            transaction=transaction,
            tenant_id="tenant-demo",
            event_id=str(dead_letter["source_event_id"]),
            transition=OUTBOX_RETRY_PENDING,
        )
        stale = harness.repository.update_outbox_event_for_retry(
            transaction=transaction,
            tenant_id="tenant-demo",
            event_id=str(dead_letter["source_event_id"]),
            transition=OUTBOX_RETRY_PENDING,
        )
        deleted = harness.repository.delete_dead_letter_event(
            transaction=transaction,
            tenant_id="tenant-demo",
            event_id="dlq_1",
        )

    runs = harness.repository.list_runs(tenant_id="tenant-demo")

    assert outbox is not None
    assert stale is None
    assert outbox["status"] == "pending"
    assert outbox["attempts"] == 0
    assert outbox["published_at"] is None
    assert deleted is True
    assert runs["deadLetterEvents"] == []


def test_runtime_repository_contract_claims_and_publishes_outbox_event(
    harness: RuntimeRepositoryHarness,
) -> None:
    with harness.transaction() as transaction:
        harness.repository.insert_outbox_event(
            transaction=transaction,
            record=_outbox_record(event_id="outbox_1", created_at="2026-06-10T00:00:01Z"),
        )
        harness.repository.insert_outbox_event(
            transaction=transaction,
            record=_outbox_record(
                event_id="outbox_2",
                created_at="2026-06-10T00:00:00Z",
                idempotency_key="dsv_2",
            ),
        )
        pending = harness.repository.pending_outbox_events(
            transaction=transaction,
            tenant_id="tenant-demo",
            limit=1,
        )
        claimed = harness.repository.mark_outbox_event_publishing(
            transaction=transaction,
            tenant_id="tenant-demo",
            event_id=str(pending[0]["id"]),
            transition=OUTBOX_PUBLISHING,
            claimed_at="2026-06-10T00:00:01Z",
        )
        stale_claim = harness.repository.mark_outbox_event_publishing(
            transaction=transaction,
            tenant_id="tenant-demo",
            event_id=str(pending[0]["id"]),
            transition=OUTBOX_PUBLISHING,
            claimed_at="2026-06-10T00:00:02Z",
        )
        published = harness.repository.mark_outbox_event_published(
            transaction=transaction,
            tenant_id="tenant-demo",
            event_id=str(pending[0]["id"]),
            transition=OUTBOX_PUBLISHED,
            published_at="2026-06-10T00:00:03Z",
            claimed_at="2026-06-10T00:00:01Z",
        )

    assert [row["id"] for row in pending] == ["outbox_2"]
    assert claimed is not None
    assert claimed["status"] == "publishing"
    assert claimed["attempts"] == 1
    assert claimed["claimed_at"] == "2026-06-10T00:00:01Z"
    assert stale_claim is None
    assert published is not None
    assert published["status"] == "published"
    assert published["published_at"] == "2026-06-10T00:00:03Z"
    assert published["claimed_at"] is None


def test_runtime_repository_contract_reclaims_only_stale_publishing_claims(
    harness: RuntimeRepositoryHarness,
) -> None:
    with harness.transaction() as transaction:
        for event_id, key in (("stale_event", "dsv_stale"), ("fresh_event", "dsv_fresh")):
            harness.repository.insert_outbox_event(
                transaction=transaction,
                record=_outbox_record(event_id=event_id, idempotency_key=key),
            )
        harness.repository.mark_outbox_event_publishing(
            transaction=transaction,
            tenant_id="tenant-demo",
            event_id="stale_event",
            transition=OUTBOX_PUBLISHING,
            claimed_at="2026-06-10T00:00:00Z",
        )
        harness.repository.mark_outbox_event_publishing(
            transaction=transaction,
            tenant_id="tenant-demo",
            event_id="fresh_event",
            transition=OUTBOX_PUBLISHING,
            claimed_at="2026-06-10T00:10:00Z",
        )

        reclaimed = harness.repository.reclaim_stale_publishing_events(
            transaction=transaction,
            tenant_id="tenant-demo",
            transition=OUTBOX_PUBLISHING_RECLAIM,
            claimed_before="2026-06-10T00:05:00Z",
        )

        stale = harness.repository.pending_outbox_events(transaction=transaction, tenant_id="tenant-demo", limit=10)

    assert reclaimed == 1
    reclaimed_ids = {row["id"]: row for row in stale}
    assert "stale_event" in reclaimed_ids
    assert reclaimed_ids["stale_event"]["status"] == "pending"
    assert reclaimed_ids["stale_event"]["claimed_at"] is None
    assert "fresh_event" not in reclaimed_ids


def test_runtime_repository_contract_fails_and_dead_letters_outbox_event(
    harness: RuntimeRepositoryHarness,
) -> None:
    with harness.transaction() as transaction:
        harness.repository.insert_outbox_event(
            transaction=transaction,
            record=_outbox_record(event_id="outbox_1"),
        )
        claimed = harness.repository.mark_outbox_event_publishing(
            transaction=transaction,
            tenant_id="tenant-demo",
            event_id="outbox_1",
            transition=OUTBOX_PUBLISHING,
            claimed_at="2026-06-10T00:00:01Z",
        )
        failed = harness.repository.mark_outbox_event_failed(
            transaction=transaction,
            tenant_id="tenant-demo",
            event_id="outbox_1",
            transition=OUTBOX_PUBLISH_FAILED,
            claimed_at="2026-06-10T00:00:01Z",
        )
        harness.repository.insert_dead_letter_event(
            transaction=transaction,
            record=DeadLetterEventRecord(
                event_id="dlq_1",
                tenant_id="tenant-demo",
                source_event_id="outbox_1",
                event_type="dataset.version.committed",
                payload={"versionId": "dsv_1"},
                error={"kind": "UNAVAILABLE", "message": "stream down"},
                failed_at="2026-06-10T00:00:03Z",
                retry_after=None,
            ),
        )

    runs = harness.repository.list_runs(tenant_id="tenant-demo")

    assert claimed is not None
    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["published_at"] is None
    assert [row["id"] for row in runs["deadLetterEvents"]] == ["dlq_1"]
    assert runs["deadLetterEvents"][0]["error"] == {"kind": "UNAVAILABLE", "message": "stream down"}


def test_runtime_repository_contract_tombstones_object_record_idempotently(
    harness: RuntimeRepositoryHarness,
) -> None:
    harness.add_object_record(record_id="Order:O-1", tenant_id="tenant-demo")

    with harness.transaction() as transaction:
        tombstoned = harness.repository.delete_object_record(
            transaction=transaction, tenant_id="tenant-demo", record_id="Order:O-1"
        )
    with harness.transaction() as transaction:
        replay = harness.repository.delete_object_record(
            transaction=transaction, tenant_id="tenant-demo", record_id="Order:O-1"
        )
        rows = harness.repository.rows_for_tenant(
            transaction=transaction, table="object_records", tenant_id="tenant-demo"
        )

    assert tombstoned is True
    # A replay touches no live row and is reported as a no-op (idempotent erasure).
    assert replay is False
    row = next(row for row in rows if row["id"] == "Order:O-1")
    # The tombstone stays so rebuilds do not resurrect the subject, but the
    # subject-bearing payloads are cleared.
    assert row["deleted"] is True
    assert row["is_active"] is False
    assert row["deletion_reason"] == "subject_erasure"
    assert row["properties"] == {}
    assert row["base_properties"] == {}
    assert row["edit_properties"] == {}
    assert "ada@example.com" not in repr(row)


def test_runtime_repository_contract_workflow_run_ledger_status_cas(
    harness: RuntimeRepositoryHarness,
) -> None:
    with harness.transaction() as transaction:
        inserted = harness.repository.insert_workflow_run_or_get_existing(
            transaction=transaction,
            record=_workflow_record_obj(),
        )
        duplicate = harness.repository.insert_workflow_run_or_get_existing(
            transaction=transaction,
            record=_workflow_record_obj(workflow_run_id="flite:workflow:duplicate:1"),
        )
        by_id = harness.repository.workflow_run_by_id(
            transaction=transaction,
            tenant_id="tenant-demo",
            workflow_run_id="flite:workflow:run:1",
        )
        by_key = harness.repository.workflow_run_by_idempotency(
            transaction=transaction,
            tenant_id="tenant-demo",
            workflow_name="ConnectorSyncWorkflow",
            idempotency_key="workflow-key-1",
        )
        starting = harness.repository.update_workflow_run_status(
            transaction=transaction,
            tenant_id="tenant-demo",
            workflow_run_id="flite:workflow:run:1",
            transition=WORKFLOW_RUN_STARTING,
            output={},
            error=None,
            started_at="2026-06-10T00:00:01Z",
            completed_at=None,
        )
        duplicate_start = harness.repository.update_workflow_run_status(
            transaction=transaction,
            tenant_id="tenant-demo",
            workflow_run_id="flite:workflow:run:1",
            transition=WORKFLOW_RUN_STARTING,
            output={},
            error=None,
            started_at="2026-06-10T00:00:02Z",
            completed_at=None,
        )
        succeeded = harness.repository.update_workflow_run_status(
            transaction=transaction,
            tenant_id="tenant-demo",
            workflow_run_id="flite:workflow:run:1",
            transition=WORKFLOW_RUN_SUCCEEDED,
            output={"datasetRef": "raw.orders"},
            error=None,
            started_at=None,
            completed_at="2026-06-10T00:00:03Z",
        )
        late_failure = harness.repository.update_workflow_run_status(
            transaction=transaction,
            tenant_id="tenant-demo",
            workflow_run_id="flite:workflow:run:1",
            transition=WORKFLOW_RUN_FAILED,
            output={},
            error={"kind": "timeout"},
            started_at=None,
            completed_at="2026-06-10T00:00:04Z",
        )
        linked = harness.repository.link_workflow_audit_event(
            transaction=transaction,
            tenant_id="tenant-demo",
            workflow_run_id="flite:workflow:run:1",
            audit_event_id="audit_workflow_1",
        )

    runs = harness.repository.list_runs(tenant_id="tenant-demo")

    assert inserted is None
    assert duplicate is not None
    assert duplicate["id"] == "flite:workflow:run:1"
    assert by_id is not None
    assert by_key is not None
    assert starting is not None
    assert starting["status"] == "starting"
    assert starting["attempts"] == 1
    assert duplicate_start is None
    assert succeeded is not None
    assert succeeded["status"] == "succeeded"
    assert succeeded["output"] == {"datasetRef": "raw.orders"}
    assert late_failure is None
    assert linked is not None
    assert linked["audit_event_id"] == "audit_workflow_1"
    assert [row["id"] for row in runs["workflowRuns"]] == ["flite:workflow:run:1"]


def test_runtime_repository_contract_workflow_run_lease_fences_stale_workers(
    harness: RuntimeRepositoryHarness,
) -> None:
    first_record = _workflow_record_obj(
        workflow_name="ContinuousStreamArchiveWorker",
        status="running",
        idempotency_key="stream-archive:raw.orders:orders:group:0",
        output=_lease_output("worker-1", "lease-1", "2026-06-10T00:01:00Z"),
        attempts=1,
        started_at="2026-06-10T00:00:00Z",
    )
    second_record = _workflow_record_obj(
        workflow_run_id="flite:workflow:run:2",
        workflow_name="ContinuousStreamArchiveWorker",
        status="running",
        idempotency_key="stream-archive:raw.orders:orders:group:0",
        output=_lease_output("worker-2", "lease-2", "2026-06-10T00:02:00Z"),
        attempts=1,
        started_at="2026-06-10T00:01:01Z",
    )

    with harness.transaction() as transaction:
        first = harness.repository.claim_workflow_run_lease(
            transaction=transaction,
            record=first_record,
            lease_owner_id="worker-1",
            now="2026-06-10T00:00:00Z",
        )
        blocked = harness.repository.claim_workflow_run_lease(
            transaction=transaction,
            record=second_record,
            lease_owner_id="worker-2",
            now="2026-06-10T00:00:30Z",
        )
        takeover = harness.repository.claim_workflow_run_lease(
            transaction=transaction,
            record=second_record,
            lease_owner_id="worker-2",
            now="2026-06-10T00:01:01Z",
        )
        stale_release = harness.repository.release_workflow_run_lease(
            transaction=transaction,
            tenant_id="tenant-demo",
            workflow_run_id="flite:workflow:run:1",
            lease_owner_id="worker-1",
            lease_token="lease-1",
            status="succeeded",
            output={"stopReason": "stale"},
            error=None,
            completed_at="2026-06-10T00:01:02Z",
        )
        released = harness.repository.release_workflow_run_lease(
            transaction=transaction,
            tenant_id="tenant-demo",
            workflow_run_id="flite:workflow:run:1",
            lease_owner_id="worker-2",
            lease_token="lease-2",
            status="succeeded",
            output={"stopReason": "empty_polls"},
            error=None,
            completed_at="2026-06-10T00:01:03Z",
        )

    assert first is not None
    assert blocked is None
    assert takeover is not None
    assert takeover["id"] == "flite:workflow:run:1"
    takeover_output = cast(dict[str, object], takeover["output"])
    takeover_lease = cast(dict[str, object], takeover_output["workerLease"])
    assert takeover_lease["ownerId"] == "worker-2"
    assert stale_release is None
    assert released is not None
    assert released["status"] == "succeeded"
    released_output = cast(dict[str, object], released["output"])
    assert released_output["stopReason"] == "empty_polls"


def test_runtime_repository_contract_allowlisted_row_reads(
    harness: RuntimeRepositoryHarness,
) -> None:
    harness.add_transform(transform_id="tf_1", tenant_id="tenant-demo")
    harness.add_materialization(materialization_id="mat_1", tenant_id="tenant-demo")
    harness.add_action_run(run_id="action_run_1", tenant_id="tenant-demo")
    harness.add_action_run(run_id="action_run_other", tenant_id="tenant-other")

    with harness.transaction() as transaction:
        transform = harness.repository.row_by_id(transaction=transaction, table="transforms", row_id="tf_1")
        materialization = harness.repository.row_by_id(
            transaction=transaction,
            table="materializations",
            row_id="mat_1",
        )
        missing = harness.repository.row_by_id(transaction=transaction, table="transforms", row_id="tf_missing")
        action_rows = harness.repository.rows_for_tenant(
            transaction=transaction,
            table="action_runs",
            tenant_id="tenant-demo",
        )

    assert transform is not None
    assert transform["api_name"] == "clean_orders"
    assert materialization is not None
    assert materialization["api_name"] == "action_log"
    assert missing is None
    assert [row["id"] for row in action_rows] == ["action_run_1"]
