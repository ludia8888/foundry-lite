from __future__ import annotations

from typing import Any

from sqlalchemy import and_, insert, select, update
from sqlalchemy.engine import Engine

from foundry_lite.application.ports.action_repository import (
    ActionRunRecord,
    ActionWritebackRecord,
    ObjectEditRecord,
    ObjectTargetUpdate,
)
from foundry_lite.infrastructure import schema as db


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
    ) -> dict[str, Any] | None:
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
        return dict(row) if row else None

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
                parameters=record.parameters,
                status=record.status,
                idempotency_key=record.idempotency_key,
                error=record.error,
                created_at=record.created_at,
                completed_at=record.completed_at,
            )
        )

    def update_action_run_terminal(
        self,
        *,
        transaction: Any,
        action_run_id: str,
        status: str,
        error: dict[str, Any] | None,
        completed_at: str,
    ) -> None:
        transaction.execute(
            update(db.action_runs)
            .where(db.action_runs.c.id == action_run_id)
            .values(status=status, error=error, completed_at=completed_at)
        )

    def insert_action_writeback(self, *, transaction: Any, record: ActionWritebackRecord) -> None:
        transaction.execute(
            insert(db.action_writebacks).values(
                id=record.writeback_id,
                tenant_id=record.tenant_id,
                action_run_id=record.action_run_id,
                mode=record.mode,
                connector_id=record.connector_id,
                request=record.request,
                response=record.response,
                status=record.status,
                idempotency_key=record.idempotency_key,
                attempts=record.attempts,
                created_at=record.created_at,
                completed_at=record.completed_at,
            )
        )

    def update_object_target(self, *, transaction: Any, record: ObjectTargetUpdate) -> bool:
        result = transaction.execute(
            update(db.object_records)
            .where(
                and_(
                    db.object_records.c.id == record.object_record_id,
                    db.object_records.c.object_version == record.expected_object_version,
                )
            )
            .values(
                edit_properties=record.edit_properties,
                properties=record.properties,
                object_version=record.next_object_version,
                updated_at=record.updated_at,
            )
        )
        return result.rowcount == 1

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
                patch=record.patch,
                previous_values=record.previous_values,
                actor_user_id=record.actor_user_id,
                idempotency_key=record.idempotency_key,
                created_at=record.created_at,
            )
        )
