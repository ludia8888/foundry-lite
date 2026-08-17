from __future__ import annotations

from typing import cast

import pytest
from foundry_lite.application.ports.insight_review_repository import InsightReviewRow
from foundry_lite.application.ports.pipeline_repository import PipelineProposalRow
from foundry_lite.application.services.ontology_proposal_governance import (
    require_assigned_decider,
    require_execution_approval,
)
from foundry_lite.application.services.pipeline_proposal_decision_policy import (
    has_execution_approval,
    require_assigned_reviewer,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied, ValidationFailed

_AUTHOR = RequestContext(actor_user_id="author-1", roles=("admin",))


def test_ontology_self_review_is_local_only_and_cannot_execute_after_protected_cutover() -> None:
    row = cast(
        InsightReviewRow,
        {
            "id": "ontology-proposal-1",
            "status": "approved",
            "created_by_user_id": "author-1",
            "assignee_user_id": "author-1",
            "decision": {
                "decision": "approved",
                "decidedByUserId": "author-1",
                "hasBlockedChanges": False,
                "migrationPlan": {"blockedChanges": []},
            },
        },
    )

    require_assigned_decider(row, _AUTHOR, is_separate_reviewer_required=False)
    require_execution_approval(row, is_separate_reviewer_required=False)
    with pytest.raises(PermissionDenied, match="reviewer other than"):
        require_assigned_decider(row, _AUTHOR, is_separate_reviewer_required=True)
    with pytest.raises(PermissionDenied, match="approval evidence"):
        require_execution_approval(row, is_separate_reviewer_required=True)


def test_pipeline_self_review_is_local_only_and_cannot_execute_after_protected_cutover() -> None:
    proposal = cast(
        PipelineProposalRow,
        {
            "id": "pipeline-proposal-1",
            "status": "approved",
            "created_by": "author-1",
            "assigned_to": "author-1",
            "decision": "approve",
        },
    )
    event = {
        "actor_user_id": "author-1",
        "after_ref": {"status": "approved"},
    }

    require_assigned_reviewer(proposal, _AUTHOR, is_separate_reviewer_required=False)
    assert has_execution_approval(proposal, event, is_separate_reviewer_required=False) is True
    with pytest.raises(ValidationFailed, match="reviewer other than"):
        require_assigned_reviewer(proposal, _AUTHOR, is_separate_reviewer_required=True)
    assert has_execution_approval(proposal, event, is_separate_reviewer_required=True) is False
