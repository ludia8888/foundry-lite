from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from sqlalchemy import and_, insert, or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from foundry_lite.application.ports import (
    DatasetFileRecord,
    DatasetRunError,
    DatasetRunKind,
    DatasetTransactionMetadata,
    DatasetTransactionRecord,
    DatasetTransactionRow,
    DatasetVersionConflictError,
    DatasetVersionRecord,
    DeadLetterRecord,
    DeadLetterRecordRow,
    DeadLetterRecordStatus,
    SyncRunRecord,
    SyncRunRow,
    WebhookEventKeyRecord,
)
from foundry_lite.application.ports.transaction_context import (
    DATASET_TRANSACTION_ABORT,
    DATASET_TRANSACTION_ABORTING,
    DATASET_TRANSACTION_COMMIT,
    DEAD_LETTER_DISCARDED,
    DEAD_LETTER_REPLAY_FAILED,
    DEAD_LETTER_REPLAY_REQUESTED,
    DEAD_LETTER_REPLAY_RUNNING,
    DEAD_LETTER_REPLAY_SUCCEEDED,
    StatusTransition,
    dataset_run_failed_transition,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.status_cas import cas_status_update


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
                metadata=dict(record.metadata),
            )
        )

    def transaction_by_id(self, *, transaction: Any, transaction_id: str) -> DatasetTransactionRow | None:
        row = (
            transaction.execute(select(db.dataset_transactions).where(db.dataset_transactions.c.id == transaction_id))
            .mappings()
            .first()
        )
        return cast(DatasetTransactionRow, dict(row)) if row else None

    def list_open_transactions(
        self, *, transaction: Any, tenant_id: str, created_before: str
    ) -> list[DatasetTransactionRow]:
        rows = (
            transaction.execute(
                select(db.dataset_transactions)
                .where(
                    and_(
                        db.dataset_transactions.c.tenant_id == tenant_id,
                        db.dataset_transactions.c.status == "OPEN",
                        db.dataset_transactions.c.created_at < created_before,
                    )
                )
                .order_by(db.dataset_transactions.c.created_at)
            )
            .mappings()
            .all()
        )
        return [cast(DatasetTransactionRow, dict(row)) for row in rows]

    def list_recoverable_transactions(
        self, *, transaction: Any, tenant_id: str, created_before: str
    ) -> list[DatasetTransactionRow]:
        rows = (
            transaction.execute(
                select(db.dataset_transactions)
                .where(
                    and_(
                        db.dataset_transactions.c.tenant_id == tenant_id,
                        db.dataset_transactions.c.status.in_(("OPEN", "ABORTING")),
                        db.dataset_transactions.c.created_at < created_before,
                    )
                )
                .order_by(db.dataset_transactions.c.created_at)
            )
            .mappings()
            .all()
        )
        return [cast(DatasetTransactionRow, dict(row)) for row in rows]

    def claim_stale_open_transaction(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        transaction_id: str,
        metadata: DatasetTransactionMetadata,
    ) -> DatasetTransactionRow | None:
        claimed = cas_status_update(
            transaction,
            db.dataset_transactions,
            tenant_id=tenant_id,
            row_id=transaction_id,
            transition=DATASET_TRANSACTION_ABORTING,
            values={"metadata": dict(metadata)},
        )
        if not claimed:
            return None
        return self.transaction_by_id(transaction=transaction, transaction_id=transaction_id)

    def abort_transaction(
        self, *, transaction: Any, tenant_id: str, transaction_id: str, metadata: DatasetTransactionMetadata
    ) -> bool:
        return cas_status_update(
            transaction,
            db.dataset_transactions,
            tenant_id=tenant_id,
            row_id=transaction_id,
            transition=DATASET_TRANSACTION_ABORT,
            values={"metadata": dict(metadata)},
        )

    def lock_dataset_for_version_allocation(self, *, transaction: Any, tenant_id: str, dataset_id: str) -> None:
        transaction.execute(
            select(db.datasets.c.id)
            .where(and_(db.datasets.c.tenant_id == tenant_id, db.datasets.c.id == dataset_id))
            .with_for_update()
        ).first()

    def insert_version(self, *, transaction: Any, record: DatasetVersionRecord) -> None:
        savepoint = transaction.begin_nested()
        try:
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
            savepoint.commit()
        except IntegrityError as exc:
            savepoint.rollback()
            raise DatasetVersionConflictError(
                f"dataset version already exists: {record.dataset_id}@{record.branch}#{record.version_number}"
            ) from exc

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
                partition_values=dict(record.partition_values),
            )
        )

    def insert_webhook_event_key(self, *, transaction: Any, record: WebhookEventKeyRecord) -> None:
        savepoint = transaction.begin_nested()
        try:
            transaction.execute(
                insert(db.webhook_event_keys).values(
                    id=record.webhook_event_key_id,
                    tenant_id=record.tenant_id,
                    dataset_id=record.dataset_id,
                    connector_name=record.connector_name,
                    resource_name=record.resource_name,
                    event_id=record.event_id,
                    transaction_id=record.transaction_id,
                    committed_version_id=record.committed_version_id,
                    payload_hash=record.payload_hash,
                    created_at=record.created_at,
                )
            )
            savepoint.commit()
        except IntegrityError as exc:
            savepoint.rollback()
            raise DatasetVersionConflictError(
                f"webhook event already exists: {record.dataset_id}:{record.connector_name}:{record.resource_name}:"
                f"{record.event_id}"
            ) from exc

    def commit_transaction(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        transaction_id: str,
        committed_version_id: str,
        schema_version: int,
        committed_at: str,
        metadata: DatasetTransactionMetadata | None = None,
    ) -> bool:
        return cas_status_update(
            transaction,
            db.dataset_transactions,
            tenant_id=tenant_id,
            row_id=transaction_id,
            transition=DATASET_TRANSACTION_COMMIT,
            values={
                "committed_version_id": committed_version_id,
                "schema_version": schema_version,
                "committed_at": committed_at,
                "metadata": dict(metadata or {}),
            },
        )

    def latest_committed_transaction(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        dataset_id: str,
    ) -> DatasetTransactionRow | None:
        row = (
            transaction.execute(
                select(db.dataset_transactions)
                .where(
                    and_(
                        db.dataset_transactions.c.tenant_id == tenant_id,
                        db.dataset_transactions.c.dataset_id == dataset_id,
                        db.dataset_transactions.c.status == "COMMITTED",
                    )
                )
                .order_by(db.dataset_transactions.c.committed_at.desc(), db.dataset_transactions.c.created_at.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        return cast(DatasetTransactionRow, dict(row)) if row else None

    def committed_transaction_by_version(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        committed_version_id: str,
    ) -> DatasetTransactionRow | None:
        row = (
            transaction.execute(
                select(db.dataset_transactions).where(
                    and_(
                        db.dataset_transactions.c.tenant_id == tenant_id,
                        db.dataset_transactions.c.committed_version_id == committed_version_id,
                        db.dataset_transactions.c.status == "COMMITTED",
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(DatasetTransactionRow, dict(row)) if row else None

    def committed_webhook_transaction_by_event(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        dataset_id: str,
        connector_name: str,
        resource_name: str,
        event_id: str,
    ) -> DatasetTransactionRow | None:
        indexed = self._webhook_event_transaction(
            transaction=transaction,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            connector_name=connector_name,
            resource_name=resource_name,
            event_id=event_id,
        )
        if indexed is not None:
            return indexed
        rows = (
            transaction.execute(
                select(db.dataset_transactions)
                .where(
                    and_(
                        db.dataset_transactions.c.tenant_id == tenant_id,
                        db.dataset_transactions.c.dataset_id == dataset_id,
                        db.dataset_transactions.c.status == "COMMITTED",
                    )
                )
                .order_by(db.dataset_transactions.c.committed_at.desc(), db.dataset_transactions.c.created_at.desc())
            )
            .mappings()
            .all()
        )
        for row in rows:
            candidate = dict(row)
            if _webhook_event_matches(candidate.get("metadata"), connector_name, resource_name, event_id):
                return cast(DatasetTransactionRow, candidate)
        return None

    def _webhook_event_transaction(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        dataset_id: str,
        connector_name: str,
        resource_name: str,
        event_id: str,
    ) -> DatasetTransactionRow | None:
        event_key = (
            transaction.execute(
                select(db.webhook_event_keys).where(
                    and_(
                        db.webhook_event_keys.c.tenant_id == tenant_id,
                        db.webhook_event_keys.c.dataset_id == dataset_id,
                        db.webhook_event_keys.c.connector_name == connector_name,
                        db.webhook_event_keys.c.resource_name == resource_name,
                        db.webhook_event_keys.c.event_id == event_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        if event_key is None:
            return None
        row = self.transaction_by_id(transaction=transaction, transaction_id=str(event_key["transaction_id"]))
        if row is None or row["tenant_id"] != tenant_id or row["dataset_id"] != dataset_id:
            return None
        if row["status"] != "COMMITTED":
            return None
        return cast(DatasetTransactionRow, _transaction_with_webhook_key_metadata(dict(row), dict(event_key)))

    def abort_open_transaction_and_fail_run(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        transaction_id: str,
        run_id: str,
        run_kind: DatasetRunKind,
        error: DatasetRunError,
        completed_at: str,
    ) -> bool:
        aborted = cas_status_update(
            transaction,
            db.dataset_transactions,
            tenant_id=tenant_id,
            row_id=transaction_id,
            transition=DATASET_TRANSACTION_ABORT,
            values={"metadata": {"error": dict(error)}},
        )
        if not aborted:
            tx = self.transaction_by_id(transaction=transaction, transaction_id=transaction_id)
            if tx is None or tx["tenant_id"] != tenant_id or tx["status"] != "ABORTED":
                return False
        run_table = _run_table(run_kind)
        updated = cas_status_update(
            transaction,
            run_table,
            tenant_id=tenant_id,
            row_id=run_id,
            transition=dataset_run_failed_transition(run_kind),
            values={"error": dict(error), "completed_at": completed_at},
        )
        return updated

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
                error=dict(record.error) if record.error is not None else None,
                created_at=record.created_at,
                completed_at=record.completed_at,
            )
        )

    def sync_run_by_id(self, *, transaction: Any, tenant_id: str, sync_run_id: str) -> SyncRunRow | None:
        row = (
            transaction.execute(
                select(db.sync_runs).where(
                    and_(
                        db.sync_runs.c.tenant_id == tenant_id,
                        db.sync_runs.c.id == sync_run_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(SyncRunRow, dict(row)) if row else None

    def sync_run_by_transaction_id(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        transaction_id: str,
    ) -> SyncRunRow | None:
        row = (
            transaction.execute(
                select(db.sync_runs).where(
                    and_(
                        db.sync_runs.c.tenant_id == tenant_id,
                        db.sync_runs.c.transaction_id == transaction_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(SyncRunRow, dict(row)) if row else None

    def insert_dead_letter_record(self, *, transaction: Any, record: DeadLetterRecord) -> bool:
        savepoint = transaction.begin_nested()
        try:
            transaction.execute(
                insert(db.dead_letter_records).values(
                    id=record.dead_letter_record_id,
                    tenant_id=record.tenant_id,
                    source_event_id=record.source_event_id,
                    source_dataset_version_id=record.source_dataset_version_id,
                    source_run_id=record.source_run_id,
                    payload=dict(record.payload),
                    payload_hash=record.payload_hash,
                    schema_version=record.schema_version,
                    transform_version=record.transform_version,
                    error_kind=record.error_kind,
                    error_message=record.error_message,
                    event_time=record.event_time,
                    ingested_at=record.ingested_at,
                    first_failed_at=record.first_failed_at,
                    attempts=record.attempts,
                    status=record.status,
                    replay_status=record.replay_status,
                    replay_run_id=record.replay_run_id,
                    replay_idempotency_key=record.replay_idempotency_key,
                    replay_requested_at=record.replay_requested_at,
                    replay_lease_token=record.replay_lease_token,
                    replay_started_at=record.replay_started_at,
                    replay_lease_expires_at=record.replay_lease_expires_at,
                    discarded_at=record.discarded_at,
                    backfill_plan=dict(record.backfill_plan) if record.backfill_plan is not None else None,
                    is_closed_partition_affected=record.is_closed_partition_affected,
                    metadata=dict(record.metadata),
                )
            )
            savepoint.commit()
            return True
        except IntegrityError:
            savepoint.rollback()
            return False

    def list_dead_letter_records(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        status: DeadLetterRecordStatus | None = None,
    ) -> list[DeadLetterRecordRow]:
        conditions = [db.dead_letter_records.c.tenant_id == tenant_id]
        if status is not None:
            conditions.append(db.dead_letter_records.c.status == status)
        rows = (
            transaction.execute(
                select(db.dead_letter_records)
                .where(and_(*conditions))
                .order_by(db.dead_letter_records.c.first_failed_at, db.dead_letter_records.c.id)
            )
            .mappings()
            .all()
        )
        return [cast(DeadLetterRecordRow, dict(row)) for row in rows]

    def dead_letter_record_by_id(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        record_id: str,
    ) -> DeadLetterRecordRow | None:
        row = (
            transaction.execute(
                select(db.dead_letter_records).where(
                    and_(db.dead_letter_records.c.tenant_id == tenant_id, db.dead_letter_records.c.id == record_id)
                )
            )
            .mappings()
            .first()
        )
        return cast(DeadLetterRecordRow, dict(row)) if row else None

    def update_dead_letter_record_replay_requested(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        record_id: str,
        replay_run_id: str,
        replay_idempotency_key: str,
        replay_requested_at: str,
        metadata: DatasetTransactionMetadata,
        backfill_plan: DatasetTransactionMetadata,
    ) -> DeadLetterRecordRow | None:
        updated = cas_status_update(
            transaction,
            db.dead_letter_records,
            tenant_id=tenant_id,
            row_id=record_id,
            transition=DEAD_LETTER_REPLAY_REQUESTED,
            values={
                "replay_status": "REQUESTED",
                "replay_run_id": replay_run_id,
                "replay_idempotency_key": replay_idempotency_key,
                "replay_requested_at": replay_requested_at,
                "replay_lease_token": None,  # nosec B105 - column name reset, not a secret value
                "replay_started_at": None,
                "replay_lease_expires_at": None,
                "backfill_plan": dict(backfill_plan),
                "metadata": dict(metadata),
            },
        )
        if not updated:
            return None
        return self.dead_letter_record_by_id(transaction=transaction, tenant_id=tenant_id, record_id=record_id)

    def discard_dead_letter_record(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        record_id: str,
        discarded_at: str,
        metadata: DatasetTransactionMetadata,
    ) -> DeadLetterRecordRow | None:
        updated = cas_status_update(
            transaction,
            db.dead_letter_records,
            tenant_id=tenant_id,
            row_id=record_id,
            transition=DEAD_LETTER_DISCARDED,
            values={"replay_status": "DISCARDED", "discarded_at": discarded_at, "metadata": dict(metadata)},
        )
        if not updated:
            return None
        return self.dead_letter_record_by_id(transaction=transaction, tenant_id=tenant_id, record_id=record_id)

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
        metadata: DatasetTransactionMetadata,
    ) -> DeadLetterRecordRow | None:
        updated = cas_status_update(
            transaction,
            db.dead_letter_records,
            tenant_id=tenant_id,
            row_id=record_id,
            transition=DEAD_LETTER_REPLAY_RUNNING,
            values={
                "replay_status": "REPLAYING",
                "replay_lease_token": replay_lease_token,
                "replay_started_at": replay_started_at,
                "replay_lease_expires_at": replay_lease_expires_at,
                "metadata": dict(metadata),
            },
            conditions=(
                db.dead_letter_records.c.replay_run_id == replay_run_id,
                or_(
                    db.dead_letter_records.c.status == "REPLAY_REQUESTED",
                    db.dead_letter_records.c.replay_lease_expires_at < replay_started_at,
                ),
            ),
        )
        if not updated:
            return None
        return self.dead_letter_record_by_id(transaction=transaction, tenant_id=tenant_id, record_id=record_id)

    def update_dead_letter_record_replay_succeeded(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        record_id: str,
        replay_run_id: str,
        metadata: DatasetTransactionMetadata,
        replay_lease_token: str | None = None,
    ) -> DeadLetterRecordRow | None:
        conditions = _dead_letter_replay_conditions(replay_run_id, replay_lease_token)
        updated = cas_status_update(
            transaction,
            db.dead_letter_records,
            tenant_id=tenant_id,
            row_id=record_id,
            transition=DEAD_LETTER_REPLAY_SUCCEEDED,
            values={
                "replay_status": "SUCCEEDED",
                "replay_run_id": replay_run_id,
                "replay_lease_token": replay_lease_token,
                "metadata": dict(metadata),
            },
            conditions=conditions,
        )
        if not updated:
            return None
        return self.dead_letter_record_by_id(transaction=transaction, tenant_id=tenant_id, record_id=record_id)

    def update_dead_letter_record_replay_failed(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        record_id: str,
        replay_run_id: str,
        metadata: DatasetTransactionMetadata,
        replay_lease_token: str | None = None,
    ) -> DeadLetterRecordRow | None:
        conditions = _dead_letter_replay_conditions(replay_run_id, replay_lease_token)
        updated = cas_status_update(
            transaction,
            db.dead_letter_records,
            tenant_id=tenant_id,
            row_id=record_id,
            transition=DEAD_LETTER_REPLAY_FAILED,
            values={
                "replay_status": "FAILED",
                "replay_run_id": replay_run_id,
                "replay_lease_token": replay_lease_token,
                "attempts": db.dead_letter_records.c.attempts + 1,
                "metadata": dict(metadata),
            },
            conditions=conditions,
        )
        if not updated:
            return None
        return self.dead_letter_record_by_id(transaction=transaction, tenant_id=tenant_id, record_id=record_id)

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
        return cas_status_update(
            transaction,
            db.sync_runs,
            tenant_id=tenant_id,
            row_id=sync_run_id,
            transition=transition,
            values={"committed_version_id": committed_version_id, "completed_at": completed_at},
        )


def _dead_letter_replay_conditions(replay_run_id: str, replay_lease_token: str | None) -> tuple[Any, ...]:
    conditions: tuple[Any, ...] = (db.dead_letter_records.c.replay_run_id == replay_run_id,)
    if replay_lease_token is None:
        return (*conditions, db.dead_letter_records.c.replay_lease_token.is_(None))
    return (*conditions, db.dead_letter_records.c.replay_lease_token == replay_lease_token)


def _run_table(run_kind: DatasetRunKind) -> Any:
    if run_kind == "sync":
        return db.sync_runs
    if run_kind == "transform":
        return db.transform_runs
    return db.materialization_runs


def _webhook_event_matches(
    metadata: object,
    connector_name: str,
    resource_name: str,
    event_id: str,
) -> bool:
    if not isinstance(metadata, Mapping):
        return False
    event = metadata.get("webhookEvent")
    if not isinstance(event, Mapping):
        return False
    return (
        event.get("connectorName") == connector_name
        and event.get("resourceName") == resource_name
        and event.get("eventId") == event_id
    )


def _transaction_with_webhook_key_metadata(
    row: dict[str, Any],
    event_key: Mapping[str, object],
) -> dict[str, Any]:
    metadata = row.get("metadata")
    metadata_dict = dict(metadata) if isinstance(metadata, Mapping) else {}
    event = metadata_dict.get("webhookEvent")
    if isinstance(event, Mapping) and isinstance(event.get("payloadHash"), str):
        return row
    metadata_dict["webhookEvent"] = {
        "connectorName": event_key["connector_name"],
        "resourceName": event_key["resource_name"],
        "eventId": event_key["event_id"],
        "payloadHash": event_key["payload_hash"],
    }
    row["metadata"] = metadata_dict
    return row
