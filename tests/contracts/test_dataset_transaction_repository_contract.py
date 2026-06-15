from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Barrier, Event
from typing import Any, Protocol

import pytest
from foundry_lite.application.ports import (
    DatasetFileRecord,
    DatasetRunKind,
    DatasetTransactionRecord,
    DatasetTransactionRepository,
    DatasetVersionConflictError,
    DatasetVersionRecord,
    SyncRunRecord,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import (
    SqlAlchemyDatasetTransactionRepository,
    SqlAlchemyDatasetVersionRepository,
)
from sqlalchemy import create_engine, insert, select
from sqlalchemy.engine import Engine


class TransactionHarness(Protocol):
    repository: DatasetTransactionRepository

    def call_in_transaction(self, fn: Any) -> Any: ...

    def add_run(self, *, run_kind: DatasetRunKind, run_id: str, transaction_id: str) -> None: ...

    def add_dataset(self) -> None: ...

    def run_status(self, *, run_kind: DatasetRunKind, run_id: str) -> dict[str, Any] | None: ...

    def versions(self) -> list[dict[str, Any]]: ...

    def files(self) -> list[dict[str, Any]]: ...

    def sync_run_row(self, *, sync_run_id: str) -> dict[str, Any] | None: ...


@dataclass
class FakeDatasetTransactionRepository:
    transactions: dict[str, dict[str, Any]] = field(default_factory=dict)
    versions_store: list[dict[str, Any]] = field(default_factory=list)
    files_store: list[dict[str, Any]] = field(default_factory=list)
    runs: dict[tuple[DatasetRunKind, str], dict[str, Any]] = field(default_factory=dict)
    sync_runs_store: dict[str, dict[str, Any]] = field(default_factory=dict)

    def create_open_transaction(self, *, transaction: Any, record: DatasetTransactionRecord) -> None:
        del transaction
        self.transactions[record.transaction_id] = {
            "id": record.transaction_id,
            "tenant_id": record.tenant_id,
            "dataset_id": record.dataset_id,
            "branch": record.branch,
            "tx_type": record.tx_type,
            "status": record.status,
            "base_version_id": record.base_version_id,
            "committed_version_id": record.committed_version_id,
            "schema_version": record.schema_version,
            "created_by": record.created_by,
            "created_at": record.created_at,
            "committed_at": record.committed_at,
            "metadata": record.metadata,
        }

    def transaction_by_id(self, *, transaction: Any, transaction_id: str) -> dict[str, Any] | None:
        del transaction
        row = self.transactions.get(transaction_id)
        return dict(row) if row else None

    def list_open_transactions(self, *, transaction: Any, tenant_id: str, created_before: str) -> list[dict[str, Any]]:
        del transaction
        rows = [
            dict(row)
            for row in self.transactions.values()
            if row["tenant_id"] == tenant_id and row["status"] == "OPEN" and row["created_at"] < created_before
        ]
        return sorted(rows, key=lambda row: row["created_at"])

    def abort_transaction(
        self, *, transaction: Any, tenant_id: str, transaction_id: str, metadata: dict[str, Any]
    ) -> None:
        del transaction
        if self.transactions[transaction_id]["tenant_id"] == tenant_id:
            self.transactions[transaction_id].update(status="ABORTED", metadata=metadata)

    def lock_dataset_for_version_allocation(self, *, transaction: Any, tenant_id: str, dataset_id: str) -> None:
        del transaction, tenant_id, dataset_id

    def insert_version(self, *, transaction: Any, record: DatasetVersionRecord) -> None:
        del transaction
        if any(
            row["dataset_id"] == record.dataset_id
            and row["branch"] == record.branch
            and row["version_number"] == record.version_number
            for row in self.versions_store
        ):
            raise DatasetVersionConflictError("dataset version already exists")
        self.versions_store.append(record.__dict__.copy())

    def insert_file(self, *, transaction: Any, record: DatasetFileRecord) -> None:
        del transaction
        self.files_store.append(record.__dict__.copy())

    def commit_transaction(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        transaction_id: str,
        committed_version_id: str,
        schema_version: int,
        committed_at: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        del transaction
        if self.transactions[transaction_id]["tenant_id"] != tenant_id:
            return
        self.transactions[transaction_id].update(
            status="COMMITTED",
            committed_version_id=committed_version_id,
            schema_version=schema_version,
            committed_at=committed_at,
            metadata=dict(metadata or {}),
        )

    def latest_committed_transaction(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        dataset_id: str,
    ) -> dict[str, Any] | None:
        del transaction
        rows = [
            row
            for row in self.transactions.values()
            if row["tenant_id"] == tenant_id and row["dataset_id"] == dataset_id and row["status"] == "COMMITTED"
        ]
        latest = sorted(rows, key=lambda row: (row["committed_at"] or "", row["created_at"]))[-1:]
        return dict(latest[0]) if latest else None

    def committed_webhook_transaction_by_event(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        dataset_id: str,
        connector_name: str,
        resource_name: str,
        event_id: str,
    ) -> dict[str, Any] | None:
        del transaction
        rows = [
            row
            for row in self.transactions.values()
            if row["tenant_id"] == tenant_id
            and row["dataset_id"] == dataset_id
            and row["status"] == "COMMITTED"
            and _webhook_event_matches(row["metadata"], connector_name, resource_name, event_id)
        ]
        latest = sorted(rows, key=lambda row: (row["committed_at"] or "", row["created_at"]))[-1:]
        return dict(latest[0]) if latest else None

    def abort_open_transaction_and_fail_run(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        transaction_id: str,
        run_id: str,
        run_kind: DatasetRunKind,
        error: dict[str, Any],
        completed_at: str,
    ) -> bool:
        del transaction
        tx = self.transactions[transaction_id]
        if tx["tenant_id"] != tenant_id:
            return False
        aborted = tx["status"] == "OPEN"
        if tx["status"] == "OPEN":
            tx.update(status="ABORTED", metadata={"error": error})
        self.runs[(run_kind, run_id)].update(status="FAILED", error=error, completed_at=completed_at)
        return aborted

    def insert_sync_run(self, *, transaction: Any, record: SyncRunRecord) -> None:
        del transaction
        self.sync_runs_store[record.sync_run_id] = {
            "id": record.sync_run_id,
            "tenant_id": record.tenant_id,
            "sync_name": record.sync_name,
            "source_type": record.source_type,
            "output_dataset_id": record.output_dataset_id,
            "transaction_id": record.transaction_id,
            "committed_version_id": record.committed_version_id,
            "status": record.status,
            "error": record.error,
            "created_at": record.created_at,
            "completed_at": record.completed_at,
        }

    def update_sync_run_terminal(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        sync_run_id: str,
        status: str,
        committed_version_id: str | None,
        completed_at: str,
    ) -> None:
        del transaction
        if self.sync_runs_store[sync_run_id]["tenant_id"] != tenant_id:
            return
        self.sync_runs_store[sync_run_id].update(
            status=status,
            committed_version_id=committed_version_id,
            completed_at=completed_at,
        )


@dataclass
class FakeTransactionHarness:
    repository: FakeDatasetTransactionRepository

    def call_in_transaction(self, fn: Any) -> Any:
        return fn(None)

    def add_run(self, *, run_kind: DatasetRunKind, run_id: str, transaction_id: str) -> None:
        self.repository.runs[(run_kind, run_id)] = {
            "id": run_id,
            "transaction_id": transaction_id,
            "status": "RUNNING",
            "error": None,
            "completed_at": None,
        }

    def add_dataset(self) -> None:
        return None

    def run_status(self, *, run_kind: DatasetRunKind, run_id: str) -> dict[str, Any] | None:
        row = self.repository.runs.get((run_kind, run_id))
        return dict(row) if row else None

    def versions(self) -> list[dict[str, Any]]:
        return list(self.repository.versions_store)

    def files(self) -> list[dict[str, Any]]:
        return list(self.repository.files_store)

    def sync_run_row(self, *, sync_run_id: str) -> dict[str, Any] | None:
        row = self.repository.sync_runs_store.get(sync_run_id)
        return dict(row) if row else None


@dataclass
class SqlAlchemyTransactionHarness:
    repository: SqlAlchemyDatasetTransactionRepository
    engine: Engine

    def call_in_transaction(self, fn: Any) -> Any:
        with self.engine.begin() as transaction:
            return fn(transaction)

    def add_run(self, *, run_kind: DatasetRunKind, run_id: str, transaction_id: str) -> None:
        table = _run_table(run_kind)
        values = _run_values(run_kind, run_id, transaction_id)
        with self.engine.begin() as transaction:
            transaction.execute(insert(table).values(**values))

    def add_dataset(self) -> None:
        with self.engine.begin() as transaction:
            transaction.execute(insert(db.datasets).values(**_dataset_values()))

    def run_status(self, *, run_kind: DatasetRunKind, run_id: str) -> dict[str, Any] | None:
        table = _run_table(run_kind)
        with self.engine.begin() as transaction:
            row = transaction.execute(select(table).where(table.c.id == run_id)).mappings().first()
            return dict(row) if row else None

    def versions(self) -> list[dict[str, Any]]:
        with self.engine.begin() as transaction:
            return [dict(row) for row in transaction.execute(select(db.dataset_versions)).mappings().all()]

    def files(self) -> list[dict[str, Any]]:
        with self.engine.begin() as transaction:
            return [dict(row) for row in transaction.execute(select(db.dataset_files)).mappings().all()]

    def sync_run_row(self, *, sync_run_id: str) -> dict[str, Any] | None:
        with self.engine.begin() as transaction:
            row = transaction.execute(select(db.sync_runs).where(db.sync_runs.c.id == sync_run_id)).mappings().first()
            return dict(row) if row else None


def _transaction_record(
    transaction_id: str, *, status: str = "OPEN", created_at: str = "2026-06-10T00:00:00Z"
) -> DatasetTransactionRecord:
    return DatasetTransactionRecord(
        transaction_id=transaction_id,
        tenant_id="tenant-demo",
        dataset_id="ds_orders",
        branch="main",
        tx_type="SNAPSHOT",
        status=status,
        base_version_id=None,
        committed_version_id=None,
        schema_version=None,
        created_by="user-demo",
        created_at=created_at,
        committed_at=None,
        metadata={},
    )


def _version_record(transaction_id: str) -> DatasetVersionRecord:
    return DatasetVersionRecord(
        version_id="dsv_orders_1",
        tenant_id="tenant-demo",
        dataset_id="ds_orders",
        branch="main",
        version_number=1,
        transaction_id=transaction_id,
        schema_version=1,
        manifest_uri="memory://manifest.json",
        row_count=3,
        byte_size=100,
        status="active",
        superseded_by_version_id=None,
        created_at="2026-06-10T00:01:00Z",
    )


def _file_record() -> DatasetFileRecord:
    return DatasetFileRecord(
        file_id="dsf_orders_1",
        tenant_id="tenant-demo",
        dataset_version_id="dsv_orders_1",
        uri="memory://part-00000.parquet",
        file_format="parquet",
        row_count=3,
        byte_size=100,
        content_hash="hash-demo",
        partition_values={},
    )


def _version_record_for(transaction_id: str, version_number: int) -> DatasetVersionRecord:
    return DatasetVersionRecord(
        version_id=f"dsv_orders_{version_number}",
        tenant_id="tenant-demo",
        dataset_id="ds_orders",
        branch="main",
        version_number=version_number,
        transaction_id=transaction_id,
        schema_version=1,
        manifest_uri=f"memory://manifest-{version_number}.json",
        row_count=3,
        byte_size=100,
        status="active",
        superseded_by_version_id=None,
        created_at=f"2026-06-10T00:0{version_number}:00Z",
    )


def _commit_locked_version(
    transaction_repository: SqlAlchemyDatasetTransactionRepository,
    version_repository: SqlAlchemyDatasetVersionRepository,
    transaction: Any,
    *,
    transaction_id: str,
    start: Barrier,
    signal_locked: Event,
    wait_for_attempt: Event,
) -> int:
    transaction_repository.create_open_transaction(
        transaction=transaction,
        record=_transaction_record(transaction_id),
    )
    start.wait()
    transaction_repository.lock_dataset_for_version_allocation(
        transaction=transaction,
        tenant_id="tenant-demo",
        dataset_id="ds_orders",
    )
    signal_locked.set()
    assert wait_for_attempt.wait(timeout=5), "second commit did not attempt the dataset lock"
    version_number = version_repository.next_version_number(transaction=transaction, dataset_id="ds_orders")
    transaction_repository.insert_version(
        transaction=transaction,
        record=_version_record_for(transaction_id, version_number),
    )
    transaction_repository.commit_transaction(
        transaction=transaction,
        tenant_id="tenant-demo",
        transaction_id=transaction_id,
        committed_version_id=f"dsv_orders_{version_number}",
        schema_version=1,
        committed_at="2026-06-10T00:02:00Z",
    )
    return version_number


def _dataset_values() -> dict[str, Any]:
    return {
        "id": "ds_orders",
        "tenant_id": "tenant-demo",
        "namespace": "raw",
        "name": "orders",
        "description": None,
        "storage_kind": "local",
        "storage_uri": None,
        "owner_team": "data",
        "classification": "internal",
        "status": "active",
        "primary_key": ["order_id"],
        "created_at": "2026-06-10T00:00:00Z",
        "updated_at": "2026-06-10T00:00:00Z",
    }


def _run_table(run_kind: DatasetRunKind) -> Any:
    if run_kind == "sync":
        return db.sync_runs
    if run_kind == "transform":
        return db.transform_runs
    return db.materialization_runs


def _run_values(run_kind: DatasetRunKind, run_id: str, transaction_id: str) -> dict[str, Any]:
    base = {
        "id": run_id,
        "tenant_id": "tenant-demo",
        "status": "RUNNING",
        "error": None,
        "created_at": "2026-06-10T00:00:00Z",
        "completed_at": None,
    }
    if run_kind == "sync":
        return {
            **base,
            "sync_name": "upload:raw.orders",
            "source_type": "file.csv",
            "output_dataset_id": "ds_orders",
            "transaction_id": transaction_id,
            "committed_version_id": None,
        }
    if run_kind == "transform":
        return {
            **base,
            "transform_id": "tr_orders",
            "input_versions": {},
            "output_version_id": None,
            "transaction_id": transaction_id,
        }
    return {
        **base,
        "materialization_id": "mat_orders",
        "api_name": "order_current",
        "source_cursor": None,
        "object_store_watermark": None,
        "consistency_level": "watermark",
        "target_dataset_version_id": None,
        "row_count": None,
    }


@pytest.fixture(params=["sqlalchemy", "fake", "postgres"])
def harness(request: pytest.FixtureRequest, tmp_path) -> TransactionHarness:
    if request.param == "fake":
        return FakeTransactionHarness(FakeDatasetTransactionRepository())
    if request.param == "sqlalchemy":
        engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}", future=True)
        db.create_database(engine)
        return SqlAlchemyTransactionHarness(SqlAlchemyDatasetTransactionRepository(engine), engine)
    postgres_fixture = request.getfixturevalue("postgres_fixture")
    return SqlAlchemyTransactionHarness(
        SqlAlchemyDatasetTransactionRepository(postgres_fixture.engine),
        postgres_fixture.engine,
    )


def test_dataset_transaction_repository_contract_commit_flow(harness: TransactionHarness) -> None:
    repository = harness.repository

    def commit_flow(transaction: Any) -> dict[str, Any] | None:
        repository.create_open_transaction(transaction=transaction, record=_transaction_record("dstx_commit"))
        repository.insert_version(transaction=transaction, record=_version_record("dstx_commit"))
        repository.insert_file(transaction=transaction, record=_file_record())
        repository.commit_transaction(
            transaction=transaction,
            tenant_id="tenant-demo",
            transaction_id="dstx_commit",
            committed_version_id="dsv_orders_1",
            schema_version=1,
            committed_at="2026-06-10T00:02:00Z",
        )
        return repository.transaction_by_id(transaction=transaction, transaction_id="dstx_commit")

    committed = harness.call_in_transaction(commit_flow)

    assert committed is not None
    assert committed["status"] == "COMMITTED"
    assert committed["committed_version_id"] == "dsv_orders_1"
    assert harness.versions()[0]["version_id" if "version_id" in harness.versions()[0] else "id"] == "dsv_orders_1"
    assert harness.files()[0]["uri"] == "memory://part-00000.parquet"


def test_dataset_transaction_repository_contract_lists_stale_open_transactions(harness: TransactionHarness) -> None:
    repository = harness.repository

    def create_and_list(transaction: Any) -> list[dict[str, Any]]:
        repository.create_open_transaction(
            transaction=transaction, record=_transaction_record("dstx_old", created_at="2026-06-10T00:00:00Z")
        )
        repository.create_open_transaction(
            transaction=transaction, record=_transaction_record("dstx_new", created_at="2026-06-15T00:00:00Z")
        )
        repository.create_open_transaction(
            transaction=transaction,
            record=_transaction_record("dstx_done", status="COMMITTED", created_at="2026-06-09T00:00:00Z"),
        )
        return list(
            repository.list_open_transactions(
                transaction=transaction, tenant_id="tenant-demo", created_before="2026-06-12T00:00:00Z"
            )
        )

    stale = harness.call_in_transaction(create_and_list)

    # Only OPEN transactions created before the cutoff are returned: the recent
    # OPEN transaction and the already-committed transaction are excluded.
    assert [row["id"] for row in stale] == ["dstx_old"]


def test_dataset_transaction_repository_contract_latest_committed_metadata(
    harness: TransactionHarness,
) -> None:
    repository = harness.repository

    def commit_two_transactions(transaction: Any) -> dict[str, Any] | None:
        repository.create_open_transaction(transaction=transaction, record=_transaction_record("dstx_first"))
        repository.create_open_transaction(transaction=transaction, record=_transaction_record("dstx_second"))
        repository.commit_transaction(
            transaction=transaction,
            tenant_id="tenant-demo",
            transaction_id="dstx_first",
            committed_version_id="dsv_orders_1",
            schema_version=1,
            committed_at="2026-06-10T00:01:00Z",
            metadata={"streamCursor": {"offset": 1}},
        )
        repository.commit_transaction(
            transaction=transaction,
            tenant_id="tenant-demo",
            transaction_id="dstx_second",
            committed_version_id="dsv_orders_2",
            schema_version=1,
            committed_at="2026-06-10T00:02:00Z",
            metadata={"streamCursor": {"offset": 2}},
        )
        return repository.latest_committed_transaction(
            transaction=transaction,
            tenant_id="tenant-demo",
            dataset_id="ds_orders",
        )

    latest = harness.call_in_transaction(commit_two_transactions)

    assert latest is not None
    assert latest["id"] == "dstx_second"
    assert latest["metadata"] == {"streamCursor": {"offset": 2}}


def test_dataset_transaction_repository_contract_finds_committed_webhook_event(
    harness: TransactionHarness,
) -> None:
    repository = harness.repository

    def commit_webhook_transaction(transaction: Any) -> dict[str, Any] | None:
        repository.create_open_transaction(transaction=transaction, record=_transaction_record("dstx_webhook"))
        repository.commit_transaction(
            transaction=transaction,
            tenant_id="tenant-demo",
            transaction_id="dstx_webhook",
            committed_version_id="dsv_webhook_1",
            schema_version=1,
            committed_at="2026-06-10T00:02:00Z",
            metadata={
                "webhookEvent": {
                    "connectorName": "mock_saas",
                    "resourceName": "orders",
                    "eventId": "evt-1",
                    "payloadHash": "hash-1",
                }
            },
        )
        return repository.committed_webhook_transaction_by_event(
            transaction=transaction,
            tenant_id="tenant-demo",
            dataset_id="ds_orders",
            connector_name="mock_saas",
            resource_name="orders",
            event_id="evt-1",
        )

    found = harness.call_in_transaction(commit_webhook_transaction)

    assert found is not None
    assert found["id"] == "dstx_webhook"
    assert found["committed_version_id"] == "dsv_webhook_1"


def test_dataset_transaction_repository_contract_locks_dataset_for_version_allocation(
    harness: TransactionHarness,
) -> None:
    harness.add_dataset()

    harness.call_in_transaction(
        lambda transaction: harness.repository.lock_dataset_for_version_allocation(
            transaction=transaction,
            tenant_id="tenant-demo",
            dataset_id="ds_orders",
        )
    )


def test_dataset_transaction_repository_contract_rejects_duplicate_dataset_version(
    harness: TransactionHarness,
) -> None:
    repository = harness.repository

    def insert_duplicate(transaction: Any) -> None:
        repository.insert_version(transaction=transaction, record=_version_record("dstx_commit_1"))
        with pytest.raises(DatasetVersionConflictError):
            repository.insert_version(transaction=transaction, record=_version_record("dstx_commit_2"))

    harness.call_in_transaction(insert_duplicate)

    assert len(harness.versions()) == 1


def test_concurrent_dataset_commits_allocate_strictly_increasing_versions(postgres_fixture: Any) -> None:
    engine = postgres_fixture.engine
    transaction_repository = SqlAlchemyDatasetTransactionRepository(engine)
    version_repository = SqlAlchemyDatasetVersionRepository(engine)
    start = Barrier(2, timeout=5)
    first_locked = Event()
    second_attempting_lock = Event()

    with engine.begin() as transaction:
        transaction.execute(insert(db.datasets).values(**_dataset_values()))

    def commit_first() -> int:
        with engine.begin() as transaction:
            return _commit_locked_version(
                transaction_repository,
                version_repository,
                transaction,
                transaction_id="dstx_first",
                start=start,
                signal_locked=first_locked,
                wait_for_attempt=second_attempting_lock,
            )

    def commit_second() -> int:
        with engine.begin() as transaction:
            transaction_repository.create_open_transaction(
                transaction=transaction,
                record=_transaction_record("dstx_second"),
            )
            start.wait()
            assert first_locked.wait(timeout=5), "first commit did not acquire the dataset lock"
            second_attempting_lock.set()
            transaction_repository.lock_dataset_for_version_allocation(
                transaction=transaction,
                tenant_id="tenant-demo",
                dataset_id="ds_orders",
            )
            version_number = version_repository.next_version_number(transaction=transaction, dataset_id="ds_orders")
            transaction_repository.insert_version(
                transaction=transaction,
                record=_version_record_for("dstx_second", version_number),
            )
            transaction_repository.commit_transaction(
                transaction=transaction,
                tenant_id="tenant-demo",
                transaction_id="dstx_second",
                committed_version_id=f"dsv_orders_{version_number}",
                schema_version=1,
                committed_at="2026-06-10T00:03:00Z",
            )
            return version_number

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(commit_first)
        second_future = executor.submit(commit_second)
        allocated_versions = [first_future.result(), second_future.result()]

    with engine.begin() as transaction:
        rows = transaction.execute(
            select(db.dataset_versions.c.version_number, db.dataset_versions.c.transaction_id).order_by(
                db.dataset_versions.c.version_number
            )
        ).all()

    assert allocated_versions == [1, 2]
    assert rows == [(1, "dstx_first"), (2, "dstx_second")]


def test_dataset_transaction_repository_contract_abort_flow(harness: TransactionHarness) -> None:
    repository = harness.repository

    def abort_flow(transaction: Any) -> dict[str, Any] | None:
        repository.create_open_transaction(transaction=transaction, record=_transaction_record("dstx_abort"))
        repository.abort_transaction(
            transaction=transaction,
            tenant_id="tenant-demo",
            transaction_id="dstx_abort",
            metadata={"validationFailures": [{"check": "row_count_min"}]},
        )
        return repository.transaction_by_id(transaction=transaction, transaction_id="dstx_abort")

    aborted = harness.call_in_transaction(abort_flow)

    assert aborted is not None
    assert aborted["status"] == "ABORTED"
    assert aborted["metadata"] == {"validationFailures": [{"check": "row_count_min"}]}


@pytest.mark.parametrize("run_kind", ["sync", "transform", "materialization"])
def test_dataset_transaction_repository_contract_abort_open_transaction_and_fail_run(
    harness: TransactionHarness,
    run_kind: DatasetRunKind,
) -> None:
    repository = harness.repository

    harness.call_in_transaction(
        lambda transaction: repository.create_open_transaction(
            transaction=transaction,
            record=_transaction_record("dstx_fail"),
        )
    )
    harness.add_run(run_kind=run_kind, run_id="run_1", transaction_id="dstx_fail")

    aborted = harness.call_in_transaction(
        lambda transaction: repository.abort_open_transaction_and_fail_run(
            transaction=transaction,
            tenant_id="tenant-demo",
            transaction_id="dstx_fail",
            run_id="run_1",
            run_kind=run_kind,
            error={"code": "VALIDATION_FAILED"},
            completed_at="2026-06-10T00:03:00Z",
        )
    )
    failed_run = harness.run_status(run_kind=run_kind, run_id="run_1")
    failed_tx = harness.call_in_transaction(
        lambda transaction: repository.transaction_by_id(transaction=transaction, transaction_id="dstx_fail")
    )

    assert aborted is True
    assert failed_run is not None
    assert failed_run["status"] == "FAILED"
    assert failed_tx is not None
    assert failed_tx["status"] == "ABORTED"
    assert failed_tx["metadata"] == {"error": {"code": "VALIDATION_FAILED"}}


def _sync_run_record(
    sync_run_id: str = "sync_run_1",
    *,
    transaction_id: str = "dstx_sync",
    status: str = "EXTRACTING",
    committed_version_id: str | None = None,
) -> SyncRunRecord:
    return SyncRunRecord(
        sync_run_id=sync_run_id,
        tenant_id="tenant-demo",
        sync_name="upload:raw.orders",
        source_type="file.csv",
        output_dataset_id="ds_orders",
        transaction_id=transaction_id,
        committed_version_id=committed_version_id,
        status=status,
        error=None,
        created_at="2026-06-10T00:00:00Z",
        completed_at=None,
    )


def _webhook_event_matches(
    metadata: object,
    connector_name: str,
    resource_name: str,
    event_id: str,
) -> bool:
    if not isinstance(metadata, dict):
        return False
    event = metadata.get("webhookEvent")
    if not isinstance(event, dict):
        return False
    return (
        event.get("connectorName") == connector_name
        and event.get("resourceName") == resource_name
        and event.get("eventId") == event_id
    )


def test_dataset_transaction_repository_contract_sync_run_insert(harness: TransactionHarness) -> None:
    repository = harness.repository

    def insert(transaction: Any) -> None:
        repository.insert_sync_run(transaction=transaction, record=_sync_run_record())

    harness.call_in_transaction(insert)

    row = harness.sync_run_row(sync_run_id="sync_run_1")
    assert row is not None
    assert row["status"] == "EXTRACTING"
    assert row["sync_name"] == "upload:raw.orders"
    assert row["source_type"] == "file.csv"
    assert row["output_dataset_id"] == "ds_orders"
    assert row["committed_version_id"] is None
    assert row["completed_at"] is None


def test_dataset_transaction_repository_contract_sync_run_terminal_committed(
    harness: TransactionHarness,
) -> None:
    repository = harness.repository

    def insert_then_commit(transaction: Any) -> None:
        repository.insert_sync_run(transaction=transaction, record=_sync_run_record())
        repository.update_sync_run_terminal(
            transaction=transaction,
            tenant_id="tenant-demo",
            sync_run_id="sync_run_1",
            status="COMMITTED",
            committed_version_id="dsv_orders_1",
            completed_at="2026-06-10T00:05:00Z",
        )

    harness.call_in_transaction(insert_then_commit)

    row = harness.sync_run_row(sync_run_id="sync_run_1")
    assert row is not None
    assert row["status"] == "COMMITTED"
    assert row["committed_version_id"] == "dsv_orders_1"
    assert row["completed_at"] == "2026-06-10T00:05:00Z"
