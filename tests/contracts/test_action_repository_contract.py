from __future__ import annotations

from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import Barrier
from typing import Any, Protocol

import pytest
from foundry_lite.application.ports.action_repository import (
    ActionRepository,
    ActionRunRecord,
    ActionRunRow,
    ActionWritebackRecord,
    ObjectEditRecord,
    ObjectTargetUpdate,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyActionRepository
from sqlalchemy import create_engine, insert, select
from sqlalchemy.engine import Engine


class ActionHarness(Protocol):
    repository: ActionRepository

    def transaction(self) -> AbstractContextManager[Any]: ...

    def add_object_record(self, **kwargs: Any) -> None: ...

    def action_run_rows(self) -> list[dict[str, Any]]: ...

    def writeback_rows(self) -> list[dict[str, Any]]: ...

    def object_rows(self) -> list[dict[str, Any]]: ...

    def object_edit_rows(self) -> list[dict[str, Any]]: ...


@dataclass
class FakeActionRepository:
    action_runs: list[ActionRunRow] = field(default_factory=list)
    action_writebacks: list[dict[str, Any]] = field(default_factory=list)
    object_records: list[dict[str, Any]] = field(default_factory=list)
    object_edits: list[dict[str, Any]] = field(default_factory=list)

    def action_run_by_idempotency(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        action_type_id: str,
        actor_user_id: str,
        idempotency_key: str,
    ) -> ActionRunRow | None:
        del transaction
        for row in self.action_runs:
            if (
                row["tenant_id"] == tenant_id
                and row["action_type_id"] == action_type_id
                and row["actor_user_id"] == actor_user_id
                and row["idempotency_key"] == idempotency_key
            ):
                return row.copy()
        return None

    def insert_action_run(self, *, transaction: Any, record: ActionRunRecord) -> None:
        del transaction
        self.action_runs.append(_action_run_row(record))

    def insert_action_run_or_get_existing(self, *, transaction: Any, record: ActionRunRecord) -> ActionRunRow | None:
        existing = self.action_run_by_idempotency(
            transaction=transaction,
            tenant_id=record.tenant_id,
            action_type_id=record.action_type_id,
            actor_user_id=record.actor_user_id,
            idempotency_key=record.idempotency_key,
        )
        if existing is not None:
            return existing
        self.action_runs.append(_action_run_row(record))
        return None

    def update_action_run_terminal(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        action_run_id: str,
        status: str,
        error: Mapping[str, object] | None,
        completed_at: str,
    ) -> None:
        del transaction
        for row in self.action_runs:
            if row["tenant_id"] == tenant_id and row["id"] == action_run_id:
                row.update(status=status, error=error, completed_at=completed_at)
                return

    def insert_action_writeback(self, *, transaction: Any, record: ActionWritebackRecord) -> None:
        del transaction
        self.action_writebacks.append(_writeback_row(record))

    def update_object_target(self, *, transaction: Any, record: ObjectTargetUpdate) -> bool:
        del transaction
        for row in self.object_records:
            if (
                row["tenant_id"] == record.tenant_id
                and row["id"] == record.object_record_id
                and row["object_version"] == record.expected_object_version
            ):
                row.update(
                    edit_properties=record.edit_properties,
                    properties=record.properties,
                    object_version=record.next_object_version,
                    updated_at=record.updated_at,
                )
                return True
        return False

    def insert_object_edit(self, *, transaction: Any, record: ObjectEditRecord) -> None:
        del transaction
        self.object_edits.append(_object_edit_row(record))


@dataclass
class FakeActionHarness:
    repository: ActionRepository

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        yield None

    def add_object_record(self, **kwargs: Any) -> None:
        repository = self.repository
        assert isinstance(repository, FakeActionRepository)
        repository.object_records.append(_object_record_row(**kwargs))

    def action_run_rows(self) -> list[dict[str, Any]]:
        repository = self.repository
        assert isinstance(repository, FakeActionRepository)
        return [dict(row) for row in repository.action_runs]

    def writeback_rows(self) -> list[dict[str, Any]]:
        repository = self.repository
        assert isinstance(repository, FakeActionRepository)
        return [dict(row) for row in repository.action_writebacks]

    def object_rows(self) -> list[dict[str, Any]]:
        repository = self.repository
        assert isinstance(repository, FakeActionRepository)
        return [dict(row) for row in repository.object_records]

    def object_edit_rows(self) -> list[dict[str, Any]]:
        repository = self.repository
        assert isinstance(repository, FakeActionRepository)
        return [dict(row) for row in repository.object_edits]


@dataclass
class SqlAlchemyActionHarness:
    repository: ActionRepository
    engine: Engine

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self.engine.begin() as conn:
            yield conn

    def add_object_record(self, **kwargs: Any) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(db.object_records).values(**_object_record_row(**kwargs)))

    def action_run_rows(self) -> list[dict[str, Any]]:
        return self._rows(db.action_runs)

    def writeback_rows(self) -> list[dict[str, Any]]:
        return self._rows(db.action_writebacks)

    def object_rows(self) -> list[dict[str, Any]]:
        return self._rows(db.object_records)

    def object_edit_rows(self) -> list[dict[str, Any]]:
        return self._rows(db.object_edits)

    def _rows(self, table: Any) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            rows = conn.execute(select(table)).mappings().all()
        return [dict(row) for row in rows]


def _action_run_record(
    run_id: str = "arun_1",
    *,
    tenant_id: str = "tenant-demo",
    action_type_id: str = "atype_approve",
    actor_user_id: str = "actor-demo",
    idempotency_key: str = "idem-1",
    request_fingerprint: str = "fingerprint-1",
) -> ActionRunRecord:
    return ActionRunRecord(
        action_run_id=run_id,
        tenant_id=tenant_id,
        action_type_id=action_type_id,
        action_type_api_name="approveOrder",
        actor_user_id=actor_user_id,
        target_object_type_id="ot_order",
        target_object_type_api_name="Order",
        target_object_id="O-1",
        expected_object_version=1,
        parameters={"status": "APPROVED"},
        status="received",
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        error=None,
        created_at="2026-06-10T00:00:00Z",
        completed_at=None,
    )


def _action_run_row(record: ActionRunRecord) -> ActionRunRow:
    return {
        "id": record.action_run_id,
        "tenant_id": record.tenant_id,
        "action_type_id": record.action_type_id,
        "action_type_api_name": record.action_type_api_name,
        "actor_user_id": record.actor_user_id,
        "target_object_type_id": record.target_object_type_id,
        "target_object_type_api_name": record.target_object_type_api_name,
        "target_object_id": record.target_object_id,
        "expected_object_version": record.expected_object_version,
        "parameters": record.parameters,
        "status": record.status,
        "idempotency_key": record.idempotency_key,
        "request_fingerprint": record.request_fingerprint,
        "error": record.error,
        "created_at": record.created_at,
        "completed_at": record.completed_at,
    }


def _writeback_record(writeback_id: str = "wb_1", *, status: str = "succeeded") -> ActionWritebackRecord:
    return ActionWritebackRecord(
        writeback_id=writeback_id,
        tenant_id="tenant-demo",
        action_run_id="arun_1",
        mode="before_commit",
        connector_id="mock_erp",
        request={"connector": "mock_erp", "simulated": True},
        response={"status_code": 200, "simulated": True},
        status=status,
        idempotency_key="idem-1",
        attempts=1,
        created_at="2026-06-10T00:00:01Z",
        completed_at="2026-06-10T00:00:02Z",
    )


def _writeback_row(record: ActionWritebackRecord) -> dict[str, Any]:
    return {
        "id": record.writeback_id,
        "tenant_id": record.tenant_id,
        "action_run_id": record.action_run_id,
        "mode": record.mode,
        "connector_id": record.connector_id,
        "request": record.request,
        "response": record.response,
        "status": record.status,
        "idempotency_key": record.idempotency_key,
        "attempts": record.attempts,
        "created_at": record.created_at,
        "completed_at": record.completed_at,
    }


def _object_record_row(
    *,
    record_id: str = "obj_order_1",
    tenant_id: str = "tenant-demo",
    object_version: int = 3,
) -> dict[str, Any]:
    return {
        "id": record_id,
        "tenant_id": tenant_id,
        "object_type_id": "ot_order",
        "object_type_api_name": "Order",
        "object_id": "O-1",
        "index_version": "active",
        "is_active": True,
        "properties": {"status": "PENDING"},
        "base_properties": {"status": "PENDING"},
        "edit_properties": {},
        "property_versions": {"status": 1},
        "source_dataset_version_id": "dsv_orders_1",
        "source_hash": "hash-demo",
        "object_version": object_version,
        "deleted": False,
        "deletion_reason": None,
        "created_at": "2026-06-10T00:00:00Z",
        "updated_at": "2026-06-10T00:00:00Z",
    }


def _object_target_update(*, expected_object_version: int = 3) -> ObjectTargetUpdate:
    return ObjectTargetUpdate(
        object_record_id="obj_order_1",
        tenant_id="tenant-demo",
        expected_object_version=expected_object_version,
        edit_properties={"status": "APPROVED"},
        properties={"status": "APPROVED"},
        next_object_version=expected_object_version + 1,
        updated_at="2026-06-10T00:00:03Z",
    )


def _object_edit_record(edit_id: str = "edit_1") -> ObjectEditRecord:
    return ObjectEditRecord(
        edit_id=edit_id,
        tenant_id="tenant-demo",
        action_run_id="arun_1",
        object_type_id="ot_order",
        object_type_api_name="Order",
        object_id="O-1",
        edit_type="set_property",
        patch={"status": "APPROVED"},
        previous_values={"status": "PENDING"},
        actor_user_id="actor-demo",
        idempotency_key="idem-1",
        created_at="2026-06-10T00:00:04Z",
    )


def _object_edit_row(record: ObjectEditRecord) -> dict[str, Any]:
    return {
        "id": record.edit_id,
        "tenant_id": record.tenant_id,
        "action_run_id": record.action_run_id,
        "object_type_id": record.object_type_id,
        "object_type_api_name": record.object_type_api_name,
        "object_id": record.object_id,
        "edit_type": record.edit_type,
        "patch": record.patch,
        "previous_values": record.previous_values,
        "actor_user_id": record.actor_user_id,
        "idempotency_key": record.idempotency_key,
        "created_at": record.created_at,
    }


@pytest.fixture(params=["sqlalchemy", "fake", "postgres"])
def harness(request: pytest.FixtureRequest, tmp_path: Path) -> ActionHarness:
    if request.param == "fake":
        return FakeActionHarness(FakeActionRepository())
    if request.param == "sqlalchemy":
        engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}", future=True)
        db.create_database(engine)
        return SqlAlchemyActionHarness(SqlAlchemyActionRepository(engine), engine)
    postgres_fixture = request.getfixturevalue("postgres_fixture")
    return SqlAlchemyActionHarness(
        SqlAlchemyActionRepository(postgres_fixture.engine),
        postgres_fixture.engine,
    )


def test_action_repository_contract_inserts_and_replays_idempotent_runs(harness: ActionHarness) -> None:
    with harness.transaction() as transaction:
        harness.repository.insert_action_run(transaction=transaction, record=_action_run_record())
        found = harness.repository.action_run_by_idempotency(
            transaction=transaction,
            tenant_id="tenant-demo",
            action_type_id="atype_approve",
            actor_user_id="actor-demo",
            idempotency_key="idem-1",
        )
        missing_actor = harness.repository.action_run_by_idempotency(
            transaction=transaction,
            tenant_id="tenant-demo",
            action_type_id="atype_approve",
            actor_user_id="other-actor",
            idempotency_key="idem-1",
        )

    assert found is not None
    assert found["id"] == "arun_1"
    assert found["parameters"] == {"status": "APPROVED"}
    assert found["request_fingerprint"] == "fingerprint-1"
    assert missing_actor is None


def test_action_repository_contract_insert_or_get_existing_replays_duplicate(harness: ActionHarness) -> None:
    with harness.transaction() as transaction:
        first = harness.repository.insert_action_run_or_get_existing(
            transaction=transaction,
            record=_action_run_record(run_id="arun_winner"),
        )
        replay = harness.repository.insert_action_run_or_get_existing(
            transaction=transaction,
            record=_action_run_record(run_id="arun_loser"),
        )

    rows = harness.action_run_rows()
    assert first is None
    assert replay is not None
    assert replay["id"] == "arun_winner"
    assert [row["id"] for row in rows] == ["arun_winner"]


def test_action_same_idempotency_key_concurrent_requests_replay_same_action_run(tmp_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'metadata.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    db.create_database(engine)
    repository = SqlAlchemyActionRepository(engine)
    start = Barrier(2)

    def submit(run_id: str) -> str:
        start.wait()
        with engine.begin() as transaction:
            existing = repository.insert_action_run_or_get_existing(
                transaction=transaction,
                record=_action_run_record(run_id=run_id),
            )
        return existing["id"] if existing is not None else run_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, ["arun_first", "arun_second"]))

    with engine.begin() as transaction:
        rows = transaction.execute(select(db.action_runs)).mappings().all()
    assert len(rows) == 1
    assert results == [rows[0]["id"], rows[0]["id"]]


def test_sqlalchemy_action_run_insert_or_get_existing_rolls_back_with_outer_transaction(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}", future=True)
    db.create_database(engine)
    repository = SqlAlchemyActionRepository(engine)

    with pytest.raises(RuntimeError):
        with engine.begin() as transaction:
            repository.insert_action_run_or_get_existing(
                transaction=transaction,
                record=_action_run_record(run_id="arun_rollback"),
            )
            raise RuntimeError("rollback after idempotency winner insert")

    with engine.begin() as transaction:
        rows = transaction.execute(select(db.action_runs)).mappings().all()
    assert rows == []


def test_action_repository_contract_updates_terminal_state_and_writebacks(harness: ActionHarness) -> None:
    with harness.transaction() as transaction:
        harness.repository.insert_action_run(transaction=transaction, record=_action_run_record())
        harness.repository.update_action_run_terminal(
            transaction=transaction,
            tenant_id="tenant-demo",
            action_run_id="arun_1",
            status="failed",
            error={"type": "ExternalSystemError"},
            completed_at="2026-06-10T00:00:05Z",
        )
        harness.repository.insert_action_writeback(transaction=transaction, record=_writeback_record(status="failed"))

    action_runs = harness.action_run_rows()
    writebacks = harness.writeback_rows()
    assert action_runs[0]["status"] == "failed"
    assert action_runs[0]["error"] == {"type": "ExternalSystemError"}
    assert writebacks[0]["status"] == "failed"
    assert writebacks[0]["connector_id"] == "mock_erp"


def test_action_repository_contract_updates_object_target_and_records_edit(harness: ActionHarness) -> None:
    harness.add_object_record()

    with harness.transaction() as transaction:
        updated = harness.repository.update_object_target(transaction=transaction, record=_object_target_update())
        stale_update = harness.repository.update_object_target(
            transaction=transaction,
            record=_object_target_update(expected_object_version=3),
        )
        harness.repository.insert_object_edit(transaction=transaction, record=_object_edit_record())

    object_rows = harness.object_rows()
    edit_rows = harness.object_edit_rows()
    assert updated is True
    assert stale_update is False
    assert object_rows[0]["object_version"] == 4
    assert object_rows[0]["properties"] == {"status": "APPROVED"}
    assert edit_rows[0]["patch"] == {"status": "APPROVED"}
