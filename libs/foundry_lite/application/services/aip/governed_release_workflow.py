"""GPT-visible bootstrap and assigned-human review handoff for governed releases."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from foundry_lite.application.services.aip.governed_release_outcomes import project_confirmed_mutation
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied, ValidationFailed

JsonObject = Mapping[str, object]

_INBOX_SCAN_PAGE_SIZE = 50
_INBOX_SCAN_MAX_PAGES = 4
_PIPELINE_INBOX_SCAN_LIMIT = 100


class OntologyReleaseWorkflowBoundary(Protocol):
    def create_branch(
        self,
        *,
        name: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]: ...

    def list_proposals(
        self,
        *,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]: ...

    def get_proposal(self, proposal_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]: ...

    def assign_proposal(
        self,
        proposal_id: str,
        *,
        reviewer_user_id: str,
        is_unassigned_only: bool = False,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]: ...


class PipelineReleaseWorkflowBoundary(Protocol):
    def create_branch(
        self,
        *,
        pipeline_id: str,
        name: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]: ...

    def list_proposals(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]: ...

    def get_proposal(self, proposal_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]: ...

    def assign_proposal(
        self,
        proposal_id: str,
        *,
        assignee_user_id: str,
        is_unassigned_only: bool = False,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]: ...


class GovernedReleaseWorkflowService:
    """Start branch work and hand a submitted proposal to an assigned human reviewer."""

    required_dependencies: tuple[str, ...] = ()
    required_collaborators: tuple[str, ...] = ()
    ontology: OntologyReleaseWorkflowBoundary
    pipelines: PipelineReleaseWorkflowBoundary
    is_separate_reviewer_required: bool

    def __init__(
        self,
        *,
        ontology: OntologyReleaseWorkflowBoundary,
        pipelines: PipelineReleaseWorkflowBoundary,
        is_separate_reviewer_required: bool = False,
    ) -> None:
        self.ontology = ontology
        self.pipelines = pipelines
        self.is_separate_reviewer_required = is_separate_reviewer_required

    def open_workspace(self, ctx: RequestContext, arguments: JsonObject) -> dict[str, object]:
        del ctx
        kind = _release_kind(arguments)
        branch_name = _required_text(arguments, "branchName")
        pipeline_id = _pipeline_id(arguments, kind)
        plan = _branch_plan(kind, branch_name, pipeline_id)
        return _workflow_view(kind, "workspace_ready", plan, "create_release_branch")

    def create_branch(self, ctx: RequestContext, arguments: JsonObject) -> dict[str, object]:
        kind = _release_kind(arguments)
        name = _required_text(arguments, "branchName")
        key = _required_text(arguments, "idempotencyKey")
        if kind == "ontology":
            branch = self.ontology.create_branch(name=name, idempotency_key=key, ctx=ctx)
        else:
            branch = self.pipelines.create_branch(
                pipeline_id=_required_text(arguments, "pipelineId"),
                name=name,
                idempotency_key=key,
                ctx=ctx,
            )
        return project_confirmed_mutation("create_release_branch", lambda: _branch_created_view(kind, branch))

    def list_inbox(self, ctx: RequestContext, arguments: JsonObject) -> dict[str, object]:
        kind = _release_kind(arguments)
        limit = _bounded_limit(arguments.get("limit"))
        proposals = self._reviewable_proposals(ctx, kind, limit)
        items = [
            _inbox_item(
                kind,
                proposal,
                ctx,
                is_separate_reviewer_required=self.is_separate_reviewer_required,
            )
            for proposal in proposals
        ]
        selected = next((item for item in items if item["canCurrentUserReview"] is True), None)
        selected = selected or next((item for item in items if item["canCurrentUserClaim"] is True), None)
        return _inbox_view(
            kind,
            items,
            selected,
            is_separate_reviewer_required=self.is_separate_reviewer_required,
        )

    def assign_reviewer(self, ctx: RequestContext, arguments: JsonObject) -> dict[str, object]:
        kind = _release_kind(arguments)
        proposal_id = _required_text(arguments, "proposalId")
        proposal = self._proposal(ctx, kind, proposal_id)
        _require_claimable(
            proposal,
            kind,
            ctx,
            is_separate_reviewer_required=self.is_separate_reviewer_required,
        )
        if kind == "ontology":
            return self.ontology.assign_proposal(
                proposal_id,
                reviewer_user_id=ctx.actor_user_id,
                is_unassigned_only=True,
                ctx=ctx,
            )
        return self.pipelines.assign_proposal(
            proposal_id,
            assignee_user_id=ctx.actor_user_id,
            is_unassigned_only=True,
            ctx=ctx,
        )

    def _reviewable_proposals(
        self,
        ctx: RequestContext,
        kind: str,
        limit: int,
    ) -> list[Mapping[str, object]]:
        assigned = self._visible_status_proposals(ctx, kind, "in_review", limit)
        remaining = limit - len(assigned)
        submitted = self._visible_status_proposals(ctx, kind, "submitted", remaining)
        return [*assigned, *submitted]

    def _visible_status_proposals(
        self,
        ctx: RequestContext,
        kind: str,
        status: str,
        limit: int,
    ) -> list[Mapping[str, object]]:
        if limit <= 0:
            return []
        if kind == "ontology":
            return self._visible_ontology_proposals(ctx, status, limit)
        payload = self.pipelines.list_proposals(status=status, limit=_PIPELINE_INBOX_SCAN_LIMIT, ctx=ctx)
        return _visible_items(
            payload,
            kind,
            ctx,
            is_separate_reviewer_required=self.is_separate_reviewer_required,
        )[:limit]

    def _visible_ontology_proposals(
        self,
        ctx: RequestContext,
        status: str,
        limit: int,
    ) -> list[Mapping[str, object]]:
        visible: list[Mapping[str, object]] = []
        cursor: str | None = None
        for _ in range(_INBOX_SCAN_MAX_PAGES):
            payload = self.ontology.list_proposals(
                status=status,
                cursor=cursor,
                limit=_INBOX_SCAN_PAGE_SIZE,
                ctx=ctx,
            )
            visible.extend(
                _visible_items(
                    payload,
                    "ontology",
                    ctx,
                    is_separate_reviewer_required=self.is_separate_reviewer_required,
                )
            )
            cursor = _next_cursor(payload)
            if len(visible) >= limit or cursor is None:
                break
        return visible[:limit]

    def _proposal(self, ctx: RequestContext, kind: str, proposal_id: str) -> dict[str, object]:
        if kind == "ontology":
            return self.ontology.get_proposal(proposal_id, ctx=ctx)
        return self.pipelines.get_proposal(proposal_id, ctx=ctx)


def _release_kind(arguments: JsonObject) -> str:
    kind = _required_text(arguments, "releaseKind")
    if kind not in {"ontology", "pipeline"}:
        raise ValidationFailed("releaseKind must be ontology or pipeline")
    return kind


def _required_text(arguments: JsonObject, key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationFailed(f"{key} is required")
    return value.strip()


def _pipeline_id(arguments: JsonObject, kind: str) -> str | None:
    return _required_text(arguments, "pipelineId") if kind == "pipeline" else None


def _bounded_limit(value: object) -> int:
    if value is None:
        return 20
    if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > 50:
        raise ValidationFailed("limit must be between 1 and 50")
    return value


def _items(payload: JsonObject) -> list[Mapping[str, object]]:
    value = payload.get("items")
    return [row for row in value if isinstance(row, Mapping)] if isinstance(value, list) else []


def _visible_items(
    payload: JsonObject,
    kind: str,
    ctx: RequestContext,
    *,
    is_separate_reviewer_required: bool,
) -> list[Mapping[str, object]]:
    return [
        row
        for row in _items(payload)
        if _is_visible_review_handoff(
            row,
            kind,
            ctx,
            is_separate_reviewer_required=is_separate_reviewer_required,
        )
    ]


def _next_cursor(payload: JsonObject) -> str | None:
    value = payload.get("nextCursor")
    return value if isinstance(value, str) and value else None


def _branch_plan(kind: str, branch_name: str, pipeline_id: str | None) -> dict[str, object]:
    plan: dict[str, object] = {"releaseKind": kind, "branchName": branch_name}
    if pipeline_id is not None:
        plan["pipelineId"] = pipeline_id
    return plan


def _workflow_view(kind: str, stage: str, plan: JsonObject, action: str | None) -> dict[str, object]:
    return {
        "releaseKind": kind,
        "proposalId": "pending-branch",
        "stage": stage,
        "candidate": {
            "id": "pending-branch",
            "title": f"{plan['branchName']} branch",
            "description": "GPT에서 격리 브랜치를 만든 뒤 Builder MCP로 편집·검증·제안합니다.",
            **dict(plan),
        },
        "releaseEvidence": {"branchPlan": dict(plan)},
        "rollbackTarget": None,
        "nextActions": [action] if action else [],
    }


def _branch_created_view(kind: str, branch: JsonObject) -> dict[str, object]:
    branch_id = _required_text(branch, "id")
    fingerprint_key = "contentFingerprint" if kind == "ontology" else "graphFingerprint"
    candidate = {
        "id": branch_id,
        "title": str(branch.get("name") or branch_id),
        "description": "격리 브랜치가 준비되었습니다. 표시된 workspaceRef로 Builder MCP 작업을 계속하세요.",
        "branchName": branch.get("name"),
        "pipelineId": branch.get("pipelineId"),
        fingerprint_key: branch.get(fingerprint_key),
    }
    workspace_ref = f"{kind}-branch:{branch_id}"
    return {
        "releaseKind": kind,
        "proposalId": f"branch:{branch_id}",
        "stage": "branch_created",
        "candidate": candidate,
        "releaseEvidence": {"branchId": branch_id, "builderWorkspaceRef": workspace_ref},
        "rollbackTarget": None,
        "nextActions": [],
    }


def _review_identity(proposal: JsonObject, kind: str) -> tuple[object, object]:
    if kind == "ontology":
        return proposal.get("submittedByUserId"), proposal.get("assigneeUserId")
    return proposal.get("createdBy"), proposal.get("assignedTo")


def _is_visible_review_handoff(
    proposal: JsonObject,
    kind: str,
    ctx: RequestContext,
    *,
    is_separate_reviewer_required: bool,
) -> bool:
    submitter, assignee = _review_identity(proposal, kind)
    if is_separate_reviewer_required and submitter == ctx.actor_user_id:
        return False
    return assignee is None or assignee == ctx.actor_user_id


def _inbox_item(
    kind: str,
    proposal: JsonObject,
    ctx: RequestContext,
    *,
    is_separate_reviewer_required: bool,
) -> dict[str, object]:
    submitter, assignee = _review_identity(proposal, kind)
    fingerprint = proposal.get("fingerprint") if kind == "ontology" else proposal.get("graphFingerprint")
    return {
        "id": proposal.get("id"),
        "releaseKind": kind,
        "pipelineId": proposal.get("pipelineId"),
        "title": proposal.get("title"),
        "description": proposal.get("description"),
        "status": proposal.get("status"),
        "fingerprint": fingerprint,
        "submittedByUserId": submitter,
        "assigneeUserId": assignee,
        "canCurrentUserClaim": assignee is None,
        "canCurrentUserReview": assignee == ctx.actor_user_id,
        "reviewPolicy": {
            "requiresAssignment": True,
            "requiresSeparateReviewer": is_separate_reviewer_required,
            "blocksStaleProposal": True,
        },
    }


def _inbox_view(
    kind: str,
    items: list[dict[str, object]],
    selected: dict[str, object] | None,
    *,
    is_separate_reviewer_required: bool,
) -> dict[str, object]:
    candidate = selected or {
        "id": "empty-inbox",
        "title": "검토할 제안이 없습니다",
        "description": "미배정 또는 나에게 배정된 검토 가능 제안만 표시됩니다.",
        "reviewPolicy": {
            "requiresAssignment": True,
            "requiresSeparateReviewer": is_separate_reviewer_required,
        },
    }
    can_review = candidate.get("canCurrentUserReview") is True
    can_claim = candidate.get("canCurrentUserClaim") is True
    action = "submit_release_decision" if can_review else "assign_release_reviewer" if can_claim else None
    return {
        "releaseKind": kind,
        "proposalId": str(candidate.get("id")),
        "stage": "awaiting_review" if can_review else "awaiting_assignment" if can_claim else "empty_inbox",
        "candidate": candidate,
        "releaseEvidence": {"reviewInbox": {"count": len(items), "items": items}},
        "rollbackTarget": None,
        "nextActions": [action] if action else [],
    }


def _require_claimable(
    proposal: JsonObject,
    kind: str,
    ctx: RequestContext,
    *,
    is_separate_reviewer_required: bool,
) -> None:
    submitter, assignee = _review_identity(proposal, kind)
    if is_separate_reviewer_required and submitter == ctx.actor_user_id:
        raise PermissionDenied("protected releases require a reviewer other than the proposal author")
    if assignee not in {None, ctx.actor_user_id}:
        raise PermissionDenied("proposal is assigned to another human reviewer")


__all__ = ["GovernedReleaseWorkflowService"]
