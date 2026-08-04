"""SQLAlchemy adapter for branch-isolated Action object overlays."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from foundry_lite.application.action_branch_types import (
    ActionBranchEditRecord,
    ActionBranchEditRow,
    ActionBranchObjectRow,
    ActionBranchObjectWrite,
)
from foundry_lite.infrastructure import schema as db


class SqlAlchemyActionBranchRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def object_overlay(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        branch_id: str,
        object_type_api_name: str,
        object_id: str,
    ) -> ActionBranchObjectRow | None:
        row = (
            transaction.execute(
                select(db.action_branch_objects).where(
                    and_(
                        db.action_branch_objects.c.tenant_id == tenant_id,
                        db.action_branch_objects.c.branch_id == branch_id,
                        db.action_branch_objects.c.object_type_api_name == object_type_api_name,
                        db.action_branch_objects.c.object_id == object_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(ActionBranchObjectRow, dict(row)) if row else None

    def store_object_overlay(
        self,
        *,
        transaction: Any,
        record: ActionBranchObjectWrite,
    ) -> ActionBranchObjectRow | None:
        if record.expected_overlay_version is None:
            inserted = transaction.execute(_insert_overlay(transaction, record)).scalar_one_or_none()
            return self._overlay_by_id(transaction, record.tenant_id, str(inserted)) if inserted else None
        result = transaction.execute(
            update(db.action_branch_objects)
            .where(
                and_(
                    db.action_branch_objects.c.tenant_id == record.tenant_id,
                    db.action_branch_objects.c.branch_id == record.branch_id,
                    db.action_branch_objects.c.object_type_api_name == record.object_type_api_name,
                    db.action_branch_objects.c.object_id == record.object_id,
                    db.action_branch_objects.c.overlay_version == record.expected_overlay_version,
                )
            )
            .values(
                overlay_version=record.overlay_version,
                properties=dict(record.properties),
                deleted=record.is_deleted,
                last_action_run_id=record.action_run_id,
                updated_at=record.updated_at,
            )
        )
        if not result.rowcount:
            return None
        return self.object_overlay(
            transaction=transaction,
            tenant_id=record.tenant_id,
            branch_id=record.branch_id,
            object_type_api_name=record.object_type_api_name,
            object_id=record.object_id,
        )

    def insert_edit(self, *, transaction: Any, record: ActionBranchEditRecord) -> ActionBranchEditRow | None:
        inserted = transaction.execute(_insert_edit(transaction, record)).scalar_one_or_none()
        if inserted is None:
            return None
        row = (
            transaction.execute(
                select(db.action_branch_edits).where(
                    and_(
                        db.action_branch_edits.c.tenant_id == record.tenant_id, db.action_branch_edits.c.id == inserted
                    )
                )
            )
            .mappings()
            .one()
        )
        return cast(ActionBranchEditRow, dict(row))

    def list_object_overlays(self, *, transaction: Any, tenant_id: str, branch_id: str) -> list[ActionBranchObjectRow]:
        rows = (
            transaction.execute(
                select(db.action_branch_objects)
                .where(
                    and_(
                        db.action_branch_objects.c.tenant_id == tenant_id,
                        db.action_branch_objects.c.branch_id == branch_id,
                    )
                )
                .order_by(db.action_branch_objects.c.object_type_api_name, db.action_branch_objects.c.object_id)
            )
            .mappings()
            .all()
        )
        return [cast(ActionBranchObjectRow, dict(row)) for row in rows]

    def list_edits(self, *, transaction: Any, tenant_id: str, branch_id: str) -> list[ActionBranchEditRow]:
        rows = (
            transaction.execute(
                select(db.action_branch_edits)
                .where(
                    and_(
                        db.action_branch_edits.c.tenant_id == tenant_id, db.action_branch_edits.c.branch_id == branch_id
                    )
                )
                .order_by(
                    db.action_branch_edits.c.created_at,
                    db.action_branch_edits.c.action_run_id,
                    db.action_branch_edits.c.ordinal,
                )
            )
            .mappings()
            .all()
        )
        return [cast(ActionBranchEditRow, dict(row)) for row in rows]

    def _overlay_by_id(self, transaction: Any, tenant_id: str, overlay_id: str) -> ActionBranchObjectRow:
        row = (
            transaction.execute(
                select(db.action_branch_objects).where(
                    and_(db.action_branch_objects.c.tenant_id == tenant_id, db.action_branch_objects.c.id == overlay_id)
                )
            )
            .mappings()
            .one()
        )
        return cast(ActionBranchObjectRow, dict(row))


def _overlay_values(record: ActionBranchObjectWrite) -> dict[str, object]:
    return {
        "id": record.overlay_id,
        "tenant_id": record.tenant_id,
        "branch_id": record.branch_id,
        "object_type_id": record.object_type_id,
        "object_type_api_name": record.object_type_api_name,
        "object_id": record.object_id,
        "base_object_version": record.base_object_version,
        "overlay_version": record.overlay_version,
        "properties": dict(record.properties),
        "deleted": record.is_deleted,
        "last_action_run_id": record.action_run_id,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _insert_overlay(transaction: Any, record: ActionBranchObjectWrite) -> Any:
    values = _overlay_values(record)
    columns = ["tenant_id", "branch_id", "object_type_api_name", "object_id"]
    if transaction.dialect.name == "postgresql":
        return (
            postgres_insert(db.action_branch_objects)
            .values(**values)
            .on_conflict_do_nothing(index_elements=columns)
            .returning(db.action_branch_objects.c.id)
        )
    if transaction.dialect.name == "sqlite":
        return (
            sqlite_insert(db.action_branch_objects)
            .values(**values)
            .on_conflict_do_nothing(index_elements=columns)
            .returning(db.action_branch_objects.c.id)
        )
    return insert(db.action_branch_objects).values(**values).returning(db.action_branch_objects.c.id)


def _insert_edit(transaction: Any, record: ActionBranchEditRecord) -> Any:
    values = {
        "id": record.edit_id,
        "tenant_id": record.tenant_id,
        "branch_id": record.branch_id,
        "action_run_id": record.action_run_id,
        "operation_key": record.operation_key,
        "ordinal": record.ordinal,
        "edit_kind": record.edit_kind,
        "object_type_id": record.object_type_id,
        "object_type_api_name": record.object_type_api_name,
        "object_id": record.object_id,
        "before": dict(record.before),
        "after": dict(record.after),
        "created_at": record.created_at,
    }
    columns = ["tenant_id", "branch_id", "operation_key"]
    if transaction.dialect.name == "postgresql":
        return (
            postgres_insert(db.action_branch_edits)
            .values(**values)
            .on_conflict_do_nothing(index_elements=columns)
            .returning(db.action_branch_edits.c.id)
        )
    if transaction.dialect.name == "sqlite":
        return (
            sqlite_insert(db.action_branch_edits)
            .values(**values)
            .on_conflict_do_nothing(index_elements=columns)
            .returning(db.action_branch_edits.c.id)
        )
    return insert(db.action_branch_edits).values(**values).returning(db.action_branch_edits.c.id)
