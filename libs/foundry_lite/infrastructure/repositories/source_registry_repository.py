"""SQLAlchemy repository adapter for source registry repository persistence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from sqlalchemy import and_, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from foundry_lite.application.ports.source_registry_repository import (
    SourceConnectionAlreadyExistsError,
    SourceConnectionRecord,
    SourceConnectionRow,
    SourceConnectionTestAlreadyExistsError,
    SourceConnectionTestRecord,
    SourceConnectionTestRow,
    SourceConnectionUpdate,
)
from foundry_lite.application.ports.transaction_context import StatusTransition
from foundry_lite.application.state_transitions import (
    SOURCE_CONNECTION_TEST_FAILED,
    SOURCE_CONNECTION_TEST_SUCCEEDED,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.status_cas import cas_status_update, cas_status_update_many


class SqlAlchemySourceRegistryRepository:
    """SQLAlchemy implementation of product Source onboarding read models."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_source(self, *, transaction: Any, record: SourceConnectionRecord) -> None:
        try:
            transaction.execute(
                insert(db.source_connections).values(
                    id=record.source_id,
                    tenant_id=record.tenant_id,
                    source_name=record.source_name,
                    display_name=record.display_name,
                    kind=record.kind,
                    target_dataset_ref=record.target_dataset_ref,
                    target_media_set_id=record.target_media_set_id,
                    status=record.status,
                    config_summary=dict(record.config_summary),
                    config_fingerprint=record.config_fingerprint,
                    last_run_id=record.last_run_id,
                    last_workflow_run_id=record.last_workflow_run_id,
                    last_commit_ref=dict(record.last_commit_ref) if record.last_commit_ref is not None else None,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                )
            )
        except IntegrityError as exc:
            raise SourceConnectionAlreadyExistsError from exc

    def update_source(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        source_name: str,
        patch: SourceConnectionUpdate,
    ) -> SourceConnectionRow | None:
        values = _source_patch_values(patch)
        if values:
            transaction.execute(
                update(db.source_connections)
                .where(
                    and_(
                        db.source_connections.c.tenant_id == tenant_id,
                        db.source_connections.c.source_name == source_name,
                    )
                )
                .values(**values)
            )
        return self.source_by_name(transaction=transaction, tenant_id=tenant_id, source_name=source_name)

    def update_source_status(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        source_name: str,
        transition: StatusTransition,
        expected_config_fingerprint: str,
        updated_at: str,
    ) -> SourceConnectionRow | None:
        updated = cas_status_update_many(
            transaction,
            db.source_connections,
            tenant_id=tenant_id,
            transition=transition,
            values={"updated_at": updated_at},
            conditions=(
                db.source_connections.c.source_name == source_name,
                db.source_connections.c.config_fingerprint == expected_config_fingerprint,
            ),
        )
        if updated != 1:
            return None
        return self.source_by_name(transaction=transaction, tenant_id=tenant_id, source_name=source_name)

    def source_by_name(self, *, transaction: Any, tenant_id: str, source_name: str) -> SourceConnectionRow | None:
        row = (
            transaction.execute(
                select(db.source_connections).where(
                    and_(
                        db.source_connections.c.tenant_id == tenant_id,
                        db.source_connections.c.source_name == source_name,
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(SourceConnectionRow, dict(row)) if row else None

    def list_sources(self, *, tenant_id: str) -> list[SourceConnectionRow]:
        with self.engine.begin() as conn:
            rows = (
                conn.execute(
                    select(db.source_connections)
                    .where(db.source_connections.c.tenant_id == tenant_id)
                    .order_by(db.source_connections.c.source_name)
                )
                .mappings()
                .all()
            )
            return [cast(SourceConnectionRow, dict(row)) for row in rows]

    def create_connection_test(self, *, transaction: Any, record: SourceConnectionTestRecord) -> None:
        try:
            with transaction.begin_nested():
                transaction.execute(
                    insert(db.source_connection_tests).values(
                        id=record.test_id,
                        tenant_id=record.tenant_id,
                        source_name=record.source_name,
                        source_type=record.source_type,
                        status=record.status,
                        config_fingerprint=record.config_fingerprint,
                        idempotency_key=record.idempotency_key,
                        checks=dict(record.checks),
                        error=dict(record.error) if record.error is not None else None,
                        operations_path=record.operations_path,
                        started_at=record.started_at,
                        completed_at=record.completed_at,
                        created_at=record.created_at,
                    )
                )
        except IntegrityError as exc:
            raise SourceConnectionTestAlreadyExistsError from exc

    def connection_test_by_idempotency_key(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        source_name: str,
        idempotency_key: str,
    ) -> SourceConnectionTestRow | None:
        row = (
            transaction.execute(
                select(db.source_connection_tests).where(
                    and_(
                        db.source_connection_tests.c.tenant_id == tenant_id,
                        db.source_connection_tests.c.source_name == source_name,
                        db.source_connection_tests.c.idempotency_key == idempotency_key,
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(SourceConnectionTestRow, dict(row)) if row else None

    def complete_connection_test(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        test_id: str,
        status: str,
        checks: Mapping[str, object],
        error: Mapping[str, object] | None,
        completed_at: str,
    ) -> SourceConnectionTestRow | None:
        updated = cas_status_update(
            transaction,
            db.source_connection_tests,
            tenant_id=tenant_id,
            row_id=test_id,
            transition=_connection_test_transition(status),
            values={
                "checks": dict(checks),
                "error": dict(error) if error is not None else None,
                "completed_at": completed_at,
            },
        )
        if not updated:
            return None
        return self._connection_test_by_id(transaction, tenant_id, test_id)

    def list_connection_tests(self, *, tenant_id: str, source_name: str, limit: int) -> list[SourceConnectionTestRow]:
        with self.engine.begin() as conn:
            rows = (
                conn.execute(
                    select(db.source_connection_tests)
                    .where(
                        and_(
                            db.source_connection_tests.c.tenant_id == tenant_id,
                            db.source_connection_tests.c.source_name == source_name,
                        )
                    )
                    .order_by(db.source_connection_tests.c.created_at.desc())
                    .limit(limit)
                )
                .mappings()
                .all()
            )
        return [cast(SourceConnectionTestRow, dict(row)) for row in rows]

    @staticmethod
    def _connection_test_by_id(transaction: Any, tenant_id: str, test_id: str) -> SourceConnectionTestRow | None:
        row = (
            transaction.execute(
                select(db.source_connection_tests).where(
                    and_(
                        db.source_connection_tests.c.tenant_id == tenant_id,
                        db.source_connection_tests.c.id == test_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(SourceConnectionTestRow, dict(row)) if row else None


def _connection_test_transition(status: str) -> StatusTransition:
    transitions = {
        "succeeded": SOURCE_CONNECTION_TEST_SUCCEEDED,
        "failed": SOURCE_CONNECTION_TEST_FAILED,
    }
    try:
        return transitions[status]
    except KeyError as exc:
        raise ValueError(f"unsupported source connection test status: {status}") from exc


def _source_patch_values(patch: SourceConnectionUpdate) -> dict[str, object]:
    raw_values: dict[str, object | None] = {
        "display_name": patch.display_name,
        "target_dataset_ref": patch.target_dataset_ref,
        "target_media_set_id": patch.target_media_set_id,
        "status": patch.status,
        "config_summary": patch.config_summary,
        "config_fingerprint": patch.config_fingerprint,
        "last_run_id": patch.last_run_id,
        "last_workflow_run_id": patch.last_workflow_run_id,
        "last_commit_ref": patch.last_commit_ref,
        "updated_at": patch.updated_at,
    }
    return {key: _patch_value(key, value) for key, value in raw_values.items() if value is not None}


def _patch_value(key: str, value: object) -> object:
    if key in {"config_summary", "last_commit_ref"} and isinstance(value, Mapping):
        return dict(value)
    return value
