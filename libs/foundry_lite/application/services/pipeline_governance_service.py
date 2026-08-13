"""Pipeline Builder proposal, review, and version governance."""

from __future__ import annotations

from typing import NoReturn

from foundry_lite.application.ports import RuntimeRepository, TransactionContext
from foundry_lite.application.ports.pipeline_repository import (
    PipelineBranchRow,
    PipelineProposalRow,
    PipelineRepository,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.pipeline_graph_model import validate_pipeline_graph
from foundry_lite.application.services.pipeline_payloads import (
    bounded_pipeline_limit,
    proposal_payload,
    proposal_record,
    require_open_branch,
    require_proposal_status,
    required_text,
    version_payload,
    version_record,
)
from foundry_lite.application.services.pipeline_proposal_decision_policy import (
    decision_status,
    has_execution_approval,
    is_decision_replay,
    next_version_number,
    proposal_audit_ref,
    require_assigned_reviewer,
)
from foundry_lite.application.services.pipeline_proposal_review_evidence import (
    description_with_review_evidence,
    latest_test_receipt,
    proposal_change_diff,
    require_approvable_change_diff,
    require_approvable_test_receipt,
)
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, PermissionDenied, ValidationFailed

_RESOURCE_TYPE = "pipeline_proposal"


class PipelineGovernanceService(CoreService):
    """Review workflow for Pipeline Builder branches."""

    required_dependencies = ("engine", "policy", "pipeline_repository", "runtime_repository")
    required_collaborators = ("runtime_service",)
    pipeline_repository: PipelineRepository
    runtime_repository: RuntimeRepository
    runtime_service: RuntimeEvidenceBoundary

    def propose_branch(
        self,
        branch_id: str,
        *,
        title: str,
        idempotency_key: str,
        description: str | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "pipeline:write", _RESOURCE_TYPE, branch_id)
        self._require_write_open(ctx, "propose_pipeline_branch", branch_id)
        required_text(idempotency_key, "Idempotency-Key")
        clean_title = required_text(title, "title")
        with self.engine.begin() as conn:
            return self._submit_branch_proposal(conn, ctx, branch_id, clean_title, description)

    def _submit_branch_proposal(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        branch_id: str,
        title: str,
        description: str | None,
    ) -> dict[str, object]:
        branch = self._require_branch(conn, ctx, branch_id)
        require_open_branch(branch)
        if branch["proposal_id"] is not None:
            proposal = self._require_proposal(conn, ctx, str(branch["proposal_id"]))
            return self._proposal_view(conn, ctx, proposal)
        validation = validate_pipeline_graph(branch["graph"])
        if not validation["valid"]:
            raise ValidationFailed("pipeline graph is invalid", details={"validation": validation})
        proposal = self.pipeline_repository.insert_proposal(
            transaction=conn,
            record=proposal_record(
                ctx,
                branch,
                title=title,
                description=description_with_review_evidence(description, branch),
                now=_now(),
            ),
        )
        self.pipeline_repository.set_branch_proposal(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            branch_id=branch_id,
            proposal_id=str(proposal["id"]),
            updated_at=_now(),
        )
        self._audit(conn, ctx, "submitted", proposal)
        return self._proposal_view(conn, ctx, proposal)

    def list_proposals(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "pipeline:read")
        with self.engine.begin() as conn:
            rows = self.pipeline_repository.list_proposals(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                status=status,
                limit=bounded_pipeline_limit(limit),
            )
            items = [self._proposal_view(conn, ctx, row) for row in rows]
        return {"items": items, "nextCursor": None}

    def get_proposal(self, proposal_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "pipeline:read")
        with self.engine.begin() as conn:
            return self._proposal_view(conn, ctx, self._require_proposal(conn, ctx, proposal_id))

    def assign_proposal(
        self,
        proposal_id: str,
        *,
        assignee_user_id: str,
        is_unassigned_only: bool = False,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "pipeline:review", _RESOURCE_TYPE, proposal_id)
        self._require_write_open(ctx, "assign_pipeline_proposal", proposal_id)
        assignee = required_text(assignee_user_id, "assigneeUserId")
        with self.engine.begin() as conn:
            before = self._require_proposal(conn, ctx, proposal_id)
            if before["assigned_to"] == assignee and before["status"] in {"submitted", "in_review"}:
                return self._proposal_view(conn, ctx, before)
            after = self.pipeline_repository.update_proposal_assignment(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                proposal_id=proposal_id,
                assigned_to=assignee,
                updated_at=_now(),
                is_unassigned_only=is_unassigned_only,
            )
            if after is None:
                # A terminal proposal read at the start yields the clearer
                # "not in the required state"; a valid `before` whose CAS still
                # lost is a true concurrent change.
                require_proposal_status(before, ("submitted", "in_review"))
                self._raise_proposal_conflict(conn, ctx, proposal_id)
            self._audit(conn, ctx, "assigned", after, before=before)
            return self._proposal_view(conn, ctx, after)

    def decide_proposal(
        self,
        proposal_id: str,
        *,
        decision: str,
        comment: str | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "pipeline:review", _RESOURCE_TYPE, proposal_id)
        self._require_write_open(ctx, "decide_pipeline_proposal", proposal_id)
        status = decision_status(decision)
        with self.engine.begin() as conn:
            before = self._require_proposal(conn, ctx, proposal_id)
            if is_decision_replay(before, ctx, status, decision, comment):
                return self._proposal_view(conn, ctx, before)
            require_proposal_status(before, ("submitted", "in_review"))
            require_assigned_reviewer(before, ctx)
            if status == "approved":
                self._require_fresh_proposal(conn, ctx, before)
                require_approvable_change_diff(
                    proposal_change_diff(before["description"], str(before["graph_fingerprint"]))
                )
                require_approvable_test_receipt(self._test_receipt(conn, ctx, before))
            after = self.pipeline_repository.update_proposal_decision(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                proposal_id=proposal_id,
                status=status,
                decision=decision,
                comment=comment,
                decided_at=_now(),
                updated_at=_now(),
            )
            if after is None:
                self._raise_proposal_conflict(conn, ctx, proposal_id)
            self._audit(conn, ctx, status, after, before=before)
            return self._proposal_view(conn, ctx, after)

    def execute_proposal(self, proposal_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "pipeline:deploy", _RESOURCE_TYPE, proposal_id)
        self._require_write_open(ctx, "execute_pipeline_proposal", proposal_id)
        with self.engine.begin() as conn:
            proposal = self._require_proposal(conn, ctx, proposal_id)
            if proposal["status"] == "executed":
                self._require_execution_approval(conn, ctx, proposal)
                return self._executed_version(conn, ctx, proposal)
            require_proposal_status(proposal, ("approved",))
            self._require_execution_approval(conn, ctx, proposal)
            self._require_fresh_proposal(conn, ctx, proposal)
            claimed = self.pipeline_repository.mark_proposal_executed(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                proposal_id=proposal_id,
                updated_at=_now(),
            )
            if claimed is None:
                current = self._require_proposal(conn, ctx, proposal_id)
                if current["status"] == "executed":
                    return self._executed_version(conn, ctx, current)
                self._raise_proposal_conflict(conn, ctx, proposal_id)
            return self._create_version_from_proposal(conn, ctx, proposal)

    def _create_version_from_proposal(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        proposal: PipelineProposalRow,
    ) -> dict[str, object]:
        latest = self.pipeline_repository.latest_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            pipeline_id=str(proposal["pipeline_id"]),
        )
        version = self.pipeline_repository.insert_version(
            transaction=conn,
            record=version_record(ctx, proposal, version_number=next_version_number(latest), now=_now()),
        )
        self.pipeline_repository.close_branch(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            branch_id=str(proposal["branch_id"]),
            status="merged",
            merged_version_id=str(version["id"]),
            updated_at=_now(),
        )
        self._audit(conn, ctx, "executed", proposal)
        return version_payload(version)

    def _require_execution_approval(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        proposal: PipelineProposalRow,
    ) -> None:
        event = self.runtime_repository.audit_event_for_resource(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            event_type="pipeline.proposal.approved",
            resource_type=_RESOURCE_TYPE,
            resource_id=str(proposal["id"]),
        )
        if not has_execution_approval(proposal, event):
            raise PermissionDenied(
                "pipeline proposal lacks assigned human-reviewer approval evidence",
                details={"proposalId": proposal["id"]},
            )

    def _executed_version(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        proposal: PipelineProposalRow,
    ) -> dict[str, object]:
        branch = self._require_branch(conn, ctx, str(proposal["branch_id"]))
        version_id = branch["merged_version_id"]
        if not isinstance(version_id, str):
            raise ConflictDetected("executed pipeline proposal is missing its merged version")
        version = self.pipeline_repository.version_by_id(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            version_id=version_id,
        )
        if version is None or version["proposal_id"] != proposal["id"]:
            raise ConflictDetected("executed pipeline proposal has invalid merged-version evidence")
        return version_payload(version)

    def withdraw_proposal(self, proposal_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "pipeline:write", _RESOURCE_TYPE, proposal_id)
        self._require_write_open(ctx, "withdraw_pipeline_proposal", proposal_id)
        with self.engine.begin() as conn:
            before = self._require_proposal(conn, ctx, proposal_id)
            after = self.pipeline_repository.withdraw_proposal(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                proposal_id=proposal_id,
                updated_at=_now(),
            )
            if after is None:
                require_proposal_status(before, ("submitted", "in_review", "approved"))
                self._raise_proposal_conflict(conn, ctx, proposal_id)
            self._audit(conn, ctx, "withdrawn", after, before=before)
            return self._proposal_view(conn, ctx, after)

    def _require_branch(self, conn: TransactionContext, ctx: RequestContext, branch_id: str) -> PipelineBranchRow:
        row = self.pipeline_repository.branch_by_id(transaction=conn, tenant_id=ctx.tenant_id, branch_id=branch_id)
        if row is None:
            raise NotFound("pipeline branch not found", details={"branch_id": branch_id})
        return row

    def _raise_proposal_conflict(self, conn: TransactionContext, ctx: RequestContext, proposal_id: str) -> NoReturn:
        """Report a concurrent proposal change after a guarded CAS matched no rows.

        The CAS UPDATE guards on the allowed source statuses, so a miss means the
        row's status changed under a concurrent writer. The caller's ``before``
        row was read before that change and already passed the same status guard,
        so re-checking it can never fire; re-read the row and report its actual
        current status instead of falling through to a bare AssertionError.
        """
        current = self.pipeline_repository.proposal_by_id(
            transaction=conn, tenant_id=ctx.tenant_id, proposal_id=proposal_id
        )
        raise ConflictDetected(
            "pipeline proposal changed concurrently",
            details={"proposalId": proposal_id, "status": current["status"] if current is not None else None},
        )

    def _require_proposal(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        proposal_id: str,
    ) -> PipelineProposalRow:
        row = self.pipeline_repository.proposal_by_id(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            proposal_id=proposal_id,
        )
        if row is None:
            raise NotFound("pipeline proposal not found", details={"proposal_id": proposal_id})
        return row

    def _require_fresh_proposal(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        proposal: PipelineProposalRow,
    ) -> None:
        reasons = self._proposal_stale_reasons(conn, ctx, proposal)
        if reasons:
            raise ConflictDetected(
                "pipeline proposal is stale and must be rebased and resubmitted",
                details={"proposalId": proposal["id"], "reasons": reasons},
            )

    def _proposal_stale_reasons(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        proposal: PipelineProposalRow,
    ) -> list[str]:
        if proposal["status"] in {"executed", "rejected", "withdrawn"}:
            return []
        branch = self._require_branch(conn, ctx, str(proposal["branch_id"]))
        latest = self.pipeline_repository.latest_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            pipeline_id=str(proposal["pipeline_id"]),
        )
        reasons: list[str] = []
        if branch["proposal_id"] != proposal["id"]:
            reasons.append("proposal_detached_from_branch")
        if branch["graph_fingerprint"] != proposal["graph_fingerprint"]:
            reasons.append("branch_graph_changed")
        latest_id = latest["id"] if latest is not None else None
        if branch["base_version_id"] != latest_id:
            reasons.append("newer_pipeline_version_exists")
        return reasons

    def _proposal_view(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        proposal: PipelineProposalRow,
    ) -> dict[str, object]:
        payload = proposal_payload(proposal)
        stale_reasons = self._proposal_stale_reasons(conn, ctx, proposal)
        payload["isStale"] = bool(stale_reasons)
        payload["staleReasons"] = stale_reasons
        payload["canCurrentUserReview"] = proposal["assigned_to"] == ctx.actor_user_id
        payload["reviewPolicy"] = {
            "requiresAssignment": True,
            "requiresSeparateReviewer": False,
            "blocksStaleProposal": True,
        }
        review_evidence = proposal_change_diff(proposal["description"], str(proposal["graph_fingerprint"]))
        payload["changeDiff"] = review_evidence.get("changeDiff")
        payload["diffCompleteness"] = review_evidence.get("completeness")
        payload["testReceipt"] = self._test_receipt(conn, ctx, proposal)
        return payload

    def _test_receipt(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        proposal: PipelineProposalRow,
    ) -> dict[str, object]:
        stored = self.pipeline_repository.latest_test_result(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            branch_id=str(proposal["branch_id"]),
        )
        return latest_test_receipt(proposal, stored)

    def _require_write_open(self, ctx: RequestContext, operation: str, resource_id: str) -> None:
        self.runtime_service._require_write_traffic_open(
            ctx,
            operation=operation,
            resource_type=_RESOURCE_TYPE,
            resource_id=resource_id,
        )

    def _audit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        event: str,
        row: PipelineProposalRow,
        *,
        before: PipelineProposalRow | None = None,
    ) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type=f"pipeline.proposal.{event}",
            resource_type=_RESOURCE_TYPE,
            resource_id=str(row["id"]),
            action=event,
            before_ref=proposal_audit_ref(before),
            after_ref=proposal_audit_ref(row),
        )
