"""Insight review and approved-action execution routes."""

from __future__ import annotations

from dataclasses import asdict
from typing import cast

from fastapi import APIRouter, Header, HTTPException, Query, Request
from foundry_lite.domain.errors import FoundryLiteError

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.request_context import _ctx
from foundry_lite_api.schemas import (
    ApprovalExecutionRequest,
    InsightReviewAssignRequest,
    InsightReviewCreateRequest,
    InsightReviewDecisionRequest,
    JsonObject,
)

router = APIRouter()


@router.get("/api/insights/reviews")
def list_insight_reviews(
    request: Request,
    status: str | None = Query(default=None),
    assignee_user_id: str | None = Query(default=None, alias="assigneeUserId"),
    limit: int = Query(default=50),
) -> JsonObject:
    try:
        return runtime.foundry.insights.list(
            ctx=_ctx(request),
            status=status,
            assignee_user_id=assignee_user_id,
            limit=limit,
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/insights/reviews")
def create_insight_review(
    request: Request,
    payload: InsightReviewCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.insights.create(
            claim_id=payload.claim_id,
            claim_text=payload.claim_text,
            evidence_object_ids=payload.evidence_object_ids,
            evidence_refs=payload.evidence_refs,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
            priority=payload.priority,
            assignee_user_id=payload.assignee_user_id,
            action_proposal=payload.action_proposal,
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/insights/reviews/{review_id}")
def get_insight_review(request: Request, review_id: str) -> JsonObject:
    try:
        return runtime.foundry.insights.get(review_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/insights/reviews/{review_id}/assign")
def assign_insight_review(
    request: Request,
    review_id: str,
    payload: InsightReviewAssignRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.insights.assign(
            review_id,
            assignee_user_id=payload.assignee_user_id,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/insights/reviews/{review_id}/decision")
def decide_insight_review(
    request: Request,
    review_id: str,
    payload: InsightReviewDecisionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.insights.decide(
            review_id,
            decision=payload.decision,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
            comment=payload.comment,
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/insights/reviews/{review_id}/execute-action")
@router.post("/api/insights/reviews/{review_id}/execute-approved-action")
def execute_approved_action(
    request: Request,
    review_id: str,
    payload: ApprovalExecutionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    if not idempotency_key:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "MISSING_IDEMPOTENCY_KEY",
                "request_id": getattr(getattr(request, "state", None), "request_id", None),
            },
        )
    try:
        result = runtime.foundry.aip.execute_approved_action(
            review_id=review_id,
            expected_proposal_fingerprint=payload.expected_proposal_fingerprint,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
        return cast(JsonObject, asdict(result))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc
