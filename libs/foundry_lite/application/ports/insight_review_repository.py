from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict

from foundry_lite.application.ports.transaction_context import TransactionContext

InsightReviewStatus = Literal["pending", "approved", "rejected"]
InsightReviewDecision = Literal["approved", "rejected"]
InsightReviewPriority = Literal["low", "normal", "high"]
InsightReviewJson = Mapping[str, object]


class InsightReviewRow(TypedDict):
    """Persisted insight review row for frontend review workspace APIs."""

    id: str
    tenant_id: str
    claim_id: str
    claim_text: str
    status: str
    priority: str
    assignee_user_id: str | None
    evidence_object_ids: Sequence[str]
    evidence_refs: Sequence[InsightReviewJson]
    action_proposal: InsightReviewJson | None
    created_by_user_id: str
    created_idempotency_key: str
    assignment_idempotency_key: str | None
    decision: InsightReviewJson | None
    decision_idempotency_key: str | None
    review_metadata: InsightReviewJson
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class InsightReviewRecord:
    review_id: str
    tenant_id: str
    claim_id: str
    claim_text: str
    status: InsightReviewStatus
    priority: InsightReviewPriority
    assignee_user_id: str | None
    evidence_object_ids: Sequence[str]
    evidence_refs: Sequence[InsightReviewJson]
    action_proposal: InsightReviewJson | None
    created_by_user_id: str
    created_idempotency_key: str
    assignment_idempotency_key: str | None
    decision: InsightReviewJson | None
    decision_idempotency_key: str | None
    review_metadata: InsightReviewJson
    created_at: str
    updated_at: str


class InsightReviewRepository(Protocol):
    """DB boundary for insight review queue and human review state."""

    def insert_review_or_get_existing(
        self,
        *,
        transaction: TransactionContext,
        record: InsightReviewRecord,
    ) -> InsightReviewRow | None:
        """Persist a review, or return the existing row for the same idempotency key."""
        ...

    def review_by_id(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        review_id: str,
    ) -> InsightReviewRow | None:
        """Return one tenant-scoped insight review."""
        ...

    def list_reviews(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        status: str | None,
        assignee_user_id: str | None,
        limit: int,
    ) -> list[InsightReviewRow]:
        """Return a bounded review queue page for one tenant."""
        ...

    def assign_review(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        review_id: str,
        assignee_user_id: str,
        assignment_idempotency_key: str,
        updated_at: str,
    ) -> InsightReviewRow | None:
        """Assign a pending review if it has not been terminally decided."""
        ...

    def decide_review(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        review_id: str,
        status: InsightReviewDecision,
        decision: InsightReviewJson,
        decision_idempotency_key: str,
        updated_at: str,
    ) -> InsightReviewRow | None:
        """Approve or reject a pending review if this caller wins the race."""
        ...
