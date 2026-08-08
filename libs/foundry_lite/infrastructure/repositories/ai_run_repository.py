"""SQLAlchemy repository adapter for ai run repository persistence."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from foundry_lite.application.ports.ai_run_repository import (
    AiCitationRecord,
    AiContextItemRecord,
    AiExecutionEventRecord,
    AiExecutionRunRecord,
    AiJsonObject,
    AiLedgerRow,
    AiMessageRecord,
    AiModelCallRecord,
    AiPromptArtifactRecord,
    AiRunLedgerSnapshot,
    AiSessionRecord,
    AiSessionStateVersionRecord,
    AiToolCallRecord,
    AiUsageLedgerRecord,
)
from foundry_lite.application.ports.transaction_context import StatusTransition
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.status_cas import cas_status_update


class SqlAlchemyAiRunRepository:
    """SQLAlchemy implementation of the canonical AI run/event ledger."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_session(self, *, transaction: Any, record: AiSessionRecord) -> None:
        savepoint = transaction.begin_nested()
        try:
            transaction.execute(insert(db.ai_sessions).values({"tenant_id": record.tenant_id, **record.__dict__}))
        except IntegrityError:
            savepoint.rollback()
            existing = self.session_by_id(
                transaction=transaction,
                tenant_id=record.tenant_id,
                session_id=record.id,
            )
            if existing is None or not _same_session_owner(existing, record):
                raise
            transaction.execute(
                update(db.ai_sessions)
                .where(and_(db.ai_sessions.c.tenant_id == record.tenant_id, db.ai_sessions.c.id == record.id))
                .values(last_activity_at=record.last_activity_at)
            )
            return
        savepoint.commit()

    def create_session_state_version(self, *, transaction: Any, record: AiSessionStateVersionRecord) -> None:
        transaction.execute(
            insert(db.ai_session_state_versions).values(
                {"tenant_id": record.tenant_id, **_state_version_values(record)}
            )
        )

    def insert_message_or_get_existing(self, *, transaction: Any, record: AiMessageRecord) -> AiLedgerRow | None:
        savepoint = transaction.begin_nested()
        try:
            transaction.execute(insert(db.ai_messages).values({"tenant_id": record.tenant_id, **record.__dict__}))
        except IntegrityError:
            savepoint.rollback()
            existing = self.message_by_client_id(
                transaction=transaction,
                tenant_id=record.tenant_id,
                session_id=record.session_id,
                client_message_id=record.client_message_id,
            )
            if existing is not None:
                return existing
            raise
        savepoint.commit()
        return None

    def create_execution_run(self, *, transaction: Any, record: AiExecutionRunRecord) -> None:
        transaction.execute(
            insert(db.ai_execution_runs).values({"tenant_id": record.tenant_id, **_execution_run_values(record)})
        )

    def insert_execution_run_or_get_existing(
        self, *, transaction: Any, record: AiExecutionRunRecord
    ) -> AiLedgerRow | None:
        values = {"tenant_id": record.tenant_id, **_execution_run_values(record)}
        statement: Any
        if transaction.dialect.name == "postgresql":
            statement = (
                postgres_insert(db.ai_execution_runs).values(**values).on_conflict_do_nothing(index_elements=("id",))
            )
        elif transaction.dialect.name == "sqlite":
            statement = (
                sqlite_insert(db.ai_execution_runs).values(**values).on_conflict_do_nothing(index_elements=("id",))
            )
        else:
            statement = insert(db.ai_execution_runs).values(**values)
        inserted = transaction.execute(statement.returning(db.ai_execution_runs.c.id)).scalar_one_or_none()
        if inserted == record.id:
            return None
        return self.execution_run_by_id(
            transaction=transaction,
            tenant_id=record.tenant_id,
            ai_run_id=record.id,
        )

    def update_execution_run_status(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        ai_run_id: str,
        transition: StatusTransition,
        usage_json: AiJsonObject | None,
        error_json: AiJsonObject | None,
        completed_at: str | None,
    ) -> AiLedgerRow | None:
        updated = cas_status_update(
            transaction,
            db.ai_execution_runs,
            tenant_id=tenant_id,
            row_id=ai_run_id,
            transition=transition,
            values={
                "usage_json": _json_or_none(usage_json),
                "error_json": _json_or_none(error_json),
                "completed_at": completed_at,
            },
        )
        if not updated:
            return None
        return self.execution_run_by_id(transaction=transaction, tenant_id=tenant_id, ai_run_id=ai_run_id)

    def append_execution_event(self, *, transaction: Any, record: AiExecutionEventRecord) -> bool:
        savepoint = transaction.begin_nested()
        try:
            transaction.execute(
                insert(db.ai_execution_events).values({"tenant_id": record.tenant_id, **record.__dict__})
            )
        except IntegrityError:
            savepoint.rollback()
            if _event_sequence_exists(transaction, record):
                return False
            raise
        savepoint.commit()
        return True

    def record_model_call(self, *, transaction: Any, record: AiModelCallRecord) -> None:
        transaction.execute(
            insert(db.ai_model_calls).values({"tenant_id": record.tenant_id, **_model_call_values(record)})
        )

    def record_context_item(self, *, transaction: Any, record: AiContextItemRecord) -> None:
        transaction.execute(
            insert(db.ai_context_items).values({"tenant_id": record.tenant_id, **_context_item_values(record)})
        )

    def record_tool_call(self, *, transaction: Any, record: AiToolCallRecord) -> None:
        transaction.execute(
            insert(db.ai_tool_calls).values({"tenant_id": record.tenant_id, **_tool_call_values(record)})
        )

    def link_tool_call_to_action_run(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        ai_run_id: str,
        tool_call_id: str,
        action_run_id: str,
    ) -> AiLedgerRow | None:
        transaction.execute(
            update(db.ai_tool_calls)
            .where(
                and_(
                    db.ai_tool_calls.c.tenant_id == tenant_id,
                    db.ai_tool_calls.c.ai_run_id == ai_run_id,
                    db.ai_tool_calls.c.id == tool_call_id,
                    db.ai_tool_calls.c.effect == "PROPOSE_WRITE",
                    or_(
                        db.ai_tool_calls.c.linked_action_run_id.is_(None),
                        db.ai_tool_calls.c.linked_action_run_id == action_run_id,
                    ),
                )
            )
            .values(linked_action_run_id=action_run_id)
        )
        row = _one_by_id(transaction, db.ai_tool_calls, tenant_id, tool_call_id)
        if row is not None and row["linked_action_run_id"] == action_run_id:
            return row
        return None

    def record_citation(self, *, transaction: Any, record: AiCitationRecord) -> None:
        transaction.execute(insert(db.ai_citations).values({"tenant_id": record.tenant_id, **_citation_values(record)}))

    def record_usage(self, *, transaction: Any, record: AiUsageLedgerRecord) -> None:
        transaction.execute(insert(db.ai_usage_ledger).values({"tenant_id": record.tenant_id, **record.__dict__}))

    def record_prompt_artifact(self, *, transaction: Any, record: AiPromptArtifactRecord) -> None:
        transaction.execute(
            insert(db.ai_prompt_artifacts).values({"tenant_id": record.tenant_id, **_prompt_artifact_values(record)})
        )

    def session_by_id(self, *, transaction: Any, tenant_id: str, session_id: str) -> AiLedgerRow | None:
        return _one_by_id(transaction, db.ai_sessions, tenant_id, session_id)

    def message_by_client_id(
        self, *, transaction: Any, tenant_id: str, session_id: str, client_message_id: str
    ) -> AiLedgerRow | None:
        row = (
            transaction.execute(
                select(db.ai_messages).where(
                    and_(
                        db.ai_messages.c.tenant_id == tenant_id,
                        db.ai_messages.c.session_id == session_id,
                        db.ai_messages.c.client_message_id == client_message_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        return _row_or_none(row)

    def state_versions_for_session(self, *, transaction: Any, tenant_id: str, session_id: str) -> list[AiLedgerRow]:
        rows = (
            transaction.execute(
                select(db.ai_session_state_versions)
                .where(
                    and_(
                        db.ai_session_state_versions.c.tenant_id == tenant_id,
                        db.ai_session_state_versions.c.session_id == session_id,
                    )
                )
                .order_by(db.ai_session_state_versions.c.version, db.ai_session_state_versions.c.id)
            )
            .mappings()
            .all()
        )
        return _rows(rows)

    def execution_run_by_id(self, *, transaction: Any, tenant_id: str, ai_run_id: str) -> AiLedgerRow | None:
        return _one_by_id(transaction, db.ai_execution_runs, tenant_id, ai_run_id)

    def ledger_for_run(self, *, transaction: Any, tenant_id: str, ai_run_id: str) -> AiRunLedgerSnapshot | None:
        run = self.execution_run_by_id(transaction=transaction, tenant_id=tenant_id, ai_run_id=ai_run_id)
        if run is None:
            return None
        return {
            "run": run,
            "events": _rows_for_run(
                transaction, db.ai_execution_events, tenant_id, ai_run_id, db.ai_execution_events.c.sequence
            ),
            "modelCalls": _rows_for_run(
                transaction, db.ai_model_calls, tenant_id, ai_run_id, db.ai_model_calls.c.attempt
            ),
            "contextItems": _rows_for_run(
                transaction, db.ai_context_items, tenant_id, ai_run_id, db.ai_context_items.c.id
            ),
            "toolCalls": _rows_for_run(
                transaction, db.ai_tool_calls, tenant_id, ai_run_id, db.ai_tool_calls.c.sequence
            ),
            "citations": _rows_for_run(
                transaction, db.ai_citations, tenant_id, ai_run_id, db.ai_citations.c.citation_order
            ),
            "usage": _rows_for_run(
                transaction, db.ai_usage_ledger, tenant_id, ai_run_id, db.ai_usage_ledger.c.recorded_at
            ),
            "promptArtifacts": _rows_for_run(
                transaction, db.ai_prompt_artifacts, tenant_id, ai_run_id, db.ai_prompt_artifacts.c.created_at
            ),
        }

    def prompt_artifact_by_id(self, *, transaction: Any, tenant_id: str, artifact_id: str) -> AiLedgerRow | None:
        return _one_by_id(transaction, db.ai_prompt_artifacts, tenant_id, artifact_id)


def _json_or_none(value: AiJsonObject | None) -> dict[str, object] | None:
    return dict(value) if value is not None else None


def _same_session_owner(existing: AiLedgerRow, record: AiSessionRecord) -> bool:
    return (
        existing.get("actor_user_id") == record.actor_user_id
        and existing.get("agent_version_id") == record.agent_version_id
    )


def _state_version_values(record: AiSessionStateVersionRecord) -> dict[str, object]:
    return {
        **record.__dict__,
        "state_json": dict(record.state_json),
    }


def _execution_run_values(record: AiExecutionRunRecord) -> dict[str, object]:
    values = dict(record.__dict__)
    values["budget_json"] = dict(record.budget_json)
    values["usage_json"] = _json_or_none(record.usage_json)
    values["error_json"] = _json_or_none(record.error_json)
    return values


def _model_call_values(record: AiModelCallRecord) -> dict[str, object]:
    values = dict(record.__dict__)
    values["error_json"] = _json_or_none(record.error_json)
    return values


def _context_item_values(record: AiContextItemRecord) -> dict[str, object]:
    values = dict(record.__dict__)
    values["selected"] = values.pop("is_selected")
    return values


def _tool_call_values(record: AiToolCallRecord) -> dict[str, object]:
    values = dict(record.__dict__)
    values["error_json"] = _json_or_none(record.error_json)
    values["result_json"] = _json_or_none(record.result_json)
    return values


def _citation_values(record: AiCitationRecord) -> dict[str, object]:
    return {
        **record.__dict__,
        "claim_span": dict(record.claim_span),
    }


def _prompt_artifact_values(record: AiPromptArtifactRecord) -> dict[str, object]:
    values = dict(record.__dict__)
    values["legal_hold"] = values.pop("is_legal_hold")
    return values


def _event_sequence_exists(transaction: Any, record: AiExecutionEventRecord) -> bool:
    row = (
        transaction.execute(
            select(db.ai_execution_events.c.id).where(
                and_(
                    db.ai_execution_events.c.ai_run_id == record.ai_run_id,
                    db.ai_execution_events.c.sequence == record.sequence,
                )
            )
        )
        .mappings()
        .first()
    )
    return row is not None


def _one_by_id(transaction: Any, table: Any, tenant_id: str, row_id: str) -> AiLedgerRow | None:
    row = (
        transaction.execute(select(table).where(and_(table.c.tenant_id == tenant_id, table.c.id == row_id)))
        .mappings()
        .first()
    )
    return _row_or_none(row)


def _rows_for_run(transaction: Any, table: Any, tenant_id: str, ai_run_id: str, order_by: Any) -> list[AiLedgerRow]:
    rows = (
        transaction.execute(
            select(table)
            .where(and_(table.c.tenant_id == tenant_id, table.c.ai_run_id == ai_run_id))
            .order_by(order_by, table.c.id)
        )
        .mappings()
        .all()
    )
    return _rows(rows)


def _row_or_none(row: Any) -> AiLedgerRow | None:
    return cast(AiLedgerRow, dict(row)) if row else None


def _rows(rows: Any) -> list[AiLedgerRow]:
    return [cast(AiLedgerRow, dict(row)) for row in rows]
