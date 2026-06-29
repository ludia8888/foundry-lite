from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import and_, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from foundry_lite.application.ports.action_repository import (
    ActionRunRecord,
    ActionRunRow,
    ActionWritebackReconciliation,
    ActionWritebackRecord,
    ObjectEditRecord,
    ObjectTargetUpdate,
)
from foundry_lite.application.ports.transaction_context import ACTION_WRITEBACK_RECONCILED, StatusTransition
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.object_change_sequence import next_object_change_sequence
from foundry_lite.infrastructure.repositories.status_cas import cas_status_update


class SqlAlchemyActionRepository:
    """SQLAlchemy implementation of action runtime persistence."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def action_run_by_idempotency(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        action_type_id: str,
        actor_user_id: str,
        idempotency_key: str,
    ) -> ActionRunRow | None:
        row = (
            transaction.execute(
                select(db.action_runs).where(
                    and_(
                        db.action_runs.c.tenant_id == tenant_id,
                        db.action_runs.c.action_type_id == action_type_id,
                        db.action_runs.c.actor_user_id == actor_user_id,
                        db.action_runs.c.idempotency_key == idempotency_key,
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(ActionRunRow, dict(row)) if row else None

    def action_run_by_id(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        action_run_id: str,
    ) -> ActionRunRow | None:
        row = (
            transaction.execute(
                select(db.action_runs).where(
                    and_(db.action_runs.c.tenant_id == tenant_id, db.action_runs.c.id == action_run_id)
                )
            )
            .mappings()
            .first()
        )
        return cast(ActionRunRow, dict(row)) if row else None

    def insert_action_run(self, *, transaction: Any, record: ActionRunRecord) -> None:
        transaction.execute(
            insert(db.action_runs).values(
                id=record.action_run_id,
                tenant_id=record.tenant_id,
                action_type_id=record.action_type_id,
                action_type_api_name=record.action_type_api_name,
                actor_user_id=record.actor_user_id,
                target_object_type_id=record.target_object_type_id,
                target_object_type_api_name=record.target_object_type_api_name,
                target_object_id=record.target_object_id,
                expected_object_version=record.expected_object_version,
                parameters=dict(record.parameters),
                status=record.status,
                idempotency_key=record.idempotency_key,
                request_fingerprint=record.request_fingerprint,
                result=dict(record.result) if record.result is not None else None,
                error=dict(record.error) if record.error is not None else None,
                created_at=record.created_at,
                completed_at=record.completed_at,
            )
        )

    def insert_action_run_or_get_existing(self, *, transaction: Any, record: ActionRunRecord) -> ActionRunRow | None:
        inserted_id = transaction.execute(_action_run_insert_or_ignore(transaction, record)).scalar_one_or_none()
        if inserted_id == record.action_run_id:
            return None
        existing = self.action_run_by_idempotency(
            transaction=transaction,
            tenant_id=record.tenant_id,
            action_type_id=record.action_type_id,
            actor_user_id=record.actor_user_id,
            idempotency_key=record.idempotency_key,
        )
        if existing is None:
            raise RuntimeError("action idempotency insert had no persisted winner")
        return existing

    def update_action_run_terminal(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        action_run_id: str,
        transition: StatusTransition,
        error: Mapping[str, object] | None,
        completed_at: str,
        result: Mapping[str, object] | None = None,
    ) -> bool:
        return cas_status_update(
            transaction,
            db.action_runs,
            tenant_id=tenant_id,
            row_id=action_run_id,
            transition=transition,
            values={
                "error": dict(error) if error is not None else None,
                "result": dict(result) if result is not None else None,
                "completed_at": completed_at,
            },
        )

    def insert_action_writeback(self, *, transaction: Any, record: ActionWritebackRecord) -> None:
        transaction.execute(
            insert(db.action_writebacks).values(
                id=record.writeback_id,
                tenant_id=record.tenant_id,
                action_run_id=record.action_run_id,
                mode=record.mode,
                connector_id=record.connector_id,
                request=dict(record.request),
                response=dict(record.response) if record.response is not None else None,
                status=record.status,
                idempotency_key=record.idempotency_key,
                attempts=record.attempts,
                created_at=record.created_at,
                completed_at=record.completed_at,
            )
        )

    def action_writeback_by_id(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        writeback_id: str,
    ) -> ActionWritebackRecord | None:
        row = (
            transaction.execute(
                select(db.action_writebacks).where(
                    and_(db.action_writebacks.c.tenant_id == tenant_id, db.action_writebacks.c.id == writeback_id)
                )
            )
            .mappings()
            .first()
        )
        return _writeback_record_from_row(dict(row)) if row else None

    def list_action_writebacks(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        statuses: Sequence[str],
        limit: int,
    ) -> list[ActionWritebackRecord]:
        rows = (
            transaction.execute(
                select(db.action_writebacks)
                .where(
                    and_(
                        db.action_writebacks.c.tenant_id == tenant_id,
                        db.action_writebacks.c.status.in_(tuple(statuses)),
                    )
                )
                .order_by(db.action_writebacks.c.created_at.desc(), db.action_writebacks.c.id.desc())
                .limit(limit)
            )
            .mappings()
            .all()
        )
        return [_writeback_record_from_row(dict(row)) for row in rows]

    def reconcile_action_writeback(self, *, transaction: Any, record: ActionWritebackReconciliation) -> bool:
        return cas_status_update(
            transaction,
            db.action_writebacks,
            tenant_id=record.tenant_id,
            row_id=record.writeback_id,
            transition=ACTION_WRITEBACK_RECONCILED,
            values={"response": dict(record.response), "completed_at": record.completed_at},
            conditions=(db.action_writebacks.c.action_run_id == record.action_run_id,),
        )

    def update_object_target(self, *, transaction: Any, record: ObjectTargetUpdate) -> bool:
        object_change_sequence = next_object_change_sequence(transaction, record.tenant_id)
        result = transaction.execute(
            update(db.object_records)
            .where(
                and_(
                    db.object_records.c.tenant_id == record.tenant_id,
                    db.object_records.c.id == record.object_record_id,
                    db.object_records.c.object_version == record.expected_object_version,
                )
            )
            .values(
                edit_properties=dict(record.edit_properties),
                properties=dict(record.properties),
                object_version=record.next_object_version,
                object_change_sequence=object_change_sequence,
                updated_at=record.updated_at,
            )
        )
        updated = result.rowcount == 1
        if updated:
            _insert_object_record_version_from_current(transaction, record.tenant_id, record.object_record_id)
        return updated

    def insert_object_edit(self, *, transaction: Any, record: ObjectEditRecord) -> None:
        transaction.execute(
            insert(db.object_edits).values(
                id=record.edit_id,
                tenant_id=record.tenant_id,
                action_run_id=record.action_run_id,
                object_type_id=record.object_type_id,
                object_type_api_name=record.object_type_api_name,
                object_id=record.object_id,
                edit_type=record.edit_type,
                patch=dict(record.patch),
                previous_values=dict(record.previous_values),
                actor_user_id=record.actor_user_id,
                idempotency_key=record.idempotency_key,
                created_at=record.created_at,
            )
        )


def _action_run_values(record: ActionRunRecord) -> dict[str, object]:
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
        "parameters": dict(record.parameters),
        "status": record.status,
        "idempotency_key": record.idempotency_key,
        "request_fingerprint": record.request_fingerprint,
        "result": dict(record.result) if record.result is not None else None,
        "error": dict(record.error) if record.error is not None else None,
        "created_at": record.created_at,
        "completed_at": record.completed_at,
    }


def _writeback_record_from_row(row: dict[str, object]) -> ActionWritebackRecord:
    request = row.get("request")
    response = row.get("response")
    return ActionWritebackRecord(
        writeback_id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        action_run_id=str(row["action_run_id"]),
        mode=str(row["mode"]),
        connector_id=str(row["connector_id"]),
        request=cast(Mapping[str, object], request),
        response=cast(Mapping[str, object], response) if response is not None else None,
        status=str(row["status"]),
        idempotency_key=str(row["idempotency_key"]),
        attempts=cast(int, row["attempts"]),
        created_at=str(row["created_at"]),
        completed_at=cast(str | None, row["completed_at"]),
    )


def _action_run_insert_or_ignore(transaction: Any, record: ActionRunRecord) -> Any:
    values = _action_run_values(record)
    conflict_columns = ["tenant_id", "action_type_id", "actor_user_id", "idempotency_key"]
    if transaction.dialect.name == "postgresql":
        return (
            postgres_insert(db.action_runs)
            .values(**values)
            .on_conflict_do_nothing(index_elements=conflict_columns)
            .returning(db.action_runs.c.id)
        )
    if transaction.dialect.name == "sqlite":
        return (
            sqlite_insert(db.action_runs)
            .values(**values)
            .on_conflict_do_nothing(index_elements=conflict_columns)
            .returning(db.action_runs.c.id)
        )
    return insert(db.action_runs).values(**values).returning(db.action_runs.c.id)


def _insert_object_record_version_from_current(transaction: Any, tenant_id: str, record_id: str) -> None:
    row = (
        transaction.execute(
            select(db.object_records).where(
                and_(db.object_records.c.tenant_id == tenant_id, db.object_records.c.id == record_id)
            )
        )
        .mappings()
        .one()
    )
    transaction.execute(
        insert(db.object_record_versions).values(
            id=f"object_record_version_{uuid4().hex}",
            tenant_id=row["tenant_id"],
            object_record_id=row["id"],
            object_type_id=row["object_type_id"],
            object_type_api_name=row["object_type_api_name"],
            object_id=row["object_id"],
            index_version=row["index_version"],
            is_active=row["is_active"],
            properties=row["properties"],
            base_properties=row["base_properties"],
            edit_properties=row["edit_properties"],
            property_versions=row["property_versions"],
            source_dataset_version_id=row["source_dataset_version_id"],
            source_hash=row["source_hash"],
            object_version=row["object_version"],
            object_change_sequence=row["object_change_sequence"],
            deleted=row["deleted"],
            deletion_reason=row["deletion_reason"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
    )
