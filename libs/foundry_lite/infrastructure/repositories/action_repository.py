"""SQLAlchemy repository adapter for action repository persistence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from sqlalchemy import and_, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from foundry_lite.application.action_log_types import (
    ActionLogEntryRecord,
    ActionLogEntryRow,
    ActionLogObjectRecord,
    ActionLogObjectRow,
    ObjectRestoreWrite,
)
from foundry_lite.application.ports.action_repository import (
    ActionRunRecord,
    ActionRunRow,
    ActionRunUsageRow,
    ActionWritebackReconciliation,
    ActionWritebackRecord,
    ObjectCreateWrite,
    ObjectDeleteWrite,
    ObjectEditRecord,
    ObjectEditRow,
    ObjectLinkDeleteWrite,
    ObjectLinkWrite,
    ObjectTargetUpdate,
)
from foundry_lite.application.ports.object_read_repository import ObjectLinkRow, ObjectRecordRow
from foundry_lite.application.ports.transaction_context import ACTION_WRITEBACK_RECONCILED, StatusTransition
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.action_log_rows import (
    action_log_by_run,
    action_log_object_rows,
    action_runs_for_monitoring_rows,
    insert_action_log_rows,
    list_action_log_rows,
    mark_log_reverted,
    object_link_for_revert_row,
    object_target_for_revert_row,
)
from foundry_lite.infrastructure.repositories.object_change_sequence import next_object_change_sequence
from foundry_lite.infrastructure.repositories.object_write_ops import (
    create_object_link_write,
    create_object_record_write,
    restore_object_write,
    snapshot_object_record_version,
    soft_delete_object_link_write,
    soft_delete_object_write,
)
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
                external_writeback_uri=record.external_writeback_uri,
                created_at=record.created_at,
                completed_at=record.completed_at,
            )
        )

    def list_action_runs_by_status(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        statuses: Sequence[str],
        limit: int,
    ) -> list[ActionRunRow]:
        rows = (
            transaction.execute(
                select(db.action_runs)
                .where(
                    and_(
                        db.action_runs.c.tenant_id == tenant_id,
                        db.action_runs.c.status.in_(tuple(statuses)),
                    )
                )
                .order_by(db.action_runs.c.created_at.asc(), db.action_runs.c.id.asc())
                .limit(limit)
            )
            .mappings()
            .all()
        )
        return [cast(ActionRunRow, dict(row)) for row in rows]

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
        completed_at: str | None,
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

    def action_run_usage(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        since: str,
        action_type_api_name: str | None = None,
        target_object_type_api_name: str | None = None,
    ) -> ActionRunUsageRow:
        conditions = [db.action_runs.c.tenant_id == tenant_id, db.action_runs.c.created_at >= since]
        if action_type_api_name is not None:
            conditions.append(db.action_runs.c.action_type_api_name == action_type_api_name)
        if target_object_type_api_name is not None:
            conditions.append(db.action_runs.c.target_object_type_api_name == target_object_type_api_name)
        status_rows = transaction.execute(
            select(db.action_runs.c.status, func.count()).where(and_(*conditions)).group_by(db.action_runs.c.status)
        ).all()
        actor_count, last_run_at = transaction.execute(
            select(
                func.count(func.distinct(db.action_runs.c.actor_user_id)),
                func.max(db.action_runs.c.created_at),
            ).where(and_(*conditions))
        ).one()
        status_counts = {str(status): int(count) for status, count in status_rows}
        return {
            "status_counts": status_counts,
            "total_runs": sum(status_counts.values()),
            "distinct_actor_count": int(actor_count or 0),
            "last_run_at": cast(str | None, last_run_at),
        }

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
            snapshot_object_record_version(transaction, record.tenant_id, record.object_record_id)
        return updated

    def create_object_record(self, *, transaction: Any, record: ObjectCreateWrite) -> bool:
        return create_object_record_write(transaction, record)

    def soft_delete_object_target(self, *, transaction: Any, record: ObjectDeleteWrite) -> bool:
        return soft_delete_object_write(transaction, record)

    def create_object_link(self, *, transaction: Any, record: ObjectLinkWrite) -> None:
        create_object_link_write(transaction, record)

    def soft_delete_object_link(self, *, transaction: Any, record: ObjectLinkDeleteWrite) -> bool:
        return soft_delete_object_link_write(transaction, record)

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
                revert_payload=dict(record.revert_payload) if record.revert_payload is not None else None,
                actor_user_id=record.actor_user_id,
                idempotency_key=record.idempotency_key,
                created_at=record.created_at,
            )
        )

    def object_edits_for_run(self, *, transaction: Any, tenant_id: str, action_run_id: str) -> list[ObjectEditRow]:
        rows = (
            transaction.execute(
                select(db.object_edits)
                .where(
                    and_(
                        db.object_edits.c.tenant_id == tenant_id,
                        db.object_edits.c.action_run_id == action_run_id,
                    )
                )
                .order_by(db.object_edits.c.created_at.asc(), db.object_edits.c.id.asc())
            )
            .mappings()
            .all()
        )
        return [cast(ObjectEditRow, dict(row)) for row in rows]

    def latest_object_edit(
        self, *, transaction: Any, tenant_id: str, object_type_id: str, object_id: str
    ) -> ObjectEditRow | None:
        row = (
            transaction.execute(
                select(db.object_edits)
                .where(
                    and_(
                        db.object_edits.c.tenant_id == tenant_id,
                        db.object_edits.c.object_type_id == object_type_id,
                        db.object_edits.c.object_id == object_id,
                    )
                )
                .order_by(db.object_edits.c.created_at.desc(), db.object_edits.c.id.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        return cast(ObjectEditRow, dict(row)) if row else None

    def insert_action_log(
        self, *, transaction: Any, entry: ActionLogEntryRecord, objects: Sequence[ActionLogObjectRecord]
    ) -> ActionLogEntryRow | None:
        return insert_action_log_rows(transaction, entry, objects)

    def action_log_by_run_id(self, *, transaction: Any, tenant_id: str, action_run_id: str) -> ActionLogEntryRow | None:
        return action_log_by_run(transaction, tenant_id, action_run_id)

    def action_log_objects(
        self, *, transaction: Any, tenant_id: str, action_log_entry_id: str
    ) -> list[ActionLogObjectRow]:
        return action_log_object_rows(transaction, tenant_id, action_log_entry_id)

    def list_action_logs(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        before_created_at: str | None,
        before_log_id: str | None,
        limit: int,
    ) -> list[ActionLogEntryRow]:
        return list_action_log_rows(transaction, tenant_id, before_created_at, before_log_id, limit)

    def action_runs_for_monitoring(self, *, transaction: Any, tenant_id: str, limit: int) -> list[ActionRunRow]:
        return action_runs_for_monitoring_rows(transaction, tenant_id, limit)

    def mark_action_log_reverted(
        self, *, transaction: Any, tenant_id: str, action_run_id: str, reverted_by_run_id: str
    ) -> bool:
        return mark_log_reverted(transaction, tenant_id, action_run_id, reverted_by_run_id)

    def object_target_for_revert(
        self, *, transaction: Any, tenant_id: str, object_type_id: str, object_id: str
    ) -> ObjectRecordRow | None:
        return object_target_for_revert_row(transaction, tenant_id, object_type_id, object_id)

    def object_link_for_revert(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        link_type_id: str,
        from_object_id: str,
        to_object_id: str,
    ) -> ObjectLinkRow | None:
        return object_link_for_revert_row(transaction, tenant_id, link_type_id, from_object_id, to_object_id)

    def restore_object_target(self, *, transaction: Any, record: ObjectRestoreWrite) -> bool:
        return restore_object_write(transaction, record)


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
        "external_writeback_uri": record.external_writeback_uri,
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
