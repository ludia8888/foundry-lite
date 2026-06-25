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
from foundry_lite.application.services.aip.eval_service import (
    EvalCaseInput,
    EvalRunRequest,
    EvalRunResult,
    EvalService,
    ReleasePromotionRequest,
    ReleasePromotionResult,
)
from foundry_lite.application.services.aip.logic_runtime import (
    LogicBlock,
    LogicRunRequest,
    LogicRunResult,
    LogicRuntimeService,
)
from foundry_lite.application.services.aip.tool_broker import ToolSpec
from foundry_lite.domain.context import RequestContext


class AipWorkspace:
    """Facade for governed AI proposal workflows."""

    def __init__(
        self,
        action_proposal: ActionProposalService,
        approval_execution: ApprovalExecutionService,
        logic_runtime: LogicRuntimeService,
        evals: EvalService,
    ) -> None:
        self._action_proposal = action_proposal
        self._approval_execution = approval_execution
        self._logic_runtime = logic_runtime
        self._evals = evals

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

    def run_logic(
        self,
        *,
        logic_run_id: str,
        ai_run_id: str,
        blocks: tuple[LogicBlock, ...],
        input_json: Mapping[str, object],
        ctx: RequestContext | None = None,
        tool_manifest: tuple[ToolSpec, ...] = (),
        agent_allowed_tools: tuple[str, ...] = (),
        agent_allowed_actions: tuple[str, ...] = (),
        model_allowed_classifications: tuple[str, ...] = ("public",),
        policy_version: str = "policy-v1",
        max_blocks: int = 25,
    ) -> LogicRunResult:
        return self._logic_runtime.run(
            ctx or RequestContext(),
            LogicRunRequest(
                logic_run_id=logic_run_id,
                ai_run_id=ai_run_id,
                blocks=blocks,
                input_json=input_json,
                tool_manifest=tool_manifest,
                agent_allowed_tools=agent_allowed_tools,
                agent_allowed_actions=agent_allowed_actions,
                model_allowed_classifications=model_allowed_classifications,
                policy_version=policy_version,
                max_blocks=max_blocks,
            ),
        )

    def run_eval(
        self,
        *,
        eval_run_id: str,
        suite_api_name: str,
        suite_version: str,
        suite_description: str,
        agent_version_id: str,
        candidate_release_channel: str,
        cases: tuple[EvalCaseInput, ...],
        ctx: RequestContext | None = None,
        min_score: float = 1.0,
        required_axes: tuple[str, ...] = (),
    ) -> EvalRunResult:
        return self._evals.run_eval(
            ctx or RequestContext(),
            EvalRunRequest(
                eval_run_id=eval_run_id,
                suite_api_name=suite_api_name,
                suite_version=suite_version,
                suite_description=suite_description,
                agent_version_id=agent_version_id,
                candidate_release_channel=candidate_release_channel,
                cases=cases,
                min_score=min_score,
                required_axes=required_axes,
            ),
        )

    def promote_agent_release(
        self,
        *,
        agent_version_id: str,
        target_release_channel: str,
        eval_run_id: str,
        ctx: RequestContext | None = None,
        policy_version: str = "release-policy-v1",
    ) -> ReleasePromotionResult:
        return self._evals.promote_release(
            ctx or RequestContext(),
            ReleasePromotionRequest(
                agent_version_id=agent_version_id,
                target_release_channel=target_release_channel,
                eval_run_id=eval_run_id,
                policy_version=policy_version,
            ),
        )
