"""SQLAlchemy adapter for Governed Release external delivery persistence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from sqlalchemy import and_, insert, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from foundry_lite.application.ports.release_delivery_repository import (
    ClaimReleaseDeliveryCommand,
    CompleteReleaseDeliveryCommand,
    PrepareReleaseDeliveryCommand,
    ReconcileReleaseDeliveryCommand,
    ReleaseDeliveryIdempotencyConflict,
    ReleaseDeliveryIntegrityError,
    ReleaseDeliveryJson,
    ReleaseDeliveryKind,
    ReleaseDeliveryMutationResult,
    ReleaseDeliveryOperation,
    ReleaseDeliveryRecord,
    ReleaseDeliveryStatus,
    ReleaseDeliveryTerminalConflict,
)
from foundry_lite.application.state_transitions import StatusTransition
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.status_cas import cas_status_update

_TERMINAL_STATUSES = frozenset({"landed", "absent", "ambiguous", "failed"})


class SqlAlchemyReleaseDeliveryRepository:
    """Store write-ahead external intents without owning transactions."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def prepare(
        self,
        *,
        transaction: Any,
        command: PrepareReleaseDeliveryCommand,
    ) -> ReleaseDeliveryMutationResult:
        values = _prepare_values(command)
        inserted_id = transaction.execute(_insert_or_ignore(transaction, values)).scalar_one_or_none()
        if inserted_id is not None:
            inserted = self.get(
                transaction=transaction,
                tenant_id=command.tenant_id,
                delivery_id=command.delivery_id,
            )
            if inserted is None:
                raise ReleaseDeliveryIntegrityError("inserted release delivery could not be read back")
            return inserted, True
        existing = self.find_by_idempotency(
            transaction=transaction,
            tenant_id=command.tenant_id,
            provider=command.provider,
            operation=command.operation,
            idempotency_key=command.idempotency_key,
        )
        if existing is None:
            raise ReleaseDeliveryIntegrityError("ignored release delivery insert had no idempotent row")
        if not _is_same_prepare_identity(existing, command):
            raise ReleaseDeliveryIdempotencyConflict("release delivery key was reused outside its request binding")
        return existing, False

    def get(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        delivery_id: str,
    ) -> ReleaseDeliveryRecord | None:
        table = db.governed_release_deliveries
        row = (
            transaction.execute(select(table).where(and_(table.c.tenant_id == tenant_id, table.c.id == delivery_id)))
            .mappings()
            .first()
        )
        return _record(row) if row is not None else None

    def find_by_idempotency(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        provider: str,
        operation: ReleaseDeliveryOperation,
        idempotency_key: str,
    ) -> ReleaseDeliveryRecord | None:
        table = db.governed_release_deliveries
        row = (
            transaction.execute(
                select(table).where(
                    and_(
                        table.c.tenant_id == tenant_id,
                        table.c.provider == provider,
                        table.c.operation == operation,
                        table.c.idempotency_key == idempotency_key,
                    )
                )
            )
            .mappings()
            .first()
        )
        return _record(row) if row is not None else None

    def claim_dispatch(
        self,
        *,
        transaction: Any,
        command: ClaimReleaseDeliveryCommand,
    ) -> ReleaseDeliveryRecord | None:
        table = db.governed_release_deliveries
        updated = cas_status_update(
            transaction,
            table,
            tenant_id=command.tenant_id,
            row_id=command.delivery_id,
            transition=StatusTransition((command.expected_status,), "dispatching"),
            values={
                "execution_attempt": command.expected_attempt + 1,
                "dispatch_started_at": command.dispatch_started_at,
                "updated_at": command.updated_at,
            },
            conditions=(table.c.execution_attempt == command.expected_attempt,),
        )
        if not updated:
            return None
        return self.get(
            transaction=transaction,
            tenant_id=command.tenant_id,
            delivery_id=command.delivery_id,
        )

    def complete(
        self,
        *,
        transaction: Any,
        command: CompleteReleaseDeliveryCommand,
    ) -> ReleaseDeliveryMutationResult | None:
        table = db.governed_release_deliveries
        updated = cas_status_update(
            transaction,
            table,
            tenant_id=command.tenant_id,
            row_id=command.delivery_id,
            transition=StatusTransition((command.expected_status,), command.terminal_status),
            values=_outcome_values(command),
            conditions=(table.c.execution_attempt == command.expected_attempt,),
        )
        current = self.get(
            transaction=transaction,
            tenant_id=command.tenant_id,
            delivery_id=command.delivery_id,
        )
        if updated:
            if current is None:
                raise ReleaseDeliveryIntegrityError("completed release delivery could not be read back")
            return current, True
        if current is None or current.status not in _TERMINAL_STATUSES:
            return None
        if _is_same_terminal_result(current, command):
            return current, False
        raise ReleaseDeliveryTerminalConflict("release delivery already has a different terminal outcome")

    def reconcile(
        self,
        *,
        transaction: Any,
        command: ReconcileReleaseDeliveryCommand,
    ) -> ReleaseDeliveryMutationResult | None:
        table = db.governed_release_deliveries
        updated = cas_status_update(
            transaction,
            table,
            tenant_id=command.tenant_id,
            row_id=command.delivery_id,
            transition=StatusTransition((command.expected_status,), command.terminal_status),
            values=_outcome_values(command),
            conditions=(table.c.execution_attempt == command.expected_attempt,),
        )
        current = self.get(
            transaction=transaction,
            tenant_id=command.tenant_id,
            delivery_id=command.delivery_id,
        )
        if updated:
            if current is None:
                raise ReleaseDeliveryIntegrityError("reconciled release delivery could not be read back")
            return current, True
        if current is None or current.status not in {"landed", "absent", "failed"}:
            return None
        if _is_same_terminal_result(current, command):
            return current, False
        raise ReleaseDeliveryTerminalConflict("release delivery already has a different reconciled outcome")

    def list_for_proposal(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        proposal_id: str,
        limit: int,
    ) -> tuple[ReleaseDeliveryRecord, ...]:
        table = db.governed_release_deliveries
        rows = (
            transaction.execute(
                select(table)
                .where(and_(table.c.tenant_id == tenant_id, table.c.proposal_id == proposal_id))
                .order_by(table.c.created_at, table.c.id)
                .limit(limit)
            )
            .mappings()
            .all()
        )
        return tuple(_record(row) for row in rows)

    def list_by_statuses(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        statuses: tuple[ReleaseDeliveryStatus, ...],
        limit: int,
    ) -> tuple[ReleaseDeliveryRecord, ...]:
        table = db.governed_release_deliveries
        rows = (
            transaction.execute(
                select(table)
                .where(and_(table.c.tenant_id == tenant_id, table.c.status.in_(statuses)))
                .order_by(table.c.updated_at, table.c.id)
                .limit(limit)
            )
            .mappings()
            .all()
        )
        return tuple(_record(row) for row in rows)

    def list_for_workflow(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        application_id: str,
        workflow_run_id: str,
        limit: int,
    ) -> tuple[ReleaseDeliveryRecord, ...]:
        table = db.governed_release_deliveries
        rows = (
            transaction.execute(
                select(table)
                .where(
                    and_(
                        table.c.tenant_id == tenant_id,
                        table.c.application_id == application_id,
                        table.c.workflow_run_id == workflow_run_id,
                    )
                )
                .order_by(table.c.created_at, table.c.id)
                .limit(limit)
            )
            .mappings()
            .all()
        )
        return tuple(_record(row) for row in rows)

    def list_workflow_roots(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        application_id: str,
        release_kind: ReleaseDeliveryKind,
        limit: int,
    ) -> tuple[ReleaseDeliveryRecord, ...]:
        table = db.governed_release_deliveries
        rows = (
            transaction.execute(
                select(table)
                .where(
                    and_(
                        table.c.tenant_id == tenant_id,
                        table.c.application_id == application_id,
                        table.c.release_kind == release_kind,
                        table.c.operation == "source_publish",
                        table.c.status == "landed",
                        table.c.parent_delivery_id.is_(None),
                    )
                )
                .order_by(table.c.created_at.desc(), table.c.id.desc())
                .limit(limit)
            )
            .mappings()
            .all()
        )
        return tuple(_record(row) for row in rows)


def _insert_or_ignore(transaction: Any, values: Mapping[str, object]) -> Any:
    table = db.governed_release_deliveries
    if transaction.dialect.name == "postgresql":
        return postgres_insert(table).values(**values).on_conflict_do_nothing().returning(table.c.id)
    if transaction.dialect.name == "sqlite":
        return sqlite_insert(table).values(**values).on_conflict_do_nothing().returning(table.c.id)
    return insert(table).values(**values).returning(table.c.id)


def _prepare_values(command: PrepareReleaseDeliveryCommand) -> dict[str, object]:
    return {
        "id": command.delivery_id,
        "tenant_id": command.tenant_id,
        "application_id": command.application_id,
        "proposal_id": command.proposal_id,
        "release_kind": command.release_kind,
        "workflow_run_id": command.workflow_run_id,
        "parent_delivery_id": command.parent_delivery_id,
        "provider": command.provider,
        "operation": command.operation,
        "status": "prepared",
        "target_ref": command.target_ref,
        "candidate_ref": command.candidate_ref,
        "environment": command.environment,
        "idempotency_key": command.idempotency_key,
        "request_fingerprint": command.request_fingerprint,
        "provider_operation_id": None,
        "provider_resource_id": None,
        "prior_resource_id": command.prior_resource_id,
        "result_ref": None,
        "error_ref": None,
        "ai_run_id": command.ai_run_id,
        "binding_hash": command.binding_hash,
        "execution_attempt": 0,
        "request_id": command.request_id,
        "created_by": command.created_by,
        "created_at": command.created_at,
        "updated_at": command.updated_at,
        "dispatch_started_at": None,
        "completed_at": None,
    }


def _is_same_prepare_identity(
    record: ReleaseDeliveryRecord,
    command: PrepareReleaseDeliveryCommand,
) -> bool:
    return (
        record.proposal_id == command.proposal_id
        and record.application_id == command.application_id
        and record.release_kind == command.release_kind
        and record.workflow_run_id == command.workflow_run_id
        and record.parent_delivery_id == command.parent_delivery_id
        and record.request_fingerprint == command.request_fingerprint
        and record.ai_run_id == command.ai_run_id
        and record.binding_hash == command.binding_hash
        and record.created_by == command.created_by
    )


def _outcome_values(
    command: CompleteReleaseDeliveryCommand | ReconcileReleaseDeliveryCommand,
) -> dict[str, object]:
    return {
        "provider_operation_id": command.provider_operation_id,
        "provider_resource_id": command.provider_resource_id,
        "prior_resource_id": command.prior_resource_id,
        "result_ref": command.result_ref,
        "error_ref": command.error_ref,
        "completed_at": command.completed_at,
        "updated_at": command.updated_at,
    }


def _is_same_terminal_result(
    record: ReleaseDeliveryRecord,
    command: CompleteReleaseDeliveryCommand | ReconcileReleaseDeliveryCommand,
) -> bool:
    return (
        record.status == command.terminal_status
        and record.execution_attempt == command.expected_attempt
        and record.provider_operation_id == command.provider_operation_id
        and record.provider_resource_id == command.provider_resource_id
        and record.prior_resource_id == command.prior_resource_id
        and record.result_ref == command.result_ref
        and record.error_ref == command.error_ref
    )


def _record(row: Mapping[str, object]) -> ReleaseDeliveryRecord:
    return ReleaseDeliveryRecord(
        delivery_id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        application_id=str(row["application_id"]),
        proposal_id=str(row["proposal_id"]),
        release_kind=cast(ReleaseDeliveryKind, row["release_kind"]),
        workflow_run_id=str(row["workflow_run_id"]),
        parent_delivery_id=cast(str | None, row["parent_delivery_id"]),
        provider=str(row["provider"]),
        operation=cast(ReleaseDeliveryOperation, row["operation"]),
        status=cast(ReleaseDeliveryStatus, row["status"]),
        target_ref=cast(ReleaseDeliveryJson, row["target_ref"]),
        candidate_ref=cast(ReleaseDeliveryJson | None, row["candidate_ref"]),
        environment=str(row["environment"]),
        idempotency_key=str(row["idempotency_key"]),
        request_fingerprint=str(row["request_fingerprint"]),
        provider_operation_id=cast(str | None, row["provider_operation_id"]),
        provider_resource_id=cast(str | None, row["provider_resource_id"]),
        prior_resource_id=cast(str | None, row["prior_resource_id"]),
        result_ref=cast(ReleaseDeliveryJson | None, row["result_ref"]),
        error_ref=cast(ReleaseDeliveryJson | None, row["error_ref"]),
        ai_run_id=str(row["ai_run_id"]),
        binding_hash=str(row["binding_hash"]),
        execution_attempt=int(cast(int, row["execution_attempt"])),
        request_id=str(row["request_id"]),
        created_by=str(row["created_by"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        dispatch_started_at=cast(str | None, row["dispatch_started_at"]),
        completed_at=cast(str | None, row["completed_at"]),
    )


__all__ = ["SqlAlchemyReleaseDeliveryRepository"]
