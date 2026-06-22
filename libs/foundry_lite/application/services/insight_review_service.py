from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal, cast

from foundry_lite.application.ports import AuditEventRecord, TransactionContext
from foundry_lite.application.ports.insight_review_repository import (
    InsightReviewDecision,
    InsightReviewPriority,
    InsightReviewRecord,
    InsightReviewRow,
)
from foundry_lite.application.primitives import _json_hash, _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.runtime_restore_gates import require_write_traffic_open
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, PermissionDenied, ValidationFailed

InsightReviewEvent = Literal["created", "assigned", "approved", "rejected"]
ALLOWED_STATUS = frozenset({"pending", "approved", "rejected"})
ALLOWED_PRIORITY = frozenset({"low", "normal", "high"})


class InsightReviewService(CoreService):
    """Application service for durable human review of AI/insight claims."""

    required_dependencies = ("engine", "policy", "insight_review_repository", "runtime_repository")
    required_collaborators = ()

    def create_review(
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
        ctx = ctx or RequestContext()
        self._require_or_audit(ctx, "insight:create", "insight_review", claim_id)
        self._require_write_open(ctx, operation="create_review", resource_id=claim_id)
        now = _now()
        record = _review_record(
            ctx,
            claim_id=claim_id,
            claim_text=claim_text,
            evidence_object_ids=evidence_object_ids,
            evidence_refs=evidence_refs,
            idempotency_key=_required_idempotency_key(idempotency_key),
            priority=_priority(priority),
            assignee_user_id=assignee_user_id,
            action_proposal=action_proposal,
            now=now,
        )
        with self.engine.begin() as conn:
            existing = self.insight_review_repository.insert_review_or_get_existing(transaction=conn, record=record)
            if existing is not None:
                _ensure_create_replay_matches(existing, record)
            row = existing or self._require_row(conn, ctx, record.review_id)
            if existing is None:
                self._audit(conn, ctx, "created", row, after_ref=_review_payload(row))
            return _review_payload(row)

    def list_reviews(
        self,
        *,
        ctx: RequestContext | None = None,
        status: str | None = None,
        assignee_user_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "insight:read")
        with self.engine.begin() as conn:
            rows = self.insight_review_repository.list_reviews(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                status=_optional_status(status),
                assignee_user_id=assignee_user_id,
                limit=_limit(limit),
            )
        return {"items": [_review_payload(row) for row in rows]}

    def review_detail(self, review_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "insight:read")
        with self.engine.begin() as conn:
            return _review_payload(self._require_row(conn, ctx, review_id))

    def assign_review(
        self,
        review_id: str,
        *,
        assignee_user_id: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self._require_or_audit(ctx, "insight:review", "insight_review", review_id)
        self._require_write_open(ctx, operation="assign_review", resource_id=review_id)
        key = _required_idempotency_key(idempotency_key)
        with self.engine.begin() as conn:
            before = self._require_pending_or_idempotent_assignment(conn, ctx, review_id, key)
            if before.get("assignment_idempotency_key") == key:
                return _review_payload(before)
            after = self.insight_review_repository.assign_review(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                review_id=review_id,
                assignee_user_id=_required_text(assignee_user_id, "assigneeUserId"),
                assignment_idempotency_key=key,
                updated_at=_now(),
            )
            row = self._resolve_update_result(conn, ctx, review_id, after, key, "assignment")
            self._audit(conn, ctx, "assigned", row, before_ref=_review_payload(before), after_ref=_review_payload(row))
            return _review_payload(row)

    def decide_review(
        self,
        review_id: str,
        *,
        decision: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
        comment: str | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self._require_or_audit(ctx, "insight:review", "insight_review", review_id)
        self._require_write_open(ctx, operation="decide_review", resource_id=review_id)
        key = _required_idempotency_key(idempotency_key)
        parsed = _decision(decision)
        with self.engine.begin() as conn:
            before = self._require_pending_or_idempotent_decision(conn, ctx, review_id, key)
            if before.get("decision_idempotency_key") == key:
                return _review_payload(before)
            payload = _decision_payload(ctx, parsed, key, comment)
            after = self.insight_review_repository.decide_review(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                review_id=review_id,
                status=parsed,
                decision=payload,
                decision_idempotency_key=key,
                updated_at=str(payload["decidedAt"]),
            )
            row = self._resolve_update_result(conn, ctx, review_id, after, key, "decision")
            self._audit(conn, ctx, parsed, row, before_ref=_review_payload(before), after_ref=_review_payload(row))
            return _review_payload(row)

    def _require_row(self, conn: TransactionContext, ctx: RequestContext, review_id: str) -> InsightReviewRow:
        row = self.insight_review_repository.review_by_id(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            review_id=review_id,
        )
        if row is None:
            raise NotFound("insight review not found", details={"review_id": review_id})
        return row

    def _require_or_audit(
        self,
        ctx: RequestContext,
        permission: str,
        resource_type: str,
        resource_id: str,
    ) -> None:
        try:
            self.policy.require(ctx, permission)
        except PermissionDenied:
            with self.engine.begin() as conn:
                self._audit_permission_denied(conn, ctx, permission, resource_type, resource_id)
            raise

    def _require_write_open(self, ctx: RequestContext, *, operation: str, resource_id: str) -> None:
        require_write_traffic_open(
            self.engine,
            self.runtime_repository,
            ctx,
            operation=operation,
            resource_type="insight_review",
            resource_id=resource_id,
        )

    def _require_pending_or_idempotent_assignment(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        review_id: str,
        idempotency_key: str,
    ) -> InsightReviewRow:
        row = self._require_row(conn, ctx, review_id)
        if row.get("assignment_idempotency_key") == idempotency_key:
            return row
        _ensure_pending(row, review_id)
        return row

    def _require_pending_or_idempotent_decision(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        review_id: str,
        idempotency_key: str,
    ) -> InsightReviewRow:
        row = self._require_row(conn, ctx, review_id)
        if row.get("decision_idempotency_key") == idempotency_key:
            return row
        _ensure_pending(row, review_id)
        return row

    def _resolve_update_result(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        review_id: str,
        updated: InsightReviewRow | None,
        idempotency_key: str,
        idempotency_field: Literal["assignment", "decision"],
    ) -> InsightReviewRow:
        row = updated or self._require_row(conn, ctx, review_id)
        field = f"{idempotency_field}_idempotency_key"
        if row.get(field) == idempotency_key:
            return row
        raise ConflictDetected("insight review changed concurrently", details={"review_id": review_id})

    def _audit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        event: InsightReviewEvent,
        row: InsightReviewRow,
        *,
        before_ref: Mapping[str, object] | None = None,
        after_ref: Mapping[str, object] | None = None,
    ) -> None:
        self.runtime_repository.insert_audit_event(
            transaction=conn,
            record=_audit_event(ctx, event, row, before_ref=before_ref, after_ref=after_ref),
        )

    def _audit_permission_denied(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        permission: str,
        resource_type: str,
        resource_id: str,
    ) -> None:
        self.runtime_repository.insert_audit_event(
            transaction=conn,
            record=_permission_denied_event(ctx, permission, resource_type, resource_id),
        )


def _review_record(
    ctx: RequestContext,
    *,
    claim_id: str,
    claim_text: str,
    evidence_object_ids: Sequence[str],
    evidence_refs: Sequence[Mapping[str, object]],
    idempotency_key: str,
    priority: InsightReviewPriority,
    assignee_user_id: str | None,
    action_proposal: Mapping[str, object] | None,
    now: str,
) -> InsightReviewRecord:
    _validate_claim_inputs(claim_id, claim_text, evidence_object_ids, evidence_refs)
    return InsightReviewRecord(
        review_id=_new_id("insight_review"),
        tenant_id=ctx.tenant_id,
        claim_id=claim_id,
        claim_text=claim_text,
        status="pending",
        priority=priority,
        assignee_user_id=assignee_user_id,
        evidence_object_ids=list(evidence_object_ids),
        evidence_refs=[dict(ref) for ref in evidence_refs],
        action_proposal=dict(action_proposal) if action_proposal is not None else None,
        created_by_user_id=ctx.actor_user_id,
        created_idempotency_key=idempotency_key,
        assignment_idempotency_key=None,
        decision=None,
        decision_idempotency_key=None,
        review_metadata={"requestId": ctx.request_id},
        created_at=now,
        updated_at=now,
    )


def _validate_claim_inputs(
    claim_id: str,
    claim_text: str,
    evidence_object_ids: Sequence[str],
    evidence_refs: Sequence[Mapping[str, object]],
) -> None:
    _required_text(claim_id, "claimId")
    _required_text(claim_text, "claimText")
    if not evidence_object_ids or not evidence_refs:
        raise ValidationFailed("insight review requires evidence objects")


def _review_payload(row: InsightReviewRow) -> dict[str, object]:
    return {
        "id": row["id"],
        "claimId": row["claim_id"],
        "claimText": row["claim_text"],
        "status": row["status"],
        "priority": row["priority"],
        "assigneeUserId": row["assignee_user_id"],
        "evidenceObjectIds": list(row["evidence_object_ids"]),
        "evidenceRefs": [dict(ref) for ref in row["evidence_refs"]],
        "actionProposal": dict(row["action_proposal"]) if row["action_proposal"] is not None else None,
        "createdByUserId": row["created_by_user_id"],
        "decision": dict(row["decision"]) if row["decision"] is not None else None,
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _ensure_create_replay_matches(existing: InsightReviewRow, record: InsightReviewRecord) -> None:
    existing_fingerprint = _review_create_fingerprint(
        claim_id=existing["claim_id"],
        claim_text=existing["claim_text"],
        evidence_object_ids=existing["evidence_object_ids"],
        evidence_refs=existing["evidence_refs"],
        priority=str(existing["priority"]),
        assignee_user_id=existing["assignee_user_id"],
        action_proposal=existing["action_proposal"],
    )
    requested_fingerprint = _review_create_fingerprint(
        claim_id=record.claim_id,
        claim_text=record.claim_text,
        evidence_object_ids=record.evidence_object_ids,
        evidence_refs=record.evidence_refs,
        priority=record.priority,
        assignee_user_id=record.assignee_user_id,
        action_proposal=record.action_proposal,
    )
    if existing_fingerprint == requested_fingerprint:
        return
    raise ConflictDetected(
        "idempotency key reused with different insight review payload",
        details={
            "idempotency_key": record.created_idempotency_key,
            "existing_review_id": existing["id"],
        },
    )


def _review_create_fingerprint(
    *,
    claim_id: str,
    claim_text: str,
    evidence_object_ids: Sequence[str],
    evidence_refs: Sequence[Mapping[str, object]],
    priority: str,
    assignee_user_id: str | None,
    action_proposal: Mapping[str, object] | None,
) -> str:
    return _json_hash(
        {
            "claimId": claim_id,
            "claimText": claim_text,
            "evidenceObjectIds": list(evidence_object_ids),
            "evidenceRefs": [dict(ref) for ref in evidence_refs],
            "priority": priority,
            "assigneeUserId": assignee_user_id,
            "actionProposal": dict(action_proposal) if action_proposal is not None else None,
        }
    )


def _audit_event(
    ctx: RequestContext,
    event: InsightReviewEvent,
    row: InsightReviewRow,
    *,
    before_ref: Mapping[str, object] | None,
    after_ref: Mapping[str, object] | None,
) -> AuditEventRecord:
    return _audit_record(
        ctx,
        event_type=f"insight_review.{event}",
        resource_id=row["id"],
        action="insight:review" if event != "created" else "insight:create",
        before_ref=before_ref,
        after_ref=after_ref,
        metadata={"status": row["status"], "claimId": row["claim_id"]},
    )


def _permission_denied_event(
    ctx: RequestContext,
    permission: str,
    resource_type: str,
    resource_id: str,
) -> AuditEventRecord:
    return _audit_record(
        ctx,
        event_type="permission.denied",
        resource_type=resource_type,
        resource_id=resource_id,
        action=permission,
        decision="deny",
        after_ref={"permission": permission},
    )


def _audit_record(
    ctx: RequestContext,
    *,
    event_type: str,
    resource_id: str,
    action: str,
    resource_type: str = "insight_review",
    decision: str = "allow",
    before_ref: Mapping[str, object] | None = None,
    after_ref: Mapping[str, object] | None = None,
    metadata: Mapping[str, object] | None = None,
) -> AuditEventRecord:
    return AuditEventRecord(
        event_id=_new_id("audit"),
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.actor_user_id,
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
        decision=decision,
        policy_decision={},
        before_ref=dict(before_ref or {}),
        after_ref=dict(after_ref or {}),
        correlation_id=ctx.request_id,
        request_id=ctx.request_id,
        metadata=dict(metadata or {}),
        created_at=_now(),
    )


def _decision_payload(
    ctx: RequestContext,
    decision: InsightReviewDecision,
    idempotency_key: str,
    comment: str | None,
) -> dict[str, object]:
    return {
        "decision": decision,
        "comment": comment,
        "decidedByUserId": ctx.actor_user_id,
        "decidedAt": _now(),
        "idempotencyKey": idempotency_key,
    }


def _ensure_pending(row: InsightReviewRow, review_id: str) -> None:
    if row["status"] != "pending":
        raise ConflictDetected("insight review is already decided", details={"review_id": review_id})


def _optional_status(status: str | None) -> str | None:
    if status is None:
        return None
    if status not in ALLOWED_STATUS:
        raise ValidationFailed("invalid insight review status", details={"status": status})
    return status


def _decision(decision: str) -> InsightReviewDecision:
    if decision not in {"approved", "rejected"}:
        raise ValidationFailed("invalid insight review decision", details={"decision": decision})
    return cast(InsightReviewDecision, decision)


def _priority(priority: str) -> InsightReviewPriority:
    if priority not in ALLOWED_PRIORITY:
        raise ValidationFailed("invalid insight review priority", details={"priority": priority})
    return cast(InsightReviewPriority, priority)


def _limit(limit: int) -> int:
    if limit < 1 or limit > 200:
        raise ValidationFailed("limit must be between 1 and 200", details={"limit": limit})
    return limit


def _required_idempotency_key(idempotency_key: str) -> str:
    return _required_text(idempotency_key, "Idempotency-Key")


def _required_text(value: str, name: str) -> str:
    if not value:
        raise ValidationFailed(f"{name} is required")
    return value
