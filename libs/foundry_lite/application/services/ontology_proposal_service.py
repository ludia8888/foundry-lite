"""Review, approve, and exactly-once apply ontology YAML proposals."""

from __future__ import annotations

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.insight_review_repository import InsightReviewRow
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.ontology_proposal_governance import (
    require_assigned_decider,
    require_decidable,
    require_execution_approval,
)
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
from foundry_lite.application.services.ontology_proposal_recovery import (
    claim_new_execution,
    finish_execution_after_activation,
    reconcile_executing_proposal,
    recover_apply_exception,
)
from foundry_lite.application.services.ontology_proposal_update import decision_time_plan, update_ontology_proposal
from foundry_lite.application.services.ontology_protocols import OntologyRuntimeBoundary
from foundry_lite.application.services.ontology_service import OntologyService
from foundry_lite.application.services.ontology_yaml import require_yaml_text_within_limit
from foundry_lite.application.services.runtime_error_payloads import scrub_error_text
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, PermissionDenied

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
        is_unassigned_only: bool = False,
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
                is_unassigned_only=is_unassigned_only,
            )
            if after is None:
                if is_unassigned_only:
                    raise ConflictDetected(
                        "ontology proposal was already claimed by another reviewer",
                        details={"proposal_id": proposal_id},
                    )
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
            require_assigned_decider(row, ctx)
            replay = decision_replay(row, parsed, comment)
            if replay is not None:
                return replay
            require_decidable(row)
        # The plan may have drifted since submit: recompute it against the
        # CURRENT active ontology outside the storage transaction (the validate
        # black box owns its own transaction) and store the decision-time plan.
        plan = decision_time_plan(self, ctx, row, parsed)
        return self._store_decision(ctx, proposal_id, parsed, comment, plan)

    def update_proposal(
        self,
        proposal_id: str,
        *,
        yaml_text: str,
        expected_fingerprint: str,
        title: str | None = None,
        description: str | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        """Revise a not-yet-decided proposal in place (submitter only)."""
        return update_ontology_proposal(
            self,
            proposal_id,
            yaml_text=yaml_text,
            expected_fingerprint=expected_fingerprint,
            title=title,
            description=description,
            ctx=ctx,
        )

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
            applied = self.ontology_service.apply_ontology_text_once(
                yaml_text,
                idempotency_key=f"{proposal_id}:execute",
                ctx=ctx,
            )
        except Exception as exc:
            return recover_apply_exception(
                self.engine,
                ctx,
                proposal_id,
                self.runtime_service,
                self._require_proposal_row,
                self._store_execution_success,
                self._record_execution_failure,
                exc,
            )
        return finish_execution_after_activation(self._finish_execution, ctx, proposal_id, dict(applied))

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
            require_assigned_decider(before, ctx)
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
            if row["status"] == "approved":
                require_execution_approval(row)
            if row["execution_status"] == "executed":
                return proposal_payload(row), ""
            if row["execution_status"] == "executing":
                replay = reconcile_executing_proposal(
                    conn,
                    ctx,
                    row,
                    self.runtime_service,
                    self._store_execution_success,
                    _now(),
                )
                if replay is not None:
                    return replay, ""
                return None, proposal_yaml_text(row)
            return None, claim_new_execution(
                self.insight_review_repository,
                conn,
                ctx,
                row,
                _now(),
            )

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
            current = self._require_proposal_row(conn, ctx, proposal_id)
            if current["execution_status"] == "executed":
                return proposal_payload(current)
            return self._store_execution_success(conn, ctx, current, applied_info)

    def _store_execution_success(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        current: InsightReviewRow,
        applied_info: dict[str, object],
    ) -> dict[str, object]:
        row = self.insight_review_repository.mark_execution_succeeded(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            review_id=current["id"],
            action_run_id=str(applied_info["ontologyVersionId"]),
            updated_at=_now(),
            metadata={"appliedOntologyVersion": applied_info},
        )
        if row is None:
            latest = self._require_proposal_row(conn, ctx, current["id"])
            if latest["execution_status"] == "executed":
                return proposal_payload(latest)
            raise ConflictDetected("ontology proposal execution state changed concurrently")
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
        safe_error = scrub_error_text(str(exc))
        with self.engine.begin() as conn:
            self.insight_review_repository.mark_execution_failed(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                review_id=proposal_id,
                error={"message": safe_error, "type": exc.__class__.__name__},
                updated_at=_now(),
            )
            self.runtime_service._audit(
                conn,
                ctx,
                event_type="ontology.proposal.apply_failed",
                resource_type=_RESOURCE_TYPE,
                resource_id=proposal_id,
                action="ontology:activate",
                after_ref={"error": safe_error, "errorType": exc.__class__.__name__},
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
