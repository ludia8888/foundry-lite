"""SQL rows for normalized Action logs and revert state checks."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import and_, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from foundry_lite.application.action_log_types import (
    ActionLogEntryRecord,
    ActionLogEntryRow,
    ActionLogObjectRecord,
    ActionLogObjectRow,
)
from foundry_lite.application.ports.action_repository import ActionRunRow
from foundry_lite.application.ports.object_read_repository import ObjectLinkRow, ObjectRecordRow
from foundry_lite.infrastructure import schema as db


def insert_action_log_rows(
    transaction: Any,
    entry: ActionLogEntryRecord,
    objects: Sequence[ActionLogObjectRecord],
) -> ActionLogEntryRow | None:
    """Insert one log once, followed by its deterministic edited-object links."""
    inserted = transaction.execute(_entry_insert(transaction, entry)).scalar_one_or_none()
    if inserted is None:
        return None
    for item in objects:
        transaction.execute(insert(db.action_log_objects).values(tenant_id=item.tenant_id, **_object_values(item)))
    return required_action_log(transaction, entry.tenant_id, entry.action_run_id)


def action_log_by_run(transaction: Any, tenant_id: str, action_run_id: str) -> ActionLogEntryRow | None:
    """Load one tenant-scoped Action log by its source run."""
    row = transaction.execute(_log_select(tenant_id).where(db.action_log_entries.c.action_run_id == action_run_id))
    mapped = row.mappings().first()
    return cast(ActionLogEntryRow, dict(mapped)) if mapped else None


def required_action_log(transaction: Any, tenant_id: str, action_run_id: str) -> ActionLogEntryRow:
    """Load the Action log inserted in the current transaction."""
    row = transaction.execute(_log_select(tenant_id).where(db.action_log_entries.c.action_run_id == action_run_id))
    return cast(ActionLogEntryRow, dict(row.mappings().one()))


def action_log_object_rows(transaction: Any, tenant_id: str, action_log_entry_id: str) -> list[ActionLogObjectRow]:
    """Load object and link edits in their original application order."""
    rows = (
        transaction.execute(
            select(db.action_log_objects)
            .where(
                and_(
                    db.action_log_objects.c.tenant_id == tenant_id,
                    db.action_log_objects.c.action_log_entry_id == action_log_entry_id,
                )
            )
            .order_by(db.action_log_objects.c.ordinal.asc())
        )
        .mappings()
        .all()
    )
    return [cast(ActionLogObjectRow, dict(row)) for row in rows]


def list_action_log_rows(
    transaction: Any,
    tenant_id: str,
    before_created_at: str | None,
    before_log_id: str | None,
    limit: int,
) -> list[ActionLogEntryRow]:
    """Load one newest-first cursor page."""
    statement = _log_select(tenant_id)
    if before_created_at is not None and before_log_id is not None:
        statement = statement.where(
            or_(
                db.action_log_entries.c.created_at < before_created_at,
                and_(
                    db.action_log_entries.c.created_at == before_created_at,
                    db.action_log_entries.c.id < before_log_id,
                ),
            )
        )
    rows = transaction.execute(
        statement.order_by(db.action_log_entries.c.created_at.desc(), db.action_log_entries.c.id.desc()).limit(limit)
    )
    return [cast(ActionLogEntryRow, dict(row)) for row in rows.mappings().all()]


def action_runs_for_monitoring_rows(transaction: Any, tenant_id: str, limit: int) -> list[ActionRunRow]:
    """Load a bounded newest-first run window without mixing tenants."""
    rows = transaction.execute(
        select(db.action_runs)
        .where(db.action_runs.c.tenant_id == tenant_id)
        .order_by(db.action_runs.c.created_at.desc(), db.action_runs.c.id.desc())
        .limit(limit)
    )
    return [cast(ActionRunRow, dict(row)) for row in rows.mappings().all()]


def mark_log_reverted(transaction: Any, tenant_id: str, action_run_id: str, reverted_by_run_id: str) -> bool:
    """Record the single winning revert run with compare-and-set semantics."""
    result = transaction.execute(
        update(db.action_log_entries)
        .where(
            and_(
                db.action_log_entries.c.tenant_id == tenant_id,
                db.action_log_entries.c.action_run_id == action_run_id,
                db.action_log_entries.c.revert_status == "eligible",
                db.action_log_entries.c.reverted_by_run_id.is_(None),
            )
        )
        .values(revert_status="reverted", reverted_by_run_id=reverted_by_run_id)
    )
    return result.rowcount == 1


def object_target_for_revert_row(
    transaction: Any, tenant_id: str, object_type_id: str, object_id: str
) -> ObjectRecordRow | None:
    """Load the active-index object row without hiding soft-deleted state."""
    row = transaction.execute(
        select(db.object_records).where(
            and_(
                db.object_records.c.tenant_id == tenant_id,
                db.object_records.c.object_type_id == object_type_id,
                db.object_records.c.object_id == object_id,
                db.object_records.c.index_version == "active",
            )
        )
    )
    mapped = row.mappings().first()
    return cast(ObjectRecordRow, dict(mapped)) if mapped else None


def object_link_for_revert_row(
    transaction: Any,
    tenant_id: str,
    link_type_id: str,
    from_object_id: str,
    to_object_id: str,
) -> ObjectLinkRow | None:
    """Load the active-index link row including its deleted state and version."""
    row = transaction.execute(
        select(db.object_links).where(
            and_(
                db.object_links.c.tenant_id == tenant_id,
                db.object_links.c.link_type_id == link_type_id,
                db.object_links.c.from_object_id == from_object_id,
                db.object_links.c.to_object_id == to_object_id,
                db.object_links.c.index_version == "active",
            )
        )
    )
    mapped = row.mappings().first()
    return cast(ObjectLinkRow, dict(mapped)) if mapped else None


def _log_select(tenant_id: str) -> Any:
    return select(db.action_log_entries).where(db.action_log_entries.c.tenant_id == tenant_id)


def _entry_insert(transaction: Any, entry: ActionLogEntryRecord) -> Any:
    values = _entry_values(entry)
    dialect = transaction.dialect.name
    if dialect == "postgresql":
        statement: Any = postgres_insert(db.action_log_entries).values(**values)
        return statement.on_conflict_do_nothing().returning(db.action_log_entries.c.id)
    if dialect == "sqlite":
        statement = sqlite_insert(db.action_log_entries).values(**values)
        return statement.on_conflict_do_nothing().returning(db.action_log_entries.c.id)
    return insert(db.action_log_entries).values(**values).returning(db.action_log_entries.c.id)


def _entry_values(entry: ActionLogEntryRecord) -> dict[str, object]:
    return {
        "id": entry.log_entry_id,
        "tenant_id": entry.tenant_id,
        "action_run_id": entry.action_run_id,
        "log_object_type_api_name": entry.log_object_type_api_name,
        "log_object_id": entry.log_object_id,
        "action_type_id": entry.action_type_id,
        "action_type_api_name": entry.action_type_api_name,
        "definition_version": entry.definition_version,
        "actor_user_id": entry.actor_user_id,
        "status": entry.status,
        "parameters": dict(entry.parameters),
        "result": dict(entry.result),
        "branch_id": entry.branch_id,
        "plan_hash": entry.plan_hash,
        "approval_id": entry.approval_id,
        "revert_allowed": entry.revert_allowed,
        "revert_status": "eligible" if entry.revert_allowed else "not_allowed",
        "reverted_by_run_id": None,
        "created_at": entry.created_at,
        "completed_at": entry.completed_at,
    }


def _object_values(item: ActionLogObjectRecord) -> dict[str, object]:
    return {
        "id": item.log_object_link_id,
        "action_log_entry_id": item.action_log_entry_id,
        "object_edit_id": item.object_edit_id,
        "object_type_id": item.object_type_id,
        "object_type_api_name": item.object_type_api_name,
        "object_id": item.object_id,
        "edit_type": item.edit_type,
        "ordinal": item.ordinal,
    }
