from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.services.insight_review_service import InsightReviewService
from foundry_lite.domain.context import RequestContext


class InsightReviewWorkspace:
    """Facade for frontend-facing insight review queue workflows."""

    def __init__(self, service: InsightReviewService) -> None:
        self._service = service

    def create(
        self,
        *,
        claim_id: str,
        claim_text: str,
        evidence_object_ids: Sequence[str],
        evidence_refs: Sequence[Mapping[str, object]],
        idempotency_key: str,
        ctx: RequestContext | None = None,
        priority: str = "normal",
        assignee_user_id: str | None = None,
        action_proposal: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        return self._service.create_review(
            claim_id=claim_id,
            claim_text=claim_text,
            evidence_object_ids=evidence_object_ids,
            evidence_refs=evidence_refs,
            idempotency_key=idempotency_key,
            ctx=ctx,
            priority=priority,
            assignee_user_id=assignee_user_id,
            action_proposal=action_proposal,
        )

    def list(
        self,
        *,
        ctx: RequestContext | None = None,
        status: str | None = None,
        assignee_user_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, object]:
        return self._service.list_reviews(
            ctx=ctx,
            status=status,
            assignee_user_id=assignee_user_id,
            limit=limit,
        )

    def get(self, review_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._service.review_detail(review_id, ctx=ctx)

    def assign(
        self,
        review_id: str,
        *,
        assignee_user_id: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._service.assign_review(
            review_id,
            assignee_user_id=assignee_user_id,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def decide(
        self,
        review_id: str,
        *,
        decision: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
        comment: str | None = None,
    ) -> dict[str, object]:
        return self._service.decide_review(
            review_id,
            decision=decision,
            idempotency_key=idempotency_key,
            ctx=ctx,
            comment=comment,
        )
