from __future__ import annotations

import json
from collections.abc import Mapping

import pytest
from foundry_lite.application.services.aip.governed_release_workflow import GovernedReleaseWorkflowService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied, ValidationFailed


class _Ontology:
    def __init__(self, proposals: list[Mapping[str, object]]) -> None:
        self.proposals = [dict(row) for row in proposals]
        self.assigned: tuple[str, str] | None = None

    def create_branch(self, *, name: str, idempotency_key: str, ctx: RequestContext | None = None) -> dict[str, object]:
        del idempotency_key, ctx
        return {
            "id": "ontology-branch-1",
            "name": name,
            "contentFingerprint": "sha256:ontology-branch",
            "yamlText": "must-not-leave-workflow-boundary",
        }

    def list_proposals(
        self,
        *,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        del ctx
        offset = int(cursor or "0")
        matching = [row for row in self.proposals if row.get("status") == status]
        items = matching[offset : offset + limit]
        next_offset = offset + len(items)
        next_cursor = str(next_offset) if next_offset < len(matching) else None
        return {"items": items, "nextCursor": next_cursor}

    def get_proposal(self, proposal_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        del ctx
        return next(dict(row) for row in self.proposals if row["id"] == proposal_id)

    def assign_proposal(
        self,
        proposal_id: str,
        *,
        reviewer_user_id: str,
        is_unassigned_only: bool = False,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        del ctx, is_unassigned_only
        self.assigned = (proposal_id, reviewer_user_id)
        proposal = self.get_proposal(proposal_id)
        proposal["assigneeUserId"] = reviewer_user_id
        return proposal


class _Pipelines:
    def __init__(self, proposals: list[Mapping[str, object]] | None = None) -> None:
        self.proposals = [dict(row) for row in proposals or []]
        self.created: dict[str, object] | None = None
        self.assigned: tuple[str, str] | None = None

    def create_branch(
        self,
        *,
        pipeline_id: str,
        name: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        del ctx
        self.created = {"pipelineId": pipeline_id, "name": name, "idempotencyKey": idempotency_key}
        return {
            "id": "pipeline-branch-1",
            "pipelineId": pipeline_id,
            "name": name,
            "graphFingerprint": "sha256:pipeline-branch",
            "graph": {"secret": "must-not-leave-workflow-boundary"},
        }

    def list_proposals(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        del ctx
        return {"items": [row for row in self.proposals if row.get("status") == status][:limit]}

    def get_proposal(self, proposal_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        del ctx
        return next(dict(row) for row in self.proposals if row["id"] == proposal_id)

    def assign_proposal(
        self,
        proposal_id: str,
        *,
        assignee_user_id: str,
        is_unassigned_only: bool = False,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        del ctx, is_unassigned_only
        self.assigned = (proposal_id, assignee_user_id)
        proposal = self.get_proposal(proposal_id)
        proposal["assignedTo"] = assignee_user_id
        return proposal


def _service(
    ontology_proposals: list[Mapping[str, object]] | None = None,
    pipeline_proposals: list[Mapping[str, object]] | None = None,
    *,
    is_separate_reviewer_required: bool = False,
) -> tuple[GovernedReleaseWorkflowService, _Ontology, _Pipelines]:
    ontology = _Ontology(ontology_proposals or [])
    pipelines = _Pipelines(pipeline_proposals)
    return (
        GovernedReleaseWorkflowService(
            ontology=ontology,
            pipelines=pipelines,
            is_separate_reviewer_required=is_separate_reviewer_required,
        ),
        ontology,
        pipelines,
    )


def _reviewer() -> RequestContext:
    return RequestContext(actor_user_id="reviewer-1", roles=("admin",))


def _mapping(value: object) -> Mapping[str, object]:
    assert isinstance(value, Mapping)
    return value


def _inbox_items(view: Mapping[str, object]) -> list[Mapping[str, object]]:
    evidence = _mapping(view["releaseEvidence"])
    inbox = _mapping(evidence["reviewInbox"])
    value = inbox["items"]
    assert isinstance(value, list)
    return [_mapping(item) for item in value]


def test_open_workspace_requires_pipeline_identity_and_returns_exact_branch_plan() -> None:
    service, _, _ = _service()

    with pytest.raises(ValidationFailed, match="pipelineId"):
        service.open_workspace(_reviewer(), {"releaseKind": "pipeline", "branchName": "candidate"})

    view = service.open_workspace(
        _reviewer(),
        {"releaseKind": "pipeline", "branchName": "candidate", "pipelineId": "orders"},
    )
    assert view["stage"] == "workspace_ready"
    assert view["nextActions"] == ["create_release_branch"]
    assert view["releaseEvidence"] == {
        "branchPlan": {"releaseKind": "pipeline", "branchName": "candidate", "pipelineId": "orders"}
    }


def test_create_branch_returns_builder_workspace_without_branch_body() -> None:
    service, ontology, pipelines = _service()

    ontology_view = service.create_branch(
        _reviewer(),
        {"releaseKind": "ontology", "branchName": "objects", "idempotencyKey": "branch-1"},
    )
    pipeline_view = service.create_branch(
        _reviewer(),
        {
            "releaseKind": "pipeline",
            "branchName": "orders",
            "pipelineId": "orders-pipeline",
            "idempotencyKey": "branch-2",
        },
    )

    assert _mapping(ontology_view["releaseEvidence"])["builderWorkspaceRef"] == "ontology-branch:ontology-branch-1"
    assert _mapping(pipeline_view["releaseEvidence"])["builderWorkspaceRef"] == "pipeline-branch:pipeline-branch-1"
    assert pipelines.created == {
        "pipelineId": "orders-pipeline",
        "name": "orders",
        "idempotencyKey": "branch-2",
    }
    assert "must-not-leave" not in json.dumps([ontology_view, pipeline_view])
    assert ontology.assigned is None


def test_inbox_includes_submitter_own_but_hides_other_reviewer_proposals() -> None:
    service, _, _ = _service(
        ontology_proposals=[
            {
                "id": "own",
                "status": "submitted",
                "title": "own",
                "submittedByUserId": "reviewer-1",
                "assigneeUserId": None,
            },
            {
                "id": "claimable",
                "status": "submitted",
                "title": "claimable",
                "submittedByUserId": "author-1",
                "assigneeUserId": None,
                "fingerprint": "sha256:claimable",
            },
            {
                "id": "other-reviewer",
                "status": "in_review",
                "title": "hidden",
                "submittedByUserId": "author-2",
                "assigneeUserId": "reviewer-2",
            },
        ]
    )

    view = service.list_inbox(_reviewer(), {"releaseKind": "ontology"})

    assert view["proposalId"] == "own"
    assert view["stage"] == "awaiting_assignment"
    assert view["nextActions"] == ["assign_release_reviewer"]
    assert [row["id"] for row in _inbox_items(view)] == ["own", "claimable"]
    assert _mapping(view["candidate"])["reviewPolicy"]["requiresSeparateReviewer"] is False


def test_inbox_prioritizes_already_assigned_current_reviewer() -> None:
    service, _, _ = _service(
        pipeline_proposals=[
            {
                "id": "claimable",
                "status": "submitted",
                "title": "claimable",
                "createdBy": "author-1",
                "assignedTo": None,
            },
            {
                "id": "assigned",
                "status": "in_review",
                "title": "assigned",
                "createdBy": "author-2",
                "assignedTo": "reviewer-1",
            },
        ]
    )

    view = service.list_inbox(_reviewer(), {"releaseKind": "pipeline"})

    assert view["proposalId"] == "assigned"
    assert view["stage"] == "awaiting_review"
    assert view["nextActions"] == ["submit_release_decision"]


def test_inbox_keeps_assigned_review_when_submitted_rows_fill_requested_limit() -> None:
    submitted = [
        {
            "id": f"claimable-{index}",
            "status": "submitted",
            "title": f"claimable {index}",
            "createdBy": f"author-{index}",
            "assignedTo": None,
        }
        for index in range(3)
    ]
    assigned = {
        "id": "assigned",
        "status": "in_review",
        "title": "assigned",
        "createdBy": "author-assigned",
        "assignedTo": "reviewer-1",
    }
    service, _, _ = _service(pipeline_proposals=[*submitted, assigned])

    view = service.list_inbox(_reviewer(), {"releaseKind": "pipeline", "limit": 2})

    assert view["proposalId"] == "assigned"
    assert view["stage"] == "awaiting_review"
    assert [row["id"] for row in _inbox_items(view)] == [
        "assigned",
        "claimable-0",
    ]


def test_inbox_scans_next_ontology_page_after_other_reviewer_rows_are_filtered_out() -> None:
    other_reviewer_first_page = [
        {
            "id": f"other-{index}",
            "status": "in_review",
            "title": f"other {index}",
            "submittedByUserId": "author-other",
            "assigneeUserId": "reviewer-2",
        }
        for index in range(50)
    ]
    claimable = {
        "id": "claimable-after-first-page",
        "status": "submitted",
        "title": "claimable after first page",
        "submittedByUserId": "author-1",
        "assigneeUserId": None,
    }
    service, _, _ = _service(ontology_proposals=[*other_reviewer_first_page, claimable])

    view = service.list_inbox(_reviewer(), {"releaseKind": "ontology", "limit": 1})

    assert view["proposalId"] == "claimable-after-first-page"
    assert view["stage"] == "awaiting_assignment"
    assert view["nextActions"] == ["assign_release_reviewer"]


def test_reviewer_claim_is_self_only_allows_submitter_and_blocks_reassignment() -> None:
    service, ontology, _ = _service(
        ontology_proposals=[
            {
                "id": "claimable",
                "status": "submitted",
                "submittedByUserId": "author-1",
                "assigneeUserId": None,
            },
            {
                "id": "own",
                "status": "submitted",
                "submittedByUserId": "reviewer-1",
                "assigneeUserId": None,
            },
            {
                "id": "assigned",
                "status": "in_review",
                "submittedByUserId": "author-2",
                "assigneeUserId": "reviewer-2",
            },
        ]
    )

    service.assign_reviewer(
        _reviewer(),
        {"releaseKind": "ontology", "proposalId": "claimable", "idempotencyKey": "claim-1"},
    )
    assert ontology.assigned == ("claimable", "reviewer-1")
    service.assign_reviewer(
        _reviewer(),
        {"releaseKind": "ontology", "proposalId": "own", "idempotencyKey": "claim-2"},
    )
    assert ontology.assigned == ("own", "reviewer-1")
    with pytest.raises(PermissionDenied, match="another"):
        service.assign_reviewer(
            _reviewer(),
            {"releaseKind": "ontology", "proposalId": "assigned", "idempotencyKey": "claim-3"},
        )


@pytest.mark.parametrize("release_kind", ["ontology", "pipeline"])
def test_protected_inbox_hides_author_proposals_and_rejects_direct_self_claim(release_kind: str) -> None:
    ontology_proposals = [
        {
            "id": "own",
            "status": "submitted",
            "submittedByUserId": "reviewer-1",
            "assigneeUserId": None,
        },
        {
            "id": "other-author",
            "status": "submitted",
            "submittedByUserId": "author-2",
            "assigneeUserId": None,
        },
    ]
    pipeline_proposals = [
        {
            "id": "own",
            "status": "submitted",
            "createdBy": "reviewer-1",
            "assignedTo": None,
        },
        {
            "id": "other-author",
            "status": "submitted",
            "createdBy": "author-2",
            "assignedTo": None,
        },
    ]
    service, _, _ = _service(
        ontology_proposals=ontology_proposals,
        pipeline_proposals=pipeline_proposals,
        is_separate_reviewer_required=True,
    )

    view = service.list_inbox(_reviewer(), {"releaseKind": release_kind})

    assert [item["id"] for item in _inbox_items(view)] == ["other-author"]
    assert _mapping(view["candidate"])["reviewPolicy"]["requiresSeparateReviewer"] is True
    with pytest.raises(PermissionDenied, match="reviewer other than"):
        service.assign_reviewer(
            _reviewer(),
            {"releaseKind": release_kind, "proposalId": "own", "idempotencyKey": "claim-own"},
        )
