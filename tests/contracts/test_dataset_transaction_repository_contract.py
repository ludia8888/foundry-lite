from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import pytest
from foundry_lite.application.ports import (
    DatasetFileRecord,
    DatasetRunKind,
    DatasetTransactionRecord,
    DatasetTransactionRepository,
    DatasetVersionRecord,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyDatasetTransactionRepository
from sqlalchemy import create_engine, insert, select
from sqlalchemy.engine import Engine


class TransactionHarness(Protocol):
    repository: DatasetTransactionRepository

    def call_in_transaction(self, fn: Any) -> Any: ...

    def add_run(self, *, run_kind: DatasetRunKind, run_id: str, transaction_id: str) -> None: ...

    def run_status(self, *, run_kind: DatasetRunKind, run_id: str) -> dict[str, Any] | None: ...

    def versions(self) -> list[dict[str, Any]]: ...

    def files(self) -> list[dict[str, Any]]: ...


@dataclass
class FakeDatasetTransactionRepository:
    transactions: dict[str, dict[str, Any]] = field(default_factory=dict)
    versions_store: list[dict[str, Any]] = field(default_factory=list)
    files_store: list[dict[str, Any]] = field(default_factory=list)
    runs: dict[tuple[DatasetRunKind, str], dict[str, Any]] = field(default_factory=dict)

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

    def abort_transaction(self, *, transaction: Any, transaction_id: str, metadata: dict[str, Any]) -> None:
        del transaction
        self.transactions[transaction_id].update(status="ABORTED", metadata=metadata)

    def insert_version(self, *, transaction: Any, record: DatasetVersionRecord) -> None:
        del transaction
        self.versions_store.append(record.__dict__.copy())

    def insert_file(self, *, transaction: Any, record: DatasetFileRecord) -> None:
        del transaction
        self.files_store.append(record.__dict__.copy())

    def commit_transaction(
        self,
        *,
        transaction: Any,
        transaction_id: str,
        committed_version_id: str,
        schema_version: int,
        committed_at: str,
    ) -> None:
        del transaction
        self.transactions[transaction_id].update(
            status="COMMITTED",
            committed_version_id=committed_version_id,
            schema_version=schema_version,
            committed_at=committed_at,
        )

    def abort_open_transaction_and_fail_run(
        self,
        *,
        transaction_id: str,
        run_id: str,
        run_kind: DatasetRunKind,
        error: dict[str, Any],
        completed_at: str,
    ) -> None:
        tx = self.transactions[transaction_id]
        if tx["status"] == "OPEN":
            tx.update(status="ABORTED", metadata={"error": error})
        self.runs[(run_kind, run_id)].update(status="FAILED", error=error, completed_at=completed_at)


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

    def run_status(self, *, run_kind: DatasetRunKind, run_id: str) -> dict[str, Any] | None:
        row = self.repository.runs.get((run_kind, run_id))
        return dict(row) if row else None

    def versions(self) -> list[dict[str, Any]]:
        return list(self.repository.versions_store)

    def files(self) -> list[dict[str, Any]]:
        return list(self.repository.files_store)


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


def _transaction_record(transaction_id: str, *, status: str = "OPEN") -> DatasetTransactionRecord:
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
        created_at="2026-06-10T00:00:00Z",
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


@pytest.fixture(params=["sqlalchemy", "fake"])
def harness(request: pytest.FixtureRequest, tmp_path) -> TransactionHarness:
    if request.param == "fake":
        return FakeTransactionHarness(FakeDatasetTransactionRepository())
    engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}", future=True)
    db.create_database(engine)
    return SqlAlchemyTransactionHarness(SqlAlchemyDatasetTransactionRepository(engine), engine)


def test_dataset_transaction_repository_contract_commit_flow(harness: TransactionHarness) -> None:
    repository = harness.repository

    def commit_flow(transaction: Any) -> dict[str, Any] | None:
        repository.create_open_transaction(transaction=transaction, record=_transaction_record("dstx_commit"))
        repository.insert_version(transaction=transaction, record=_version_record("dstx_commit"))
        repository.insert_file(transaction=transaction, record=_file_record())
        repository.commit_transaction(
            transaction=transaction,
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


def test_dataset_transaction_repository_contract_abort_flow(harness: TransactionHarness) -> None:
    repository = harness.repository

    def abort_flow(transaction: Any) -> dict[str, Any] | None:
        repository.create_open_transaction(transaction=transaction, record=_transaction_record("dstx_abort"))
        repository.abort_transaction(
            transaction=transaction,
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

    repository.abort_open_transaction_and_fail_run(
        transaction_id="dstx_fail",
        run_id="run_1",
        run_kind=run_kind,
        error={"code": "VALIDATION_FAILED"},
        completed_at="2026-06-10T00:03:00Z",
    )
    failed_run = harness.run_status(run_kind=run_kind, run_id="run_1")
    failed_tx = harness.call_in_transaction(
        lambda transaction: repository.transaction_by_id(transaction=transaction, transaction_id="dstx_fail")
    )

    assert failed_run is not None
    assert failed_run["status"] == "FAILED"
    assert failed_tx is not None
    assert failed_tx["status"] == "ABORTED"
    assert failed_tx["metadata"] == {"error": {"code": "VALIDATION_FAILED"}}
