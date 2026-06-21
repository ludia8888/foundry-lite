from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from threading import Barrier, Event
from typing import Any, Protocol

import pytest
from foundry_lite.application.ports import (
    DatasetFileRecord,
    DatasetRunKind,
    DatasetTransactionRecord,
    DatasetVersionConflictError,
    DatasetVersionRecord,
    DeadLetterRecord,
    SyncRunRecord,
)
from foundry_lite.application.ports.transaction_context import (
    SYNC_RUN_COMMITTED,
    StatusTransition,
    dataset_run_failed_transition,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import (
    SqlAlchemyDatasetTransactionRepository,
    SqlAlchemyDatasetVersionRepository,
)
from sqlalchemy import create_engine, insert, select
from sqlalchemy.engine import Engine


class TransactionHarness(Protocol):
    repository: Any

    def call_in_transaction(self, fn: Any) -> Any: ...

    def add_run(self, *, run_kind: DatasetRunKind, run_id: str, transaction_id: str) -> None: ...

    def add_dataset(self) -> None: ...

    def run_status(self, *, run_kind: DatasetRunKind, run_id: str) -> dict[str, Any] | None: ...

    def versions(self) -> list[dict[str, Any]]: ...

    def files(self) -> list[dict[str, Any]]: ...

    def sync_run_row(self, *, sync_run_id: str) -> dict[str, Any] | None: ...

    def dead_letter_records(self) -> list[dict[str, Any]]: ...


@dataclass
class FakeDatasetTransactionRepository:
    transactions: dict[str, dict[str, Any]] = field(default_factory=dict)
    versions_store: list[dict[str, Any]] = field(default_factory=list)
    files_store: list[dict[str, Any]] = field(default_factory=list)
    dead_letters_store: list[dict[str, Any]] = field(default_factory=list)
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
    ) -> bool:
        del transaction
        row = self.transactions[transaction_id]
        if row["tenant_id"] == tenant_id and row["status"] == "OPEN":
            row.update(status="ABORTED", metadata=metadata)
            return True
        return False

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
    ) -> bool:
        del transaction
        if self.transactions[transaction_id]["tenant_id"] != tenant_id:
            return False
        if self.transactions[transaction_id]["status"] != "OPEN":
            return False
        self.transactions[transaction_id].update(
            status="COMMITTED",
            committed_version_id=committed_version_id,
            schema_version=schema_version,
            committed_at=committed_at,
            metadata=dict(metadata or {}),
        )
        return True

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

    def committed_transaction_by_version(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        committed_version_id: str,
    ) -> dict[str, Any] | None:
        del transaction
        for row in self.transactions.values():
            if (
                row["tenant_id"] == tenant_id
                and row["committed_version_id"] == committed_version_id
                and row["status"] == "COMMITTED"
            ):
                return dict(row)
        return None

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
        if tx["status"] not in {"OPEN", "ABORTED"}:
            return False
        if tx["status"] == "OPEN":
            tx.update(status="ABORTED", metadata={"error": error})
        run = self.runs[(run_kind, run_id)]
        if run["status"] not in _failed_run_from_statuses(run_kind):
            return False
        run.update(status="FAILED", error=error, completed_at=completed_at)
        return True

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

    def sync_run_by_id(self, *, transaction: Any, tenant_id: str, sync_run_id: str) -> dict[str, Any] | None:
        del transaction
        row = self.sync_runs_store.get(sync_run_id)
        if row is None or row["tenant_id"] != tenant_id:
            return None
        return dict(row)

    def insert_dead_letter_record(self, *, transaction: Any, record: DeadLetterRecord) -> bool:
        del transaction
        if any(
            row["tenant_id"] == record.tenant_id
            and row["source_event_id"] == record.source_event_id
            and row["payload_hash"] == record.payload_hash
            for row in self.dead_letters_store
        ):
            return False
        self.dead_letters_store.append(_dead_letter_values(record))
        return True

    def list_dead_letter_records(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        del transaction
        rows = [
            dict(row)
            for row in self.dead_letters_store
            if row["tenant_id"] == tenant_id and (status is None or row["status"] == status)
        ]
        return sorted(rows, key=lambda row: (row["first_failed_at"], row["id"]))

    def dead_letter_record_by_id(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        record_id: str,
    ) -> dict[str, Any] | None:
        del transaction
        for row in self.dead_letters_store:
            if row["tenant_id"] == tenant_id and row["id"] == record_id:
                return dict(row)
        return None

    def update_dead_letter_record_replay_requested(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        record_id: str,
        replay_run_id: str,
        replay_idempotency_key: str,
        replay_requested_at: str,
        metadata: dict[str, Any],
        backfill_plan: dict[str, Any],
    ) -> dict[str, Any] | None:
        del transaction
        row = self._mutable_dead_letter(tenant_id, record_id)
        if row is None or row["status"] != "QUARANTINED":
            return None
        row.update(
            status="REPLAY_REQUESTED",
            replay_status="REQUESTED",
            replay_run_id=replay_run_id,
            replay_idempotency_key=replay_idempotency_key,
            replay_requested_at=replay_requested_at,
            replay_lease_token=None,
            replay_started_at=None,
            replay_lease_expires_at=None,
            metadata=dict(metadata),
            backfill_plan=dict(backfill_plan),
        )
        return dict(row)

    def claim_dead_letter_record_replay(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        record_id: str,
        replay_run_id: str,
        replay_lease_token: str,
        replay_started_at: str,
        replay_lease_expires_at: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        del transaction
        row = self._mutable_dead_letter(tenant_id, record_id)
        if row is None or not _can_claim_replay(row, replay_run_id, replay_started_at):
            return None
        row.update(
            status="REPLAYING",
            replay_status="REPLAYING",
            replay_lease_token=replay_lease_token,
            replay_started_at=replay_started_at,
            replay_lease_expires_at=replay_lease_expires_at,
            metadata=dict(metadata),
        )
        return dict(row)

    def update_dead_letter_record_replay_succeeded(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        record_id: str,
        replay_run_id: str,
        metadata: dict[str, Any],
        replay_lease_token: str | None = None,
    ) -> dict[str, Any] | None:
        del transaction
        row = self._mutable_dead_letter(tenant_id, record_id)
        if not _is_matching_replay(row, replay_run_id, replay_lease_token):
            return None
        row.update(
            status="RESOLVED",
            replay_status="SUCCEEDED",
            replay_run_id=replay_run_id,
            replay_lease_token=replay_lease_token,
            metadata=dict(metadata),
        )
        return dict(row)

    def update_dead_letter_record_replay_failed(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        record_id: str,
        replay_run_id: str,
        metadata: dict[str, Any],
        replay_lease_token: str | None = None,
    ) -> dict[str, Any] | None:
        del transaction
        row = self._mutable_dead_letter(tenant_id, record_id)
        if not _is_matching_replay(row, replay_run_id, replay_lease_token):
            return None
        row.update(
            status="QUARANTINED",
            replay_status="FAILED",
            replay_run_id=replay_run_id,
            replay_lease_token=replay_lease_token,
            attempts=row["attempts"] + 1,
            metadata=dict(metadata),
        )
        return dict(row)

    def discard_dead_letter_record(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        record_id: str,
        discarded_at: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        del transaction
        row = self._mutable_dead_letter(tenant_id, record_id)
        if row is None or row["status"] != "QUARANTINED":
            return None
        row.update(
            status="DISCARDED",
            replay_status="DISCARDED",
            discarded_at=discarded_at,
            metadata=dict(metadata),
        )
        return dict(row)

    def _mutable_dead_letter(self, tenant_id: str, record_id: str) -> dict[str, Any] | None:
        for row in self.dead_letters_store:
            if row["tenant_id"] == tenant_id and row["id"] == record_id:
                return row
        return None

    def update_sync_run_terminal(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        sync_run_id: str,
        transition: StatusTransition,
        committed_version_id: str | None,
        completed_at: str,
    ) -> bool:
        del transaction
        if self.sync_runs_store[sync_run_id]["tenant_id"] != tenant_id:
            return False
        if self.sync_runs_store[sync_run_id]["status"] not in transition.from_statuses:
            return False
        self.sync_runs_store[sync_run_id].update(
            status=transition.to_status,
            committed_version_id=committed_version_id,
            completed_at=completed_at,
        )
        return True


@dataclass
class FakeTransactionHarness:
    repository: FakeDatasetTransactionRepository

    def call_in_transaction(self, fn: Any) -> Any:
        return fn(None)

    def add_run(self, *, run_kind: DatasetRunKind, run_id: str, transaction_id: str) -> None:
        self.repository.runs[(run_kind, run_id)] = {
            "id": run_id,
            "transaction_id": transaction_id,
            "status": _initial_run_status(run_kind),
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

    def dead_letter_records(self) -> list[dict[str, Any]]:
        return list(self.repository.dead_letters_store)


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

    def dead_letter_records(self) -> list[dict[str, Any]]:
        with self.engine.begin() as transaction:
            rows = transaction.execute(
                select(db.dead_letter_records).order_by(db.dead_letter_records.c.first_failed_at)
            )
            return [dict(row) for row in rows.mappings().all()]


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


def _file_record_for(version_number: int, content_hash: str = "hash-demo") -> DatasetFileRecord:
    return DatasetFileRecord(
        file_id=f"dsf_orders_{version_number}",
        tenant_id="tenant-demo",
        dataset_version_id=f"dsv_orders_{version_number}",
        uri=f"memory://part-{version_number:05d}.parquet",
        file_format="parquet",
        row_count=3,
        byte_size=100,
        content_hash=content_hash,
        partition_values={},
    )


def _dead_letter_record(record_id: str = "dlqr_orders_bad_1") -> DeadLetterRecord:
    return DeadLetterRecord(
        dead_letter_record_id=record_id,
        tenant_id="tenant-demo",
        source_event_id="shipment_events:0:1",
        source_dataset_version_id=None,
        source_run_id="sync_stream_1",
        payload={"op": "u", "pk": "bad"},
        payload_hash="payload-hash-1",
        schema_version=1,
        transform_version=None,
        error_kind="VALIDATION_FAILED",
        error_message="cdc envelope field must be an object",
        event_time=None,
        ingested_at="2026-06-10T00:03:00Z",
        first_failed_at="2026-06-10T00:03:00Z",
        attempts=1,
        status="QUARANTINED",
        replay_status="NOT_REQUESTED",
        replay_run_id=None,
        is_closed_partition_affected=False,
        metadata={"stream": "shipments", "offset": 1},
    )


def _dead_letter_values(record: DeadLetterRecord) -> dict[str, Any]:
    return {
        "id": record.dead_letter_record_id,
        "tenant_id": record.tenant_id,
        "source_event_id": record.source_event_id,
        "source_dataset_version_id": record.source_dataset_version_id,
        "source_run_id": record.source_run_id,
        "payload": dict(record.payload),
        "payload_hash": record.payload_hash,
        "schema_version": record.schema_version,
        "transform_version": record.transform_version,
        "error_kind": record.error_kind,
        "error_message": record.error_message,
        "event_time": record.event_time,
        "ingested_at": record.ingested_at,
        "first_failed_at": record.first_failed_at,
        "attempts": record.attempts,
        "status": record.status,
        "replay_status": record.replay_status,
        "replay_run_id": record.replay_run_id,
        "replay_idempotency_key": record.replay_idempotency_key,
        "replay_requested_at": record.replay_requested_at,
        "replay_lease_token": record.replay_lease_token,
        "replay_started_at": record.replay_started_at,
        "replay_lease_expires_at": record.replay_lease_expires_at,
        "discarded_at": record.discarded_at,
        "backfill_plan": dict(record.backfill_plan) if record.backfill_plan is not None else None,
        "is_closed_partition_affected": record.is_closed_partition_affected,
        "metadata": dict(record.metadata),
    }


def _version_row_id(row: dict[str, Any]) -> str:
    return str(row["version_id" if "version_id" in row else "id"])


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
        "partition_spec": [],
        "sort_order": [],
        "target_file_size_bytes": None,
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
        "status": _initial_run_status(run_kind),
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


def _initial_run_status(run_kind: DatasetRunKind) -> str:
    if run_kind == "sync":
        return "EXTRACTING"
    if run_kind == "transform":
        return "RUNNING"
    return "running"


def _failed_run_from_statuses(run_kind: DatasetRunKind) -> tuple[str, ...]:
    return dataset_run_failed_transition(run_kind).from_statuses


def _is_matching_replay(row: dict[str, Any] | None, replay_run_id: str, replay_lease_token: str | None) -> bool:
    if row is None:
        return False
    if row["status"] not in {"REPLAY_REQUESTED", "REPLAYING"} or row["replay_run_id"] != replay_run_id:
        return False
    if replay_lease_token is None:
        return row.get("replay_lease_token") is None
    return row.get("replay_lease_token") == replay_lease_token


def _can_claim_replay(row: dict[str, Any], replay_run_id: str, replay_started_at: str) -> bool:
    if row["replay_run_id"] != replay_run_id:
        return False
    if row["status"] == "REPLAY_REQUESTED":
        return True
    return row["status"] == "REPLAYING" and str(row.get("replay_lease_expires_at")) < replay_started_at


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


def test_dataset_transaction_repository_contract_commit_requires_open_state(harness: TransactionHarness) -> None:
    repository = harness.repository

    def commit_twice(transaction: Any) -> tuple[bool, bool, dict[str, Any] | None]:
        repository.create_open_transaction(transaction=transaction, record=_transaction_record("dstx_commit_once"))
        repository.insert_version(transaction=transaction, record=_version_record("dstx_commit_once"))
        first = repository.commit_transaction(
            transaction=transaction,
            tenant_id="tenant-demo",
            transaction_id="dstx_commit_once",
            committed_version_id="dsv_orders_1",
            schema_version=1,
            committed_at="2026-06-10T00:02:00Z",
        )
        second = repository.commit_transaction(
            transaction=transaction,
            tenant_id="tenant-demo",
            transaction_id="dstx_commit_once",
            committed_version_id="dsv_orders_2",
            schema_version=2,
            committed_at="2026-06-10T00:03:00Z",
        )
        row = repository.transaction_by_id(transaction=transaction, transaction_id="dstx_commit_once")
        return first, second, row

    first, second, committed = harness.call_in_transaction(commit_twice)

    assert first is True
    assert second is False
    assert committed is not None
    assert committed["status"] == "COMMITTED"
    assert committed["committed_version_id"] == "dsv_orders_1"
    assert committed["schema_version"] == 1


def test_dataset_transaction_repository_contract_allows_same_content_hash_as_new_version(
    harness: TransactionHarness,
) -> None:
    repository = harness.repository

    def commit_same_content_hash_twice(transaction: Any) -> None:
        for version_number in (1, 2):
            transaction_id = f"dstx_same_hash_{version_number}"
            repository.create_open_transaction(transaction=transaction, record=_transaction_record(transaction_id))
            repository.insert_version(
                transaction=transaction,
                record=_version_record_for(transaction_id, version_number),
            )
            repository.insert_file(
                transaction=transaction,
                record=_file_record_for(version_number, content_hash="same-content-hash"),
            )
            repository.commit_transaction(
                transaction=transaction,
                tenant_id="tenant-demo",
                transaction_id=transaction_id,
                committed_version_id=f"dsv_orders_{version_number}",
                schema_version=1,
                committed_at=f"2026-06-10T00:0{version_number}:30Z",
            )

    harness.call_in_transaction(commit_same_content_hash_twice)

    version_ids = {_version_row_id(row) for row in harness.versions()}
    files = harness.files()
    assert version_ids == {"dsv_orders_1", "dsv_orders_2"}
    assert {file["dataset_version_id"] for file in files} == version_ids
    assert {file["content_hash"] for file in files} == {"same-content-hash"}


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


def test_dataset_transaction_repository_contract_finds_committed_transaction_by_version(
    harness: TransactionHarness,
) -> None:
    repository = harness.repository

    def commit_and_lookup(transaction: Any) -> dict[str, Any] | None:
        repository.create_open_transaction(transaction=transaction, record=_transaction_record("dstx_version_lookup"))
        repository.commit_transaction(
            transaction=transaction,
            tenant_id="tenant-demo",
            transaction_id="dstx_version_lookup",
            committed_version_id="dsv_lookup",
            schema_version=1,
            committed_at="2026-06-10T00:02:00Z",
            metadata={"materializationDetail": {"apiName": "order_current"}},
        )
        return repository.committed_transaction_by_version(
            transaction=transaction,
            tenant_id="tenant-demo",
            committed_version_id="dsv_lookup",
        )

    row = harness.call_in_transaction(commit_and_lookup)

    assert row is not None
    assert row["id"] == "dstx_version_lookup"
    assert row["metadata"] == {"materializationDetail": {"apiName": "order_current"}}


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


@pytest.mark.parametrize("run_kind", ["sync", "transform", "materialization"])
def test_dataset_transaction_repository_contract_fails_run_when_transaction_already_aborted(
    harness: TransactionHarness,
    run_kind: DatasetRunKind,
) -> None:
    repository = harness.repository

    harness.call_in_transaction(
        lambda transaction: repository.create_open_transaction(
            transaction=transaction,
            record=_transaction_record("dstx_quality_blocked"),
        )
    )
    harness.call_in_transaction(
        lambda transaction: repository.abort_transaction(
            transaction=transaction,
            tenant_id="tenant-demo",
            transaction_id="dstx_quality_blocked",
            metadata={"validationFailures": [{"check": "unique"}]},
        )
    )
    harness.add_run(run_kind=run_kind, run_id="run_quality_blocked", transaction_id="dstx_quality_blocked")

    updated = harness.call_in_transaction(
        lambda transaction: repository.abort_open_transaction_and_fail_run(
            transaction=transaction,
            tenant_id="tenant-demo",
            transaction_id="dstx_quality_blocked",
            run_id="run_quality_blocked",
            run_kind=run_kind,
            error={"code": "VALIDATION_FAILED"},
            completed_at="2026-06-10T00:04:00Z",
        )
    )
    failed_run = harness.run_status(run_kind=run_kind, run_id="run_quality_blocked")
    failed_tx = harness.call_in_transaction(
        lambda transaction: repository.transaction_by_id(
            transaction=transaction,
            transaction_id="dstx_quality_blocked",
        )
    )

    assert updated is True
    assert failed_run is not None
    assert failed_run["status"] == "FAILED"
    assert failed_tx is not None
    assert failed_tx["metadata"] == {"validationFailures": [{"check": "unique"}]}


def test_dataset_transaction_repository_contract_dead_letter_record_is_tenant_scoped(
    harness: TransactionHarness,
) -> None:
    repository = harness.repository

    def insert_records(transaction: Any) -> list[dict[str, Any]]:
        inserted = repository.insert_dead_letter_record(transaction=transaction, record=_dead_letter_record())
        duplicate = repository.insert_dead_letter_record(
            transaction=transaction, record=_dead_letter_record("dlqr_dup")
        )
        repository.insert_dead_letter_record(
            transaction=transaction,
            record=replace(
                _dead_letter_record("dlqr_other"), tenant_id="tenant-other", payload_hash="payload-hash-other"
            ),
        )
        rows = repository.list_dead_letter_records(
            transaction=transaction,
            tenant_id="tenant-demo",
            status="QUARANTINED",
        )
        return [{"inserted": inserted, "duplicate": duplicate}, *rows]

    rows = harness.call_in_transaction(insert_records)

    assert rows[0] == {"inserted": True, "duplicate": False}
    assert [row["id"] for row in rows[1:]] == ["dlqr_orders_bad_1"]
    assert rows[1]["payload"] == {"op": "u", "pk": "bad"}
    assert rows[1]["replay_status"] == "NOT_REQUESTED"
    assert harness.dead_letter_records()[0]["metadata"] == {"stream": "shipments", "offset": 1}


def test_dataset_transaction_repository_contract_dead_letter_record_replay_and_discard(
    harness: TransactionHarness,
) -> None:
    repository = harness.repository

    def replay_flow(transaction: Any) -> dict[str, Any]:
        repository.insert_dead_letter_record(transaction=transaction, record=_dead_letter_record())
        repository.insert_dead_letter_record(
            transaction=transaction,
            record=replace(
                _dead_letter_record("dlqr_orders_bad_2"),
                source_event_id="shipment_events:0:2",
                payload_hash="payload-hash-2",
            ),
        )
        updated = repository.update_dead_letter_record_replay_requested(
            transaction=transaction,
            tenant_id="tenant-demo",
            record_id="dlqr_orders_bad_1",
            replay_run_id="record_replay_1",
            replay_idempotency_key="retry-key-1",
            replay_requested_at="2026-06-10T00:04:00Z",
            metadata={"stream": "shipments", "lastOperation": {"operation": "retry"}},
            backfill_plan={"nextStep": "Replay worker must re-ingest this record."},
        )
        duplicate_request = repository.update_dead_letter_record_replay_requested(
            transaction=transaction,
            tenant_id="tenant-demo",
            record_id="dlqr_orders_bad_1",
            replay_run_id="record_replay_duplicate",
            replay_idempotency_key="retry-key-duplicate",
            replay_requested_at="2026-06-10T00:04:01Z",
            metadata={"stream": "shipments", "lastOperation": {"operation": "retry"}},
            backfill_plan={"nextStep": "Replay worker must re-ingest this record."},
        )
        failed_request = repository.update_dead_letter_record_replay_requested(
            transaction=transaction,
            tenant_id="tenant-demo",
            record_id="dlqr_orders_bad_2",
            replay_run_id="record_replay_2",
            replay_idempotency_key="retry-key-2",
            replay_requested_at="2026-06-10T00:04:02Z",
            metadata={"stream": "shipments", "lastOperation": {"operation": "retry"}},
            backfill_plan={"nextStep": "Replay worker must re-ingest this record."},
        )
        assert updated is not None
        assert duplicate_request is None
        assert failed_request is not None
        succeeded = repository.update_dead_letter_record_replay_succeeded(
            transaction=transaction,
            tenant_id="tenant-demo",
            record_id="dlqr_orders_bad_1",
            replay_run_id="record_replay_1",
            metadata={
                "stream": "shipments",
                "replayResult": {"datasetVersionId": "dsv_replay_1", "rowCount": 1},
            },
        )
        failed = repository.update_dead_letter_record_replay_failed(
            transaction=transaction,
            tenant_id="tenant-demo",
            record_id="dlqr_orders_bad_2",
            replay_run_id="record_replay_2",
            metadata={"stream": "shipments", "lastReplayError": {"type": "ValidationFailed"}},
        )
        assert succeeded is not None
        assert failed is not None
        return {"requested": updated, "succeeded": succeeded, "failed": failed}

    replay_result = harness.call_in_transaction(replay_flow)
    missing = harness.call_in_transaction(
        lambda transaction: repository.dead_letter_record_by_id(
            transaction=transaction,
            tenant_id="tenant-other",
            record_id="dlqr_orders_bad_1",
        )
    )
    discarded = harness.call_in_transaction(
        lambda transaction: repository.discard_dead_letter_record(
            transaction=transaction,
            tenant_id="tenant-demo",
            record_id="dlqr_orders_bad_2",
            discarded_at="2026-06-10T00:05:00Z",
            metadata={"stream": "shipments", "lastOperation": {"operation": "discard"}},
        )
    )

    replayed = replay_result["requested"]
    succeeded = replay_result["succeeded"]
    failed = replay_result["failed"]
    assert replayed["status"] == "REPLAY_REQUESTED"
    assert replayed["replay_status"] == "REQUESTED"
    assert replayed["replay_idempotency_key"] == "retry-key-1"
    assert replayed["backfill_plan"] == {"nextStep": "Replay worker must re-ingest this record."}
    assert succeeded["status"] == "RESOLVED"
    assert succeeded["replay_status"] == "SUCCEEDED"
    assert succeeded["metadata"]["replayResult"] == {"datasetVersionId": "dsv_replay_1", "rowCount": 1}
    assert failed["status"] == "QUARANTINED"
    assert failed["replay_status"] == "FAILED"
    assert failed["attempts"] == 2
    assert missing is None
    assert discarded is not None
    assert discarded["status"] == "DISCARDED"
    assert discarded["discarded_at"] == "2026-06-10T00:05:00Z"


def test_dataset_transaction_repository_contract_dead_letter_replay_lease_fences_terminal_updates(
    harness: TransactionHarness,
) -> None:
    repository = harness.repository

    def lease_flow(transaction: Any) -> dict[str, Any | None]:
        repository.insert_dead_letter_record(transaction=transaction, record=_dead_letter_record())
        repository.insert_dead_letter_record(
            transaction=transaction,
            record=replace(
                _dead_letter_record("dlqr_orders_bad_2"),
                source_event_id="shipment_events:0:2",
                payload_hash="payload-hash-2",
            ),
        )
        requested = repository.update_dead_letter_record_replay_requested(
            transaction=transaction,
            tenant_id="tenant-demo",
            record_id="dlqr_orders_bad_1",
            replay_run_id="record_replay_1",
            replay_idempotency_key="retry-key-1",
            replay_requested_at="2026-06-10T00:04:00Z",
            metadata={"stream": "shipments", "lastOperation": {"operation": "retry"}},
            backfill_plan={"nextStep": "Replay worker must re-ingest this record."},
        )
        claimed = repository.claim_dead_letter_record_replay(
            transaction=transaction,
            tenant_id="tenant-demo",
            record_id="dlqr_orders_bad_1",
            replay_run_id="record_replay_1",
            replay_lease_token="lease-token-1",
            replay_started_at="2026-06-10T00:04:01Z",
            replay_lease_expires_at="2026-06-10T00:04:30Z",
            metadata={"stream": "shipments", "replayLease": {"token": "lease-token-1"}},
        )
        duplicate_claim = repository.claim_dead_letter_record_replay(
            transaction=transaction,
            tenant_id="tenant-demo",
            record_id="dlqr_orders_bad_1",
            replay_run_id="record_replay_1",
            replay_lease_token="lease-token-2",
            replay_started_at="2026-06-10T00:04:02Z",
            replay_lease_expires_at="2026-06-10T00:04:40Z",
            metadata={"stream": "shipments", "replayLease": {"token": "lease-token-2"}},
        )
        missing_token_terminal = repository.update_dead_letter_record_replay_succeeded(
            transaction=transaction,
            tenant_id="tenant-demo",
            record_id="dlqr_orders_bad_1",
            replay_run_id="record_replay_1",
            metadata={"stream": "shipments", "replayResult": {"datasetVersionId": "dsv_wrong"}},
        )
        wrong_token_terminal = repository.update_dead_letter_record_replay_succeeded(
            transaction=transaction,
            tenant_id="tenant-demo",
            record_id="dlqr_orders_bad_1",
            replay_run_id="record_replay_1",
            replay_lease_token="wrong-token",
            metadata={"stream": "shipments", "replayResult": {"datasetVersionId": "dsv_wrong"}},
        )
        succeeded = repository.update_dead_letter_record_replay_succeeded(
            transaction=transaction,
            tenant_id="tenant-demo",
            record_id="dlqr_orders_bad_1",
            replay_run_id="record_replay_1",
            replay_lease_token="lease-token-1",
            metadata={"stream": "shipments", "replayResult": {"datasetVersionId": "dsv_replay_1"}},
        )
        second_requested = repository.update_dead_letter_record_replay_requested(
            transaction=transaction,
            tenant_id="tenant-demo",
            record_id="dlqr_orders_bad_2",
            replay_run_id="record_replay_2",
            replay_idempotency_key="retry-key-2",
            replay_requested_at="2026-06-10T00:05:00Z",
            metadata={"stream": "shipments", "lastOperation": {"operation": "retry"}},
            backfill_plan={"nextStep": "Replay worker must re-ingest this record."},
        )
        first_expiring_claim = repository.claim_dead_letter_record_replay(
            transaction=transaction,
            tenant_id="tenant-demo",
            record_id="dlqr_orders_bad_2",
            replay_run_id="record_replay_2",
            replay_lease_token="lease-token-old",
            replay_started_at="2026-06-10T00:05:01Z",
            replay_lease_expires_at="2026-06-10T00:05:10Z",
            metadata={"stream": "shipments", "replayLease": {"token": "lease-token-old"}},
        )
        reclaimed = repository.claim_dead_letter_record_replay(
            transaction=transaction,
            tenant_id="tenant-demo",
            record_id="dlqr_orders_bad_2",
            replay_run_id="record_replay_2",
            replay_lease_token="lease-token-new",
            replay_started_at="2026-06-10T00:05:11Z",
            replay_lease_expires_at="2026-06-10T00:06:00Z",
            metadata={"stream": "shipments", "replayLease": {"token": "lease-token-new"}},
        )
        old_token_terminal = repository.update_dead_letter_record_replay_failed(
            transaction=transaction,
            tenant_id="tenant-demo",
            record_id="dlqr_orders_bad_2",
            replay_run_id="record_replay_2",
            replay_lease_token="lease-token-old",
            metadata={"stream": "shipments", "lastReplayError": {"type": "stale-worker"}},
        )
        failed = repository.update_dead_letter_record_replay_failed(
            transaction=transaction,
            tenant_id="tenant-demo",
            record_id="dlqr_orders_bad_2",
            replay_run_id="record_replay_2",
            replay_lease_token="lease-token-new",
            metadata={"stream": "shipments", "lastReplayError": {"type": "ValidationFailed"}},
        )
        return {
            "requested": requested,
            "claimed": claimed,
            "duplicate_claim": duplicate_claim,
            "missing_token_terminal": missing_token_terminal,
            "wrong_token_terminal": wrong_token_terminal,
            "succeeded": succeeded,
            "second_requested": second_requested,
            "first_expiring_claim": first_expiring_claim,
            "reclaimed": reclaimed,
            "old_token_terminal": old_token_terminal,
            "failed": failed,
        }

    result = harness.call_in_transaction(lease_flow)

    assert result["requested"] is not None
    assert result["claimed"] is not None
    assert result["duplicate_claim"] is None
    assert result["missing_token_terminal"] is None
    assert result["wrong_token_terminal"] is None
    assert result["succeeded"] is not None
    assert result["succeeded"]["status"] == "RESOLVED"
    assert result["succeeded"]["replay_lease_token"] == "lease-token-1"
    assert result["second_requested"] is not None
    assert result["first_expiring_claim"] is not None
    assert result["reclaimed"] is not None
    assert result["reclaimed"]["replay_lease_token"] == "lease-token-new"
    assert result["old_token_terminal"] is None
    assert result["failed"] is not None
    assert result["failed"]["status"] == "QUARANTINED"
    assert result["failed"]["replay_status"] == "FAILED"


def test_concurrent_replay_requests_create_one_replay_run(postgres_fixture: Any) -> None:
    engine = postgres_fixture.engine
    repository = SqlAlchemyDatasetTransactionRepository(engine)
    start = Barrier(2, timeout=5)

    with engine.begin() as transaction:
        repository.insert_dead_letter_record(transaction=transaction, record=_dead_letter_record())

    def request_replay(replay_run_id: str) -> dict[str, Any] | None:
        with engine.begin() as transaction:
            start.wait()
            return repository.update_dead_letter_record_replay_requested(
                transaction=transaction,
                tenant_id="tenant-demo",
                record_id="dlqr_orders_bad_1",
                replay_run_id=replay_run_id,
                replay_idempotency_key="retry-key-1",
                replay_requested_at="2026-06-10T00:04:00Z",
                metadata={"stream": "shipments", "lastOperation": {"operation": "retry"}},
                backfill_plan={"nextStep": "Replay worker must re-ingest this record."},
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            executor.submit(request_replay, "record_replay_1"),
            executor.submit(request_replay, "record_replay_2"),
        ]
        rows = [future.result() for future in results]

    stored = SqlAlchemyTransactionHarness(repository, engine).dead_letter_records()[0]
    winners = [row for row in rows if row is not None]
    assert len(winners) == 1
    assert stored["status"] == "REPLAY_REQUESTED"
    assert stored["replay_idempotency_key"] == "retry-key-1"
    assert stored["replay_run_id"] == winners[0]["replay_run_id"]
    assert stored["replay_run_id"] in {"record_replay_1", "record_replay_2"}


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


def test_dataset_transaction_repository_contract_sync_run_lookup_is_tenant_scoped(
    harness: TransactionHarness,
) -> None:
    repository = harness.repository

    def insert_and_lookup(transaction: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        repository.insert_sync_run(transaction=transaction, record=_sync_run_record())
        found = repository.sync_run_by_id(transaction=transaction, tenant_id="tenant-demo", sync_run_id="sync_run_1")
        hidden = repository.sync_run_by_id(transaction=transaction, tenant_id="tenant-other", sync_run_id="sync_run_1")
        return found, hidden

    found, hidden = harness.call_in_transaction(insert_and_lookup)

    assert found is not None
    assert found["id"] == "sync_run_1"
    assert found["transaction_id"] == "dstx_sync"
    assert hidden is None


def test_dataset_transaction_repository_contract_sync_run_terminal_committed(
    harness: TransactionHarness,
) -> None:
    repository = harness.repository

    def insert_then_commit(transaction: Any) -> tuple[bool, bool]:
        repository.insert_sync_run(transaction=transaction, record=_sync_run_record())
        updated = repository.update_sync_run_terminal(
            transaction=transaction,
            tenant_id="tenant-demo",
            sync_run_id="sync_run_1",
            transition=SYNC_RUN_COMMITTED,
            committed_version_id="dsv_orders_1",
            completed_at="2026-06-10T00:05:00Z",
        )
        stale = repository.update_sync_run_terminal(
            transaction=transaction,
            tenant_id="tenant-demo",
            sync_run_id="sync_run_1",
            transition=SYNC_RUN_COMMITTED,
            committed_version_id="dsv_orders_2",
            completed_at="2026-06-10T00:06:00Z",
        )
        return updated, stale

    updated, stale = harness.call_in_transaction(insert_then_commit)

    row = harness.sync_run_row(sync_run_id="sync_run_1")
    assert row is not None
    assert updated is True
    assert stale is False
    assert row["status"] == "COMMITTED"
    assert row["committed_version_id"] == "dsv_orders_1"
    assert row["completed_at"] == "2026-06-10T00:05:00Z"
