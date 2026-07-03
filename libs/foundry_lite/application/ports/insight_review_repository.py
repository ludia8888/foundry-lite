"""Application port contract for insight review repository."""

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
    proposal_type: str | None
    proposal_fingerprint: str | None
    originating_ai_run_id: str | None
    originating_tool_call_id: str | None
    expires_at: str | None
    execution_status: str | None
    approved_action_run_id: str | None
    approval_policy_version: str | None
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
    proposal_type: str | None
    proposal_fingerprint: str | None
    originating_ai_run_id: str | None
    originating_tool_call_id: str | None
    expires_at: str | None
    execution_status: str | None
    approved_action_run_id: str | None
    approval_policy_version: str | None
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

    def review_by_execution_idempotency_key(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        idempotency_key: str,
    ) -> InsightReviewRow | None:
        """Return the review that owns one approval-execution idempotency key."""
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

    def list_proposal_reviews(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        proposal_type: str,
        status: str | None,
        execution_statuses: Sequence[str] | None,
        is_assigned: bool | None,
        created_before: str | None,
        before_id: str | None,
        limit: int,
    ) -> list[InsightReviewRow]:
        """Return a keyset page of one proposal type, newest first.

        ``created_before``/``before_id`` continue a page strictly after the
        cursor row in ``(created_at DESC, id DESC)`` order.
        """
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

    def mark_execution_started(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        review_id: str,
        execution_idempotency_key: str,
        execution_request_fingerprint: str,
        proposal_fingerprint: str,
        updated_at: str,
    ) -> InsightReviewRow | None:
        """Move an approved action proposal review into executing state."""
        ...

    def mark_execution_succeeded(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        review_id: str,
        action_run_id: str,
        updated_at: str,
        metadata: InsightReviewJson | None = None,
    ) -> InsightReviewRow | None:
        """Link an approved review to the run/version it produced.

        ``metadata`` entries are merged into ``review_metadata`` so callers can
        persist execution results (for example the applied ontology version).
        """
        ...

    def update_proposal_content(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        review_id: str,
        expected_fingerprint: str,
        proposal_fingerprint: str,
        claim_text: str,
        action_proposal: InsightReviewJson,
        revision: InsightReviewJson,
        updated_at: str,
    ) -> InsightReviewRow | None:
        """Revise a still-pending, undecided proposal's content in place.

        The conditional update only applies while the review status is
        ``pending`` (which implies undecided: decisions move status and
        decision together), execution status is ``pending_review`` (not
        withdrawn), and the row still carries ``expected_fingerprint``; a
        concurrent decide/withdraw/update loses cleanly with ``None``. The
        ``revision`` entry is merged into ``review_metadata`` so reviewers can
        see that content changed after assignment.
        """
        ...

    def withdraw_review(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        review_id: str,
        withdrawal: InsightReviewJson,
        updated_at: str,
    ) -> InsightReviewRow | None:
        """Terminate a not-yet-executed review because its creator withdrew it."""
        ...

    def mark_execution_failed(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        review_id: str,
        error: InsightReviewJson,
        updated_at: str,
    ) -> InsightReviewRow | None:
        """Record that approval execution failed after the execution claim was taken."""
        ...
