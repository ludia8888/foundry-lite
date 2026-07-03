"""Ontology proposal revision flow plus decision-time plan recomputation.

A submitted-but-undecided proposal can be revised in place by its original
submitter, so a UI can iterate on a draft (typo fixes, plan tweaks) without
withdraw-and-resubmit. The new yaml goes through the same validate black box
as submit (a blocked migration plan is stored and flagged, not refused), the
stored fingerprint gates stale drafts, and a conditional repository update
loses cleanly against a concurrent decide/withdraw/update. The decision-time
plan recomputation shares this module so the proposal service stays within
the application module size cap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.insight_review_repository import InsightReviewRow
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.ontology_proposal_payloads import (
    ProposalDecision,
    proposal_payload,
    proposal_yaml_text,
    require_fingerprint_match,
    require_proposal_updatable,
    required_text,
    revised_proposal_content,
    revision_record,
    yaml_fingerprint,
)
from foundry_lite.application.services.ontology_yaml import require_yaml_text_within_limit
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ConflictDetected,
    FoundryLiteError,
    PermissionDenied,
    ValidationFailed,
)

if TYPE_CHECKING:
    from foundry_lite.application.ports import OntologyValidationResult
    from foundry_lite.application.services.ontology_proposal_service import OntologyProposalService

_RESOURCE_TYPE = "ontology_proposal"


def update_ontology_proposal(
    service: OntologyProposalService,
    proposal_id: str,
    *,
    yaml_text: str,
    expected_fingerprint: str,
    title: str | None = None,
    description: str | None = None,
    ctx: RequestContext | None = None,
) -> dict[str, object]:
    """Revise a not-yet-decided proposal's yaml/title/description in place."""
    ctx = ctx or RequestContext()
    service.runtime_service._require_or_audit(ctx, "ontology:validate", _RESOURCE_TYPE, proposal_id)
    service._require_write_open(ctx, "update_ontology_proposal", proposal_id)
    required_text(expected_fingerprint, "expectedFingerprint")
    if title is not None:
        required_text(title, "title")
    require_yaml_text_within_limit(yaml_text)
    replay = _guard_update_or_replay(service, ctx, proposal_id, yaml_text, expected_fingerprint)
    if replay is not None:
        return replay
    # Same black box as submit: structural validity is required, a blocked
    # migration plan is stored (and flagged) so reviewers see what changed.
    validation = service.ontology_service.validate_yaml_text(yaml_text, ctx=ctx)
    return _store_revision(
        service,
        ctx,
        proposal_id,
        yaml_text=yaml_text,
        title=title,
        description=description,
        validation=validation,
        expected_fingerprint=expected_fingerprint,
    )


def decision_time_plan(
    service: OntologyProposalService,
    ctx: RequestContext,
    row: InsightReviewRow,
    decision: ProposalDecision,
) -> dict[str, object]:
    """Recompute the migration plan against the CURRENT active ontology."""
    try:
        validation = service.ontology_service.validate_yaml_text(proposal_yaml_text(row), ctx=ctx)
    except FoundryLiteError as exc:
        if decision == "approved":
            raise ValidationFailed(
                "ontology proposal no longer validates against the current active ontology",
                details={"proposal_id": row["id"], "error": exc.message, "errorDetails": dict(exc.details)},
            ) from exc
        return {"status": "validation_failed", "error": exc.message}
    plan = dict(validation["migration_plan"])
    if decision == "approved" and plan.get("blockedChanges"):
        raise ValidationFailed(
            "ontology proposal approval refused: migration plan is blocked at decision time",
            details={"proposal_id": row["id"], "migrationPlan": plan},
        )
    return plan


def _guard_update_or_replay(
    service: OntologyProposalService,
    ctx: RequestContext,
    proposal_id: str,
    yaml_text: str,
    expected_fingerprint: str,
) -> dict[str, object] | None:
    """Check the revision preconditions and short-circuit same-content replays."""
    with service.engine.begin() as conn:
        row = service._require_proposal_row(conn, ctx, proposal_id)
        _require_update_submitter(service, conn, ctx, row)
        require_proposal_updatable(row)
        if row["proposal_fingerprint"] == yaml_fingerprint(yaml_text):
            # Idempotent replay: this exact content is already stored.
            return proposal_payload(row)
        require_fingerprint_match(row, expected_fingerprint)
    return None


def _store_revision(
    service: OntologyProposalService,
    ctx: RequestContext,
    proposal_id: str,
    *,
    yaml_text: str,
    title: str | None,
    description: str | None,
    validation: OntologyValidationResult,
    expected_fingerprint: str,
) -> dict[str, object]:
    now = _now()
    with service.engine.begin() as conn:
        before = service._require_proposal_row(conn, ctx, proposal_id)
        claim_text, action_proposal = revised_proposal_content(
            before, yaml_text=yaml_text, title=title, description=description, validation=validation
        )
        after = service.insight_review_repository.update_proposal_content(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            review_id=proposal_id,
            expected_fingerprint=expected_fingerprint,
            proposal_fingerprint=yaml_fingerprint(yaml_text),
            claim_text=claim_text,
            action_proposal=action_proposal,
            revision=revision_record(ctx, before, now),
            updated_at=now,
        )
        if after is None:
            return _resolve_lost_update_race(service, conn, ctx, proposal_id, yaml_text, expected_fingerprint)
        service._audit_event(conn, ctx, "revised", after, before=before)
        return proposal_payload(after)


def _require_update_submitter(
    service: OntologyProposalService,
    conn: TransactionContext,
    ctx: RequestContext,
    row: InsightReviewRow,
) -> None:
    if row["created_by_user_id"] == ctx.actor_user_id:
        return
    service._audit_denied(conn, ctx, "ontology:proposal:update", row["id"])
    raise PermissionDenied(
        "only the submitter may update an ontology proposal",
        details={"proposal_id": row["id"], "submittedByUserId": row["created_by_user_id"]},
    )


def _resolve_lost_update_race(
    service: OntologyProposalService,
    conn: TransactionContext,
    ctx: RequestContext,
    proposal_id: str,
    yaml_text: str,
    expected_fingerprint: str,
) -> dict[str, object]:
    row = service._require_proposal_row(conn, ctx, proposal_id)
    if row["proposal_fingerprint"] == yaml_fingerprint(yaml_text):
        # A concurrent identical update already stored this content.
        return proposal_payload(row)
    require_proposal_updatable(row)
    require_fingerprint_match(row, expected_fingerprint)
    raise ConflictDetected(
        "ontology proposal changed concurrently during update",
        details={"proposal_id": proposal_id},
    )
