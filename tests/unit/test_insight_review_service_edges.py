from __future__ import annotations

import pytest
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import ConflictDetected, NotFound, PermissionDenied, ValidationFailed
from foundry_lite.infrastructure import schema as db
from sqlalchemy import select


def test_insight_review_idempotent_replays_and_read_filters(foundry) -> None:
    ctx = demo_admin_context()
    created = _create_review(foundry, ctx=ctx, claim_id="claim-replay", idempotency_key="create-replay")

    detail = foundry.insights.get(str(created["id"]), ctx=ctx)
    pending = foundry.insights.list(ctx=ctx, status="pending", limit=5)
    assigned = foundry.insights.assign(
        str(created["id"]),
        assignee_user_id="ops-reviewer",
        idempotency_key="assign-replay",
        ctx=ctx,
    )
    replayed_assignment = foundry.insights.assign(
        str(created["id"]),
        assignee_user_id="ops-reviewer",
        idempotency_key="assign-replay",
        ctx=ctx,
    )
    decided = foundry.insights.decide(
        str(created["id"]),
        decision="approved",
        idempotency_key="decision-replay",
        ctx=ctx,
        comment="Evidence checked",
    )
    replayed_decision = foundry.insights.decide(
        str(created["id"]),
        decision="approved",
        idempotency_key="decision-replay",
        ctx=ctx,
        comment="Evidence checked",
    )
    approved = foundry.insights.list(ctx=ctx, status="approved", assignee_user_id="ops-reviewer", limit=10)

    assert detail["id"] == created["id"]
    assert [item["id"] for item in pending["items"]] == [created["id"]]
    assert replayed_assignment == assigned
    assert replayed_decision == decided
    assert [item["id"] for item in approved["items"]] == [created["id"]]


def test_insight_review_validation_and_terminal_guards(foundry) -> None:
    ctx = demo_admin_context()

    with pytest.raises(ValidationFailed, match="invalid insight review priority"):
        _create_review(
            foundry,
            ctx=ctx,
            claim_id="claim-bad-priority",
            idempotency_key="create-bad-priority",
            priority="urgent",
        )
    with pytest.raises(ValidationFailed, match="requires evidence"):
        foundry.insights.create(
            claim_id="claim-no-evidence",
            claim_text="Missing evidence should be rejected",
            evidence_object_ids=[],
            evidence_refs=[],
            idempotency_key="create-no-evidence",
            ctx=ctx,
        )

    created = _create_review(foundry, ctx=ctx, claim_id="claim-terminal", idempotency_key="create-terminal")
    with pytest.raises(NotFound, match="insight review not found"):
        foundry.insights.get("missing-review", ctx=ctx)
    with pytest.raises(ValidationFailed, match="invalid insight review status"):
        foundry.insights.list(ctx=ctx, status="queued")
    with pytest.raises(ValidationFailed, match="limit must be between"):
        foundry.insights.list(ctx=ctx, limit=0)
    with pytest.raises(ValidationFailed, match="invalid insight review decision"):
        foundry.insights.decide(str(created["id"]), decision="maybe", idempotency_key="decision-bad", ctx=ctx)

    foundry.insights.decide(
        str(created["id"]),
        decision="approved",
        idempotency_key="decision-terminal",
        ctx=ctx,
    )
    with pytest.raises(ConflictDetected, match="already decided"):
        foundry.insights.assign(
            str(created["id"]),
            assignee_user_id="late-reviewer",
            idempotency_key="assign-after-decision",
            ctx=ctx,
        )


def test_insight_review_permission_denied_is_audited_as_deny(foundry) -> None:
    viewer = RequestContext(actor_user_id="viewer-user", roles=("viewer",), request_id="req-insight-deny")

    with pytest.raises(PermissionDenied, match="insight:create"):
        _create_review(foundry, ctx=viewer, claim_id="claim-denied", idempotency_key="create-denied")

    with foundry.engine.begin() as conn:
        row = (
            conn.execute(
                select(
                    db.audit_events.c.action,
                    db.audit_events.c.decision,
                    db.audit_events.c.resource_id,
                    db.audit_events.c.after_ref,
                ).where(db.audit_events.c.event_type == "permission.denied")
            )
            .mappings()
            .one()
        )

    assert row["action"] == "insight:create"
    assert row["decision"] == "deny"
    assert row["resource_id"] == "claim-denied"
    assert row["after_ref"]["permission"] == "insight:create"


def _create_review(
    foundry,
    *,
    ctx: RequestContext,
    claim_id: str,
    idempotency_key: str,
    priority: str = "normal",
) -> dict[str, object]:
    return foundry.insights.create(
        claim_id=claim_id,
        claim_text="Supplier risk increased",
        evidence_object_ids=["evidence-1"],
        evidence_refs=[{"evidenceId": "evidence-1", "sourceObjectId": "O-1001"}],
        idempotency_key=idempotency_key,
        ctx=ctx,
        priority=priority,
        action_proposal={"actionApiName": "ApproveOrder", "targetObjectId": "O-1001"},
    )
