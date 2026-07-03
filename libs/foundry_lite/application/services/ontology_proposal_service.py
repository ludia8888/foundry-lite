"""Ontology change proposal service: review-then-apply governance for ontology YAML.

A submitter records the YAML plus its migration plan, a *different* reviewer
approves against the current active ontology, and only an approved proposal is
executed through the same validate/apply black boxes the direct endpoint uses.
Proposals persist in the reusable insight-review storage (idempotent create,
CAS assign/decide, exactly-once execution)."""

from __future__ import annotations

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.insight_review_repository import InsightReviewRow
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.ontology_proposal_payloads import (
    ONTOLOGY_PROPOSAL_TYPE,
    PROPOSAL_EVENT_ACTIONS,
    ProposalDecision,
    ProposalEvent,
    bounded_limit,
    decision_record,
    decision_replay,
    decode_proposal_cursor,
    encode_proposal_cursor,
    ensure_submit_replay_matches,
    parse_decision,
    proposal_audit_ref,
    proposal_detail_payload,
    proposal_list_filters,
    proposal_payload,
    proposal_record,
    proposal_status,
    proposal_yaml_text,
    require_fingerprint_match,
    required_text,
    withdrawal_record,
)
from foundry_lite.application.services.ontology_protocols import OntologyRuntimeBoundary
from foundry_lite.application.services.ontology_service import OntologyService
from foundry_lite.application.services.ontology_yaml import require_yaml_text_within_limit
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ConflictDetected,
    FoundryLiteError,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)

_RESOURCE_TYPE = "ontology_proposal"


class OntologyProposalService(CoreService):
    """Submit, review, and apply ontology change proposals with drift protection."""

    required_dependencies = ("engine", "policy", "insight_review_repository")
    required_collaborators = ("ontology_service", "runtime_service")
    ontology_service: OntologyService
    runtime_service: OntologyRuntimeBoundary

    def submit_proposal(
        self,
        *,
        yaml_text: str,
        title: str,
        idempotency_key: str,
        description: str | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "ontology:validate", _RESOURCE_TYPE, "draft")
        self._require_write_open(ctx, "submit_ontology_proposal", "draft")
        key = required_text(idempotency_key, "Idempotency-Key")
        required_text(title, "title")
        require_yaml_text_within_limit(yaml_text)
        # Structural validation is required; a blocked migration plan is not.
        # The plan is stored so reviewers see exactly what would change.
        validation = self.ontology_service.validate_yaml_text(yaml_text, ctx=ctx)
        record = proposal_record(
            ctx,
            title=title,
            description=description,
            yaml_text=yaml_text,
            validation=validation,
            idempotency_key=key,
            now=_now(),
        )
        with self.engine.begin() as conn:
            existing = self.insight_review_repository.insert_review_or_get_existing(transaction=conn, record=record)
            if existing is not None:
                ensure_submit_replay_matches(existing, record)
                return proposal_payload(existing)
            row = self._require_proposal_row(conn, ctx, record.review_id)
            self._audit_event(conn, ctx, "submitted", row)
            return proposal_payload(row)

    def assign_reviewer(
        self,
        proposal_id: str,
        *,
        reviewer_user_id: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "ontology:activate", _RESOURCE_TYPE, proposal_id)
        self._require_write_open(ctx, "assign_ontology_proposal", proposal_id)
        reviewer = required_text(reviewer_user_id, "reviewerUserId")
        key = f"{proposal_id}:assign:{reviewer}"
        with self.engine.begin() as conn:
            before = self._require_proposal_row(conn, ctx, proposal_id)
            if before.get("assignment_idempotency_key") == key:
                return proposal_payload(before)
            after = self.insight_review_repository.assign_review(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                review_id=proposal_id,
                assignee_user_id=reviewer,
                assignment_idempotency_key=key,
                updated_at=_now(),
            )
            if after is None:
                raise ConflictDetected(
                    "ontology proposal is no longer awaiting review",
                    details={"proposal_id": proposal_id, "status": proposal_status(before)},
                )
            self._audit_event(conn, ctx, "assigned", after, before=before)
            return proposal_payload(after)

    def decide_proposal(
        self,
        proposal_id: str,
        *,
        decision: str,
        expected_fingerprint: str,
        comment: str | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "ontology:activate", _RESOURCE_TYPE, proposal_id)
        self._require_write_open(ctx, "decide_ontology_proposal", proposal_id)
        parsed = parse_decision(decision)
        with self.engine.begin() as conn:
            row = self._require_proposal_row(conn, ctx, proposal_id)
            require_fingerprint_match(row, expected_fingerprint)
            replay = decision_replay(row, parsed, comment)
            if replay is not None:
                return replay
            self._require_decidable(conn, ctx, row, parsed)
        # The plan may have drifted since submit: recompute it against the
        # CURRENT active ontology outside the storage transaction (the validate
        # black box owns its own transaction) and store the decision-time plan.
        plan = self._decision_time_plan(ctx, row, parsed)
        return self._store_decision(ctx, proposal_id, parsed, comment, plan)

    def execute_proposal(
        self,
        proposal_id: str,
        *,
        expected_fingerprint: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "ontology:activate", _RESOURCE_TYPE, proposal_id)
        self._require_write_open(ctx, "execute_ontology_proposal", proposal_id)
        replay, yaml_text = self._claim_execution(ctx, proposal_id, expected_fingerprint)
        if replay is not None:
            return replay
        try:
            applied = self.ontology_service.apply_ontology_text(yaml_text, ctx=ctx)
        except Exception as exc:
            self._record_execution_failure(ctx, proposal_id, exc)
            raise
        return self._finish_execution(ctx, proposal_id, dict(applied))

    def withdraw_proposal(
        self,
        proposal_id: str,
        *,
        reason: str | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "ontology:validate", _RESOURCE_TYPE, proposal_id)
        self._require_write_open(ctx, "withdraw_ontology_proposal", proposal_id)
        with self.engine.begin() as conn:
            before = self._require_proposal_row(conn, ctx, proposal_id)
            self._require_submitter(conn, ctx, before)
            if proposal_status(before) == "withdrawn":
                return proposal_payload(before)
            after = self.insight_review_repository.withdraw_review(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                review_id=proposal_id,
                withdrawal=withdrawal_record(ctx, reason, _now()),
                updated_at=_now(),
            )
            if after is None:
                raise ConflictDetected(
                    "ontology proposal can no longer be withdrawn",
                    details={"proposal_id": proposal_id, "status": proposal_status(before)},
                )
            self._audit_event(conn, ctx, "withdrawn", after, before=before)
            return proposal_payload(after)

    def list_proposals(
        self,
        *,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "ontology:validate")
        filters = proposal_list_filters(status)
        page = decode_proposal_cursor(cursor)
        bounded = bounded_limit(limit)
        with self.engine.begin() as conn:
            rows = self.insight_review_repository.list_proposal_reviews(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                proposal_type=ONTOLOGY_PROPOSAL_TYPE,
                status=filters.status,
                execution_statuses=filters.execution_statuses,
                is_assigned=filters.is_assigned,
                created_before=page.created_before,
                before_id=page.before_id,
                limit=bounded,
            )
        next_cursor = encode_proposal_cursor(rows[-1]) if len(rows) == bounded else None
        return {"items": [proposal_payload(row) for row in rows], "nextCursor": next_cursor}

    def get_proposal(self, proposal_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "ontology:validate")
        with self.engine.begin() as conn:
            row = self._require_proposal_row(conn, ctx, proposal_id)
        return proposal_detail_payload(row)

    def _decision_time_plan(
        self,
        ctx: RequestContext,
        row: InsightReviewRow,
        decision: ProposalDecision,
    ) -> dict[str, object]:
        try:
            validation = self.ontology_service.validate_yaml_text(proposal_yaml_text(row), ctx=ctx)
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

    def _store_decision(
        self,
        ctx: RequestContext,
        proposal_id: str,
        decision: ProposalDecision,
        comment: str | None,
        plan: dict[str, object],
    ) -> dict[str, object]:
        key = f"{proposal_id}:decision"
        payload = decision_record(
            ctx, decision=decision, comment=comment, idempotency_key=key, migration_plan=plan, now=_now()
        )
        with self.engine.begin() as conn:
            before = self._require_proposal_row(conn, ctx, proposal_id)
            after = self.insight_review_repository.decide_review(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                review_id=proposal_id,
                status=decision,
                decision=payload,
                decision_idempotency_key=key,
                updated_at=str(payload["decidedAt"]),
            )
            if after is None:
                return self._resolve_lost_decision_race(conn, ctx, proposal_id, decision, comment)
            self._audit_event(conn, ctx, "decided", after, before=before)
            return proposal_payload(after)

    def _resolve_lost_decision_race(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        proposal_id: str,
        decision: ProposalDecision,
        comment: str | None,
    ) -> dict[str, object]:
        row = self._require_proposal_row(conn, ctx, proposal_id)
        replay = decision_replay(row, decision, comment)
        if replay is not None:
            return replay
        raise ConflictDetected(
            "ontology proposal was decided concurrently",
            details={"proposal_id": proposal_id, "status": proposal_status(row)},
        )

    def _claim_execution(
        self,
        ctx: RequestContext,
        proposal_id: str,
        expected_fingerprint: str,
    ) -> tuple[dict[str, object] | None, str]:
        with self.engine.begin() as conn:
            row = self._require_proposal_row(conn, ctx, proposal_id)
            require_fingerprint_match(row, expected_fingerprint)
            if row["execution_status"] == "executed":
                return proposal_payload(row), ""
            if proposal_status(row) != "approved":
                raise ConflictDetected(
                    "only approved ontology proposals can be executed",
                    details={"proposal_id": proposal_id, "status": proposal_status(row)},
                )
            fingerprint = str(row["proposal_fingerprint"])
            claimed = self.insight_review_repository.mark_execution_started(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                review_id=proposal_id,
                execution_idempotency_key=f"{proposal_id}:execute",
                execution_request_fingerprint=fingerprint,
                proposal_fingerprint=fingerprint,
                updated_at=_now(),
            )
            if claimed is None:
                raise ConflictDetected(
                    "ontology proposal execution was already claimed",
                    details={"proposal_id": proposal_id, "executionStatus": row["execution_status"]},
                )
            return None, proposal_yaml_text(claimed)

    def _finish_execution(
        self,
        ctx: RequestContext,
        proposal_id: str,
        applied: dict[str, object],
    ) -> dict[str, object]:
        applied_info: dict[str, object] = {
            "ontologyVersionId": applied["ontology_version_id"],
            "versionNumber": applied["version_number"],
            "migrationPlan": applied["migration_plan"],
            "at": _now(),
            "appliedByUserId": ctx.actor_user_id,
        }
        with self.engine.begin() as conn:
            row = self.insight_review_repository.mark_execution_succeeded(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                review_id=proposal_id,
                action_run_id=str(applied["ontology_version_id"]),
                updated_at=_now(),
                metadata={"appliedOntologyVersion": applied_info},
            )
            if row is None:
                raise ConflictDetected(
                    "ontology proposal execution state changed concurrently",
                    details={"proposal_id": proposal_id},
                )
            self._audit_event(conn, ctx, "applied", row)
            self._outbox_applied(conn, ctx, row, applied_info)
            return proposal_payload(row)

    def _outbox_applied(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        row: InsightReviewRow,
        applied_info: dict[str, object],
    ) -> None:
        # The apply black box already emits ontology.version.activated; this
        # proposal-scoped event is additive so consumers can track governance.
        self.runtime_service._outbox(
            conn,
            ctx,
            "ontology.proposal.applied",
            _RESOURCE_TYPE,
            row["id"],
            {"proposalId": row["id"], "proposalFingerprint": row["proposal_fingerprint"], **applied_info},
            idempotency_key=f"{row['id']}:applied",
            correlation_id=ctx.request_id,
        )

    def _record_execution_failure(self, ctx: RequestContext, proposal_id: str, exc: Exception) -> None:
        with self.engine.begin() as conn:
            self.insight_review_repository.mark_execution_failed(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                review_id=proposal_id,
                error={"message": str(exc), "type": exc.__class__.__name__},
                updated_at=_now(),
            )
            self.runtime_service._audit(
                conn,
                ctx,
                event_type="ontology.proposal.apply_failed",
                resource_type=_RESOURCE_TYPE,
                resource_id=proposal_id,
                action="ontology:activate",
                after_ref={"error": str(exc), "errorType": exc.__class__.__name__},
            )

    def _require_decidable(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        row: InsightReviewRow,
        decision: ProposalDecision,
    ) -> None:
        if proposal_status(row) not in ("submitted", "in_review"):
            raise ConflictDetected(
                "ontology proposal is no longer decidable",
                details={"proposal_id": row["id"], "status": proposal_status(row)},
            )
        if decision == "approved" and row["created_by_user_id"] == ctx.actor_user_id:
            # Separation of duties: the submitter can never be the approver.
            self._audit_denied(conn, ctx, "ontology:proposal:approve", row["id"])
            raise PermissionDenied(
                "ontology proposal submitters may not approve their own proposal",
                details={"proposal_id": row["id"], "submittedByUserId": row["created_by_user_id"]},
            )

    def _require_submitter(self, conn: TransactionContext, ctx: RequestContext, row: InsightReviewRow) -> None:
        if row["created_by_user_id"] == ctx.actor_user_id:
            return
        self._audit_denied(conn, ctx, "ontology:proposal:withdraw", row["id"])
        raise PermissionDenied(
            "only the submitter may withdraw an ontology proposal",
            details={"proposal_id": row["id"], "submittedByUserId": row["created_by_user_id"]},
        )

    def _audit_denied(self, conn: TransactionContext, ctx: RequestContext, permission: str, proposal_id: str) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="permission.denied",
            resource_type=_RESOURCE_TYPE,
            resource_id=proposal_id,
            action=permission,
            decision="deny",
            after_ref={"permission": permission},
        )

    def _require_proposal_row(self, conn: TransactionContext, ctx: RequestContext, pid: str) -> InsightReviewRow:
        row = self.insight_review_repository.review_by_id(transaction=conn, tenant_id=ctx.tenant_id, review_id=pid)
        if row is None or row.get("proposal_type") != ONTOLOGY_PROPOSAL_TYPE:
            raise NotFound("ontology proposal not found", details={"proposal_id": pid})
        return row

    def _require_write_open(self, ctx: RequestContext, operation: str, resource_id: str) -> None:
        self.runtime_service._require_write_traffic_open(
            ctx, operation=operation, resource_type=_RESOURCE_TYPE, resource_id=resource_id
        )

    def _audit_event(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        event: ProposalEvent,
        row: InsightReviewRow,
        *,
        before: InsightReviewRow | None = None,
    ) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type=f"ontology.proposal.{event}",
            resource_type=_RESOURCE_TYPE,
            resource_id=row["id"],
            action=PROPOSAL_EVENT_ACTIONS[event],
            before_ref=proposal_audit_ref(before) if before is not None else None,
            after_ref=proposal_audit_ref(row),
        )
