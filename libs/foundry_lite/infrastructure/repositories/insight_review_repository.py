from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, desc, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from foundry_lite.application.ports.insight_review_repository import (
    InsightReviewDecision,
    InsightReviewJson,
    InsightReviewRecord,
    InsightReviewRow,
)
from foundry_lite.application.ports.transaction_context import StatusTransition
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.status_cas import cas_status_guarded_update, cas_status_update


class SqlAlchemyInsightReviewRepository:
    """SQLAlchemy implementation of durable insight review queue state."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def insert_review_or_get_existing(
        self,
        *,
        transaction: Any,
        record: InsightReviewRecord,
    ) -> InsightReviewRow | None:
        inserted_id = transaction.execute(_insight_review_insert_or_ignore(transaction, record)).scalar_one_or_none()
        if inserted_id == record.review_id:
            return None
        return self._review_by_create_key(transaction, record.tenant_id, record.created_idempotency_key)

    def review_by_id(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        review_id: str,
    ) -> InsightReviewRow | None:
        row = (
            transaction.execute(
                select(db.insight_reviews).where(
                    and_(db.insight_reviews.c.tenant_id == tenant_id, db.insight_reviews.c.id == review_id)
                )
            )
            .mappings()
            .first()
        )
        return cast(InsightReviewRow, dict(row)) if row else None

    def list_reviews(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        status: str | None,
        assignee_user_id: str | None,
        limit: int,
    ) -> list[InsightReviewRow]:
        conditions = [db.insight_reviews.c.tenant_id == tenant_id]
        if status:
            conditions.append(db.insight_reviews.c.status == status)
        if assignee_user_id:
            conditions.append(db.insight_reviews.c.assignee_user_id == assignee_user_id)
        rows = (
            transaction.execute(
                select(db.insight_reviews)
                .where(and_(*conditions))
                .order_by(desc(db.insight_reviews.c.updated_at), desc(db.insight_reviews.c.id))
                .limit(limit)
            )
            .mappings()
            .all()
        )
        return [cast(InsightReviewRow, dict(row)) for row in rows]

    def assign_review(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        review_id: str,
        assignee_user_id: str,
        assignment_idempotency_key: str,
        updated_at: str,
    ) -> InsightReviewRow | None:
        updated = cas_status_guarded_update(
            transaction,
            db.insight_reviews,
            tenant_id=tenant_id,
            row_id=review_id,
            allowed_statuses=("pending",),
            values={
                "assignee_user_id": assignee_user_id,
                "assignment_idempotency_key": assignment_idempotency_key,
                "updated_at": updated_at,
            },
        )
        if not updated:
            return None
        return self.review_by_id(transaction=transaction, tenant_id=tenant_id, review_id=review_id)

    def decide_review(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        review_id: str,
        status: InsightReviewDecision,
        decision: InsightReviewJson,
        decision_idempotency_key: str,
        updated_at: str,
    ) -> InsightReviewRow | None:
        updated = cas_status_update(
            transaction,
            db.insight_reviews,
            tenant_id=tenant_id,
            row_id=review_id,
            transition=StatusTransition(("pending",), status),
            values={
                "decision": dict(decision),
                "decision_idempotency_key": decision_idempotency_key,
                "updated_at": updated_at,
            },
        )
        if not updated:
            return None
        return self.review_by_id(transaction=transaction, tenant_id=tenant_id, review_id=review_id)

    def mark_execution_started(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        review_id: str,
        updated_at: str,
    ) -> InsightReviewRow | None:
        updated = transaction.execute(
            update(db.insight_reviews)
            .where(
                and_(
                    db.insight_reviews.c.tenant_id == tenant_id,
                    db.insight_reviews.c.id == review_id,
                    db.insight_reviews.c.status == "approved",
                    db.insight_reviews.c.execution_status == "pending_review",
                )
            )
            .values(execution_status="executing", updated_at=updated_at)
        )
        if updated.rowcount != 1:
            return None
        return self.review_by_id(transaction=transaction, tenant_id=tenant_id, review_id=review_id)

    def mark_execution_succeeded(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        review_id: str,
        action_run_id: str,
        updated_at: str,
    ) -> InsightReviewRow | None:
        updated = transaction.execute(
            update(db.insight_reviews)
            .where(
                and_(
                    db.insight_reviews.c.tenant_id == tenant_id,
                    db.insight_reviews.c.id == review_id,
                    db.insight_reviews.c.status == "approved",
                    db.insight_reviews.c.execution_status == "executing",
                )
            )
            .values(
                execution_status="executed",
                approved_action_run_id=action_run_id,
                updated_at=updated_at,
            )
        )
        if updated.rowcount != 1:
            return None
        return self.review_by_id(transaction=transaction, tenant_id=tenant_id, review_id=review_id)

    def mark_execution_failed(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        review_id: str,
        error: InsightReviewJson,
        updated_at: str,
    ) -> InsightReviewRow | None:
        row = self.review_by_id(transaction=transaction, tenant_id=tenant_id, review_id=review_id)
        if row is None or row["execution_status"] != "executing":
            return None
        metadata = {**dict(row["review_metadata"]), "executionError": dict(error)}
        updated = transaction.execute(
            update(db.insight_reviews)
            .where(
                and_(
                    db.insight_reviews.c.tenant_id == tenant_id,
                    db.insight_reviews.c.id == review_id,
                    db.insight_reviews.c.execution_status == "executing",
                )
            )
            .values(execution_status="failed", review_metadata=metadata, updated_at=updated_at)
        )
        if updated.rowcount != 1:
            return None
        return self.review_by_id(transaction=transaction, tenant_id=tenant_id, review_id=review_id)

    def _review_by_create_key(
        self,
        transaction: Any,
        tenant_id: str,
        idempotency_key: str,
    ) -> InsightReviewRow:
        row = (
            transaction.execute(
                select(db.insight_reviews).where(
                    and_(
                        db.insight_reviews.c.tenant_id == tenant_id,
                        db.insight_reviews.c.created_idempotency_key == idempotency_key,
                    )
                )
            )
            .mappings()
            .one()
        )
        return cast(InsightReviewRow, dict(row))


def _insight_review_values(record: InsightReviewRecord) -> dict[str, object]:
    return {
        "id": record.review_id,
        "tenant_id": record.tenant_id,
        "claim_id": record.claim_id,
        "claim_text": record.claim_text,
        "status": record.status,
        "priority": record.priority,
        "assignee_user_id": record.assignee_user_id,
        "evidence_object_ids": list(record.evidence_object_ids),
        "evidence_refs": [dict(ref) for ref in record.evidence_refs],
        "action_proposal": dict(record.action_proposal) if record.action_proposal is not None else None,
        "proposal_type": record.proposal_type,
        "proposal_fingerprint": record.proposal_fingerprint,
        "originating_ai_run_id": record.originating_ai_run_id,
        "originating_tool_call_id": record.originating_tool_call_id,
        "expires_at": record.expires_at,
        "execution_status": record.execution_status,
        "approved_action_run_id": record.approved_action_run_id,
        "approval_policy_version": record.approval_policy_version,
        "created_by_user_id": record.created_by_user_id,
        "created_idempotency_key": record.created_idempotency_key,
        "assignment_idempotency_key": record.assignment_idempotency_key,
        "decision": dict(record.decision) if record.decision is not None else None,
        "decision_idempotency_key": record.decision_idempotency_key,
        "review_metadata": dict(record.review_metadata),
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _insight_review_insert_or_ignore(transaction: Any, record: InsightReviewRecord) -> Any:
    values = _insight_review_values(record)
    if transaction.dialect.name == "postgresql":
        return (
            postgres_insert(db.insight_reviews)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["tenant_id", "created_idempotency_key"])
            .returning(db.insight_reviews.c.id)
        )
    if transaction.dialect.name == "sqlite":
        return (
            sqlite_insert(db.insight_reviews)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["tenant_id", "created_idempotency_key"])
            .returning(db.insight_reviews.c.id)
        )
    return insert(db.insight_reviews).values(**values).returning(db.insight_reviews.c.id)
