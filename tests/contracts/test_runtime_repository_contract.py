from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import pytest
from foundry_lite.application.ports import (
    AuditEventRecord,
    LineageEdgeRecord,
    OutboxEventRecord,
    RuntimeLookupTable,
    RuntimeRepository,
    RuntimeRowsTable,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyRuntimeRepository
from sqlalchemy import create_engine, insert
from sqlalchemy.engine import Engine


class RuntimeRepositoryHarness(Protocol):
    repository: RuntimeRepository

    def transaction(self) -> AbstractContextManager[Any]: ...

    def add_transform(self, *, transform_id: str, tenant_id: str) -> None: ...

    def add_materialization(self, *, materialization_id: str, tenant_id: str) -> None: ...

    def add_action_run(self, *, run_id: str, tenant_id: str) -> None: ...


@dataclass
class FakeRuntimeRepository:
    tables: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))

    def lineage_for_resource(self, *, tenant_id: str, resource_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.tables["lineage_edges"]
            if row["tenant_id"] == tenant_id
            and (row["from_resource_id"] == resource_id or row["to_resource_id"] == resource_id)
        ]

    def list_runs(self, *, tenant_id: str) -> dict[str, list[dict[str, Any]]]:
        return {
            "syncRuns": self.rows_for_tenant(transaction=None, table="sync_runs", tenant_id=tenant_id),
            "transformRuns": self.rows_for_tenant(transaction=None, table="transform_runs", tenant_id=tenant_id),
            "indexRuns": self.rows_for_tenant(transaction=None, table="index_runs", tenant_id=tenant_id),
            "actionRuns": self.rows_for_tenant(transaction=None, table="action_runs", tenant_id=tenant_id),
            "actionWritebacks": self.rows_for_tenant(
                transaction=None,
                table="action_writebacks",
                tenant_id=tenant_id,
            ),
            "materializationRuns": self.rows_for_tenant(
                transaction=None,
                table="materialization_runs",
                tenant_id=tenant_id,
            ),
            "outboxEvents": self.rows_for_tenant(transaction=None, table="outbox_events", tenant_id=tenant_id),
            "auditEvents": self.rows_for_tenant(transaction=None, table="audit_events", tenant_id=tenant_id),
        }

    def row_by_id(self, *, transaction: Any, table: RuntimeLookupTable, row_id: str) -> dict[str, Any] | None:
        del transaction
        for row in self.tables[table]:
            if row["id"] == row_id:
                return dict(row)
        return None

    def rows_for_tenant(self, *, transaction: Any, table: RuntimeRowsTable, tenant_id: str) -> list[dict[str, Any]]:
        del transaction
        return [dict(row) for row in self.tables[table] if row["tenant_id"] == tenant_id]

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

    def insert_lineage_edge(self, *, transaction: Any, record: LineageEdgeRecord) -> None:
        del transaction
        self.tables["lineage_edges"].append(_lineage_row(record))


@dataclass
class FakeRuntimeRepositoryHarness:
    repository: FakeRuntimeRepository

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        yield None

    def add_transform(self, *, transform_id: str, tenant_id: str) -> None:
        self.repository.tables["transforms"].append(_transform_row(transform_id=transform_id, tenant_id=tenant_id))

    def add_materialization(self, *, materialization_id: str, tenant_id: str) -> None:
        self.repository.tables["materializations"].append(
            _materialization_row(materialization_id=materialization_id, tenant_id=tenant_id)
        )

    def add_action_run(self, *, run_id: str, tenant_id: str) -> None:
        self.repository.tables["action_runs"].append(_action_run_row(run_id=run_id, tenant_id=tenant_id))


@dataclass
class SqlAlchemyRuntimeRepositoryHarness:
    repository: SqlAlchemyRuntimeRepository
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

    def add_action_run(self, *, run_id: str, tenant_id: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(db.action_runs).values(**_action_run_row(run_id=run_id, tenant_id=tenant_id)))


def _audit_record(*, event_id: str = "audit_1", tenant_id: str = "tenant-demo") -> AuditEventRecord:
    return AuditEventRecord(
        event_id=event_id,
        tenant_id=tenant_id,
        actor_user_id="user-demo",
        event_type="permission.denied",
        resource_type="dataset",
        resource_id="raw.orders",
        action="dataset:write",
        decision="deny",
        policy_decision={},
        before_ref={},
        after_ref={"reason": "missing role"},
        correlation_id="req-demo",
        request_id="req-demo",
        metadata={},
        created_at="2026-06-10T00:00:00Z",
    )


def _outbox_record(*, event_id: str = "outbox_1", tenant_id: str = "tenant-demo") -> OutboxEventRecord:
    return OutboxEventRecord(
        event_id=event_id,
        tenant_id=tenant_id,
        event_type="dataset.version.committed",
        aggregate_type="dataset_version",
        aggregate_id="dsv_1",
        payload={"versionId": "dsv_1"},
        status="pending",
        attempts=0,
        idempotency_key="dsv_1",
        correlation_id="run_1",
        created_at="2026-06-10T00:00:00Z",
        published_at=None,
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


def _action_run_row(*, run_id: str, tenant_id: str) -> dict[str, Any]:
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
        "status": "SUCCEEDED",
        "idempotency_key": run_id,
        "error": None,
        "created_at": "2026-06-10T00:00:00Z",
        "completed_at": "2026-06-10T00:00:01Z",
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
