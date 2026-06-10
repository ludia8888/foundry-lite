from __future__ import annotations

from typing import Any

from sqlalchemy import and_, insert, select, update
from sqlalchemy.engine import Engine

from foundry_lite.application.ports import (
    DatasetFileRecord,
    DatasetRunKind,
    DatasetTransactionRecord,
    DatasetVersionRecord,
    SyncRunRecord,
)
from foundry_lite.infrastructure import schema as db


class SqlAlchemyDatasetTransactionRepository:
    """SQLAlchemy implementation of dataset transaction state and version writes."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_open_transaction(self, *, transaction: Any, record: DatasetTransactionRecord) -> None:
        transaction.execute(
            insert(db.dataset_transactions).values(
                id=record.transaction_id,
                tenant_id=record.tenant_id,
                dataset_id=record.dataset_id,
                branch=record.branch,
                tx_type=record.tx_type,
                status=record.status,
                base_version_id=record.base_version_id,
                committed_version_id=record.committed_version_id,
                schema_version=record.schema_version,
                created_by=record.created_by,
                created_at=record.created_at,
                committed_at=record.committed_at,
                metadata=record.metadata,
            )
        )

    def transaction_by_id(self, *, transaction: Any, transaction_id: str) -> dict[str, Any] | None:
        row = (
            transaction.execute(select(db.dataset_transactions).where(db.dataset_transactions.c.id == transaction_id))
            .mappings()
            .first()
        )
        return dict(row) if row else None

    def abort_transaction(self, *, transaction: Any, transaction_id: str, metadata: dict[str, Any]) -> None:
        transaction.execute(
            update(db.dataset_transactions)
            .where(db.dataset_transactions.c.id == transaction_id)
            .values(status="ABORTED", metadata=metadata)
        )

    def insert_version(self, *, transaction: Any, record: DatasetVersionRecord) -> None:
        transaction.execute(
            insert(db.dataset_versions).values(
                id=record.version_id,
                tenant_id=record.tenant_id,
                dataset_id=record.dataset_id,
                branch=record.branch,
                version_number=record.version_number,
                transaction_id=record.transaction_id,
                schema_version=record.schema_version,
                manifest_uri=record.manifest_uri,
                row_count=record.row_count,
                byte_size=record.byte_size,
                status=record.status,
                superseded_by_version_id=record.superseded_by_version_id,
                created_at=record.created_at,
            )
        )

    def insert_file(self, *, transaction: Any, record: DatasetFileRecord) -> None:
        transaction.execute(
            insert(db.dataset_files).values(
                id=record.file_id,
                tenant_id=record.tenant_id,
                dataset_version_id=record.dataset_version_id,
                uri=record.uri,
                format=record.file_format,
                row_count=record.row_count,
                byte_size=record.byte_size,
                content_hash=record.content_hash,
                partition_values=record.partition_values,
            )
        )

    def commit_transaction(
        self,
        *,
        transaction: Any,
        transaction_id: str,
        committed_version_id: str,
        schema_version: int,
        committed_at: str,
    ) -> None:
        transaction.execute(
            update(db.dataset_transactions)
            .where(db.dataset_transactions.c.id == transaction_id)
            .values(
                status="COMMITTED",
                committed_version_id=committed_version_id,
                schema_version=schema_version,
                committed_at=committed_at,
            )
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
        with self.engine.begin() as transaction:
            transaction.execute(
                update(db.dataset_transactions)
                .where(
                    and_(
                        db.dataset_transactions.c.id == transaction_id,
                        db.dataset_transactions.c.status == "OPEN",
                    )
                )
                .values(status="ABORTED", metadata={"error": error})
            )
            run_table = _run_table(run_kind)
            transaction.execute(
                update(run_table)
                .where(run_table.c.id == run_id)
                .values(status="FAILED", error=error, completed_at=completed_at)
            )

    def insert_sync_run(self, *, transaction: Any, record: SyncRunRecord) -> None:
        transaction.execute(
            insert(db.sync_runs).values(
                id=record.sync_run_id,
                tenant_id=record.tenant_id,
                sync_name=record.sync_name,
                source_type=record.source_type,
                output_dataset_id=record.output_dataset_id,
                transaction_id=record.transaction_id,
                committed_version_id=record.committed_version_id,
                status=record.status,
                error=record.error,
                created_at=record.created_at,
                completed_at=record.completed_at,
            )
        )

    def update_sync_run_terminal(
        self,
        *,
        transaction: Any,
        sync_run_id: str,
        status: str,
        committed_version_id: str | None,
        completed_at: str,
    ) -> None:
        transaction.execute(
            update(db.sync_runs)
            .where(db.sync_runs.c.id == sync_run_id)
            .values(
                status=status,
                committed_version_id=committed_version_id,
                completed_at=completed_at,
            )
        )


def _run_table(run_kind: DatasetRunKind) -> Any:
    if run_kind == "sync":
        return db.sync_runs
    if run_kind == "transform":
        return db.transform_runs
    return db.materialization_runs
