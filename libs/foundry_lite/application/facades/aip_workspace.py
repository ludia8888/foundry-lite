from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.services.aip.action_proposal import (
    ActionProposalRequest,
    ActionProposalResult,
    ActionProposalService,
)
from foundry_lite.application.services.aip.approval_execution import (
    ApprovalExecutionRequest,
    ApprovalExecutionResult,
    ApprovalExecutionService,
)
from foundry_lite.domain.context import RequestContext


class AipWorkspace:
    """Facade for governed AI proposal workflows."""

    def __init__(self, action_proposal: ActionProposalService, approval_execution: ApprovalExecutionService) -> None:
        self._action_proposal = action_proposal
        self._approval_execution = approval_execution

    def propose_action(
        self,
        *,
        originating_ai_run_id: str,
        action_type: str,
        target_object_type: str,
        target_object_id: str,
        expected_object_version: int,
        parameters: Mapping[str, object],
        evidence_context_ids: tuple[str, ...],
        agent_allowed_actions: tuple[str, ...],
        policy_version: str,
        expires_at: str,
        claim_text: str,
        ctx: RequestContext | None = None,
        originating_tool_call_id: str | None = None,
        priority: str = "normal",
        assignee_user_id: str | None = None,
    ) -> ActionProposalResult:
        return self._action_proposal.propose(
            ctx or RequestContext(),
            ActionProposalRequest(
                originating_ai_run_id=originating_ai_run_id,
                action_type=action_type,
                target_object_type=target_object_type,
                target_object_id=target_object_id,
                expected_object_version=expected_object_version,
                parameters=parameters,
                evidence_context_ids=evidence_context_ids,
                agent_allowed_actions=agent_allowed_actions,
                policy_version=policy_version,
                expires_at=expires_at,
                claim_text=claim_text,
                originating_tool_call_id=originating_tool_call_id,
                priority=priority,
                assignee_user_id=assignee_user_id,
            ),
        )

    def execute_approved_action(
        self,
        *,
        review_id: str,
        expected_proposal_fingerprint: str,
        ctx: RequestContext | None = None,
    ) -> ApprovalExecutionResult:
        return self._approval_execution.execute(
            ctx or RequestContext(),
            ApprovalExecutionRequest(
                review_id=review_id,
                expected_proposal_fingerprint=expected_proposal_fingerprint,
            ),
        )
