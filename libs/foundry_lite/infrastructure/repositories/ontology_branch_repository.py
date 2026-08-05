"""SQLAlchemy repository adapter for ontology branch persistence."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, desc, insert, or_, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from foundry_lite.application.ports.ontology_branch_repository import (
    OntologyBranchActionIdempotencyRecord,
    OntologyBranchActionIdempotencyRow,
    OntologyBranchRebase,
    OntologyBranchRecord,
    OntologyBranchRow,
)
from foundry_lite.application.ports.transaction_context import StatusTransition, TransitionResult
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.status_cas import (
    cas_status_guarded_update,
    cas_status_transition,
)


class SqlAlchemyOntologyBranchRepository:
    """Tenant-scoped ontology branch rows with CAS-guarded content updates."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def insert_branch_if_name_free(
        self,
        *,
        transaction: Any,
        record: OntologyBranchRecord,
    ) -> OntologyBranchRow | None:
        existing = self.open_branch_by_name(transaction=transaction, tenant_id=record.tenant_id, name=record.name)
        if existing is not None:
            return None
        transaction.execute(
            db.ontology_branches.insert().values(
                id=record.branch_id,
                tenant_id=record.tenant_id,
                name=record.name,
                status=record.status,
                base_version_id=record.base_version_id,
                base_version_number=record.base_version_number,
                base_yaml_text=record.base_yaml_text,
                content_yaml_text=record.content_yaml_text,
                content_fingerprint=record.content_fingerprint,
                created_by=record.created_by,
                created_at=record.created_at,
                updated_at=record.updated_at,
                rebased_at=None,
                merged_proposal_id=None,
                merged_version_number=None,
            )
        )
        return self.branch_by_id(transaction=transaction, tenant_id=record.tenant_id, branch_id=record.branch_id)

    def branch_by_id(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        branch_id: str,
    ) -> OntologyBranchRow | None:
        row = (
            transaction.execute(
                select(db.ontology_branches).where(
                    and_(db.ontology_branches.c.tenant_id == tenant_id, db.ontology_branches.c.id == branch_id)
                )
            )
            .mappings()
            .first()
        )
        return cast(OntologyBranchRow, dict(row)) if row is not None else None

    def open_branch_by_name(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        name: str,
    ) -> OntologyBranchRow | None:
        row = (
            transaction.execute(
                select(db.ontology_branches).where(
                    and_(
                        db.ontology_branches.c.tenant_id == tenant_id,
                        db.ontology_branches.c.name == name,
                        db.ontology_branches.c.status == "open",
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(OntologyBranchRow, dict(row)) if row is not None else None

    def list_branches(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        status: str | None,
        created_before: str | None,
        before_id: str | None,
        limit: int,
    ) -> list[OntologyBranchRow]:
        conditions: list[Any] = [db.ontology_branches.c.tenant_id == tenant_id]
        if status is not None:
            conditions.append(db.ontology_branches.c.status == status)
        if created_before is not None and before_id is not None:
            conditions.append(
                or_(
                    db.ontology_branches.c.created_at < created_before,
                    and_(
                        db.ontology_branches.c.created_at == created_before,
                        db.ontology_branches.c.id < before_id,
                    ),
                )
            )
        rows = (
            transaction.execute(
                select(db.ontology_branches)
                .where(and_(*conditions))
                .order_by(desc(db.ontology_branches.c.created_at), desc(db.ontology_branches.c.id))
                .limit(limit)
            )
            .mappings()
            .all()
        )
        return [cast(OntologyBranchRow, dict(row)) for row in rows]

    def update_branch_content(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        branch_id: str,
        expected_fingerprint: str,
        content_yaml_text: str,
        content_fingerprint: str,
        updated_at: str,
    ) -> OntologyBranchRow | None:
        updated = cas_status_guarded_update(
            transaction,
            db.ontology_branches,
            tenant_id=tenant_id,
            row_id=branch_id,
            allowed_statuses=("open",),
            values={
                "content_yaml_text": content_yaml_text,
                "content_fingerprint": content_fingerprint,
                "updated_at": updated_at,
            },
            conditions=(db.ontology_branches.c.content_fingerprint == expected_fingerprint,),
        )
        if not updated:
            return None
        return self.branch_by_id(transaction=transaction, tenant_id=tenant_id, branch_id=branch_id)

    def rebase_branch(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        branch_id: str,
        expected_fingerprint: str,
        rebase: OntologyBranchRebase,
    ) -> OntologyBranchRow | None:
        updated = cas_status_guarded_update(
            transaction,
            db.ontology_branches,
            tenant_id=tenant_id,
            row_id=branch_id,
            allowed_statuses=("open",),
            values={
                "base_version_id": rebase.base_version_id,
                "base_version_number": rebase.base_version_number,
                "base_yaml_text": rebase.base_yaml_text,
                "content_yaml_text": rebase.content_yaml_text,
                "content_fingerprint": rebase.content_fingerprint,
                "rebased_at": rebase.rebased_at,
                "updated_at": rebase.updated_at,
            },
            conditions=(db.ontology_branches.c.content_fingerprint == expected_fingerprint,),
        )
        if not updated:
            return None
        return self.branch_by_id(transaction=transaction, tenant_id=tenant_id, branch_id=branch_id)

    def set_branch_proposal(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        branch_id: str,
        proposal_id: str,
        updated_at: str,
    ) -> OntologyBranchRow | None:
        updated = cas_status_guarded_update(
            transaction,
            db.ontology_branches,
            tenant_id=tenant_id,
            row_id=branch_id,
            allowed_statuses=("open",),
            values={"merged_proposal_id": proposal_id, "updated_at": updated_at},
        )
        if not updated:
            return None
        return self.branch_by_id(transaction=transaction, tenant_id=tenant_id, branch_id=branch_id)

    def transition_branch_status(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        branch_id: str,
        transition: StatusTransition,
        merged_version_number: int | None,
        updated_at: str,
    ) -> TransitionResult:
        values: dict[str, object] = {"updated_at": updated_at}
        if merged_version_number is not None:
            values["merged_version_number"] = merged_version_number
        return cas_status_transition(
            transaction,
            db.ontology_branches,
            tenant_id=tenant_id,
            row_id=branch_id,
            transition=transition,
            values=values,
        )

    def action_idempotency_record(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        actor_user_id: str,
        branch_id: str,
        operation: str,
        idempotency_key: str,
    ) -> OntologyBranchActionIdempotencyRow | None:
        table = db.ontology_branch_action_idempotency_records
        row = (
            transaction.execute(
                select(table).where(
                    and_(
                        table.c.tenant_id == tenant_id,
                        table.c.actor_user_id == actor_user_id,
                        table.c.branch_id == branch_id,
                        table.c.operation == operation,
                        table.c.idempotency_key == idempotency_key,
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(OntologyBranchActionIdempotencyRow, dict(row)) if row is not None else None

    def insert_action_idempotency_or_existing(
        self,
        *,
        transaction: Any,
        record: OntologyBranchActionIdempotencyRecord,
    ) -> OntologyBranchActionIdempotencyRow | None:
        inserted_id = transaction.execute(
            _insert_action_idempotency_or_ignore(transaction, _action_idempotency_values(record))
        ).scalar_one_or_none()
        if inserted_id == record.record_id:
            return None
        return self.action_idempotency_record(
            transaction=transaction,
            tenant_id=record.tenant_id,
            actor_user_id=record.actor_user_id,
            branch_id=record.branch_id,
            operation=record.operation,
            idempotency_key=record.idempotency_key,
        )


def _insert_action_idempotency_or_ignore(transaction: Any, values: dict[str, object]) -> Any:
    table = db.ontology_branch_action_idempotency_records
    conflict = ("tenant_id", "actor_user_id", "branch_id", "operation", "idempotency_key")
    if transaction.dialect.name == "postgresql":
        return (
            postgres_insert(table)
            .values(**values)
            .on_conflict_do_nothing(index_elements=conflict)
            .returning(table.c.id)
        )
    if transaction.dialect.name == "sqlite":
        return (
            sqlite_insert(table).values(**values).on_conflict_do_nothing(index_elements=conflict).returning(table.c.id)
        )
    return insert(table).values(**values).returning(table.c.id)


def _action_idempotency_values(record: OntologyBranchActionIdempotencyRecord) -> dict[str, object]:
    return {
        "id": record.record_id,
        "tenant_id": record.tenant_id,
        "actor_user_id": record.actor_user_id,
        "branch_id": record.branch_id,
        "operation": record.operation,
        "idempotency_key": record.idempotency_key,
        "request_fingerprint": record.request_fingerprint,
        "response_json": record.response_json,
        "created_at": record.created_at,
    }
