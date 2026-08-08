"""Thin facade entrypoints for aip workspace workflows."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.facades.aip_workspace_payloads import (
    agent_runtime_request_from_payload as _agent_runtime_request_from_payload,
)
from foundry_lite.application.facades.aip_workspace_payloads import (
    builder_request_from_payload as _builder_request_from_payload,
)
from foundry_lite.application.facades.aip_workspace_payloads import (
    builder_runtime_request_from_payload as _builder_runtime_request_from_payload,
)
from foundry_lite.application.ports import OsdkMcpStreamLease
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
from foundry_lite.application.services.aip.citation_service import (
    CitationNavigationResolveResult,
    CitationService,
)
from foundry_lite.application.services.aip.eval_service import (
    EvalCaseInput,
    EvalRunRequest,
    EvalRunResult,
    EvalService,
    ReleasePromotionRequest,
    ReleasePromotionResult,
)
from foundry_lite.application.services.aip.fde_mcp_service import FdeMcpGateway, FdeMcpToolCall
from foundry_lite.application.services.aip.logic_runtime import (
    LogicBlock,
    LogicRunRequest,
    LogicRunResult,
    LogicRuntimeService,
)
from foundry_lite.application.services.aip.runtime_services import (
    AgentRuntimeResult,
    AgentRuntimeService,
    BuilderRuntimeResult,
    BuilderRuntimeService,
    FdePilotService,
    FdeRuntimeService,
    FdeTurnResult,
    fde_turn_request_from_payload,
)
from foundry_lite.application.services.aip.tool_broker import ToolSpec
from foundry_lite.application.services.aip.visual_builder import (
    VisualBuilderContextSource,
    VisualBuilderDraftRequest,
    VisualBuilderService,
    VisualBuilderValidationResult,
)
from foundry_lite.domain.context import RequestContext


class AipWorkspace:
    """Facade for governed AI proposal workflows."""

    def __init__(
        self,
        agent_runtime: AgentRuntimeService,
        action_proposal: ActionProposalService,
        approval_execution: ApprovalExecutionService,
        builder_runtime: BuilderRuntimeService,
        logic_runtime: LogicRuntimeService,
        evals: EvalService,
        fde_runtime: FdeRuntimeService,
        fde_mcp: FdeMcpGateway,
        fde_pilot: FdePilotService,
        visual_builder: VisualBuilderService,
        citation: CitationService,
    ) -> None:
        self._agent_runtime = agent_runtime
        self._action_proposal = action_proposal
        self._approval_execution = approval_execution
        self._builder_runtime = builder_runtime
        self._logic_runtime = logic_runtime
        self._evals = evals
        self._fde_runtime = fde_runtime
        self._fde_mcp = fde_mcp
        self._fde_pilot = fde_pilot
        self._visual_builder = visual_builder
        self._citation = citation

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
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> ApprovalExecutionResult:
        return self._approval_execution.execute(
            ctx or RequestContext(),
            ApprovalExecutionRequest(
                review_id=review_id,
                expected_proposal_fingerprint=expected_proposal_fingerprint,
                idempotency_key=idempotency_key,
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

    def validate_builder_draft(
        self,
        *,
        agent_version_id: str,
        release_channel: str,
        model_alias_version: str,
        prompt_version_id: str,
        context_sources: tuple[VisualBuilderContextSource, ...],
        tool_manifest: tuple[ToolSpec, ...],
        logic_blocks: tuple[LogicBlock, ...],
        eval_axes: tuple[str, ...],
        ctx: RequestContext | None = None,
        agent_allowed_actions: tuple[str, ...] = (),
        max_logic_blocks: int = 25,
    ) -> VisualBuilderValidationResult:
        return self._visual_builder.validate_draft(
            ctx or RequestContext(),
            VisualBuilderDraftRequest(
                agent_version_id=agent_version_id,
                release_channel=release_channel,
                model_alias_version=model_alias_version,
                prompt_version_id=prompt_version_id,
                context_sources=context_sources,
                tool_manifest=tool_manifest,
                logic_blocks=logic_blocks,
                eval_axes=eval_axes,
                agent_allowed_actions=agent_allowed_actions,
                max_logic_blocks=max_logic_blocks,
            ),
        )

    def validate_builder_payload(
        self,
        *,
        payload: Mapping[str, object],
        ctx: RequestContext | None = None,
    ) -> VisualBuilderValidationResult:
        return self._visual_builder.validate_draft(ctx or RequestContext(), _builder_request_from_payload(payload))

    def run_builder_payload(
        self,
        *,
        payload: Mapping[str, object],
        ctx: RequestContext | None = None,
    ) -> BuilderRuntimeResult:
        return self._builder_runtime.run(ctx or RequestContext(), _builder_runtime_request_from_payload(payload))

    def run_agent_payload(
        self,
        *,
        payload: Mapping[str, object],
        ctx: RequestContext | None = None,
    ) -> AgentRuntimeResult:
        return self._agent_runtime.run(ctx or RequestContext(), _agent_runtime_request_from_payload(payload))

    def fde_catalog(self, *, ctx: RequestContext | None = None) -> Mapping[str, object]:
        return self._fde_runtime.catalog(ctx or RequestContext())

    def run_fde_payload(
        self,
        *,
        payload: Mapping[str, object],
        ctx: RequestContext | None = None,
    ) -> FdeTurnResult:
        return self._fde_runtime.run_turn(
            ctx or RequestContext(),
            fde_turn_request_from_payload(payload),
        )

    def fde_mcp_tools(
        self,
        application_id: str,
        *,
        session_id: str | None = None,
        discovery_mode: str = "eager",
        ctx: RequestContext | None = None,
    ) -> Mapping[str, object]:
        return self._fde_mcp.list_tools(
            ctx or RequestContext(), application_id, session_id=session_id, discovery_mode=discovery_mode
        )

    def activate_fde_mcp_lazy_discovery(
        self,
        application_id: str,
        session_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> None:
        self._fde_mcp.activate_lazy_discovery(ctx or RequestContext(), application_id, session_id)

    def consume_fde_mcp_endpoint_rate_limit(
        self,
        application_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> None:
        self._fde_mcp.consume_endpoint_rate_limit(ctx or RequestContext(), application_id)

    def run_fde_mcp_tool(
        self,
        request: FdeMcpToolCall,
        *,
        ctx: RequestContext | None = None,
    ) -> Mapping[str, object]:
        return self._fde_mcp.execute_tool(ctx or RequestContext(), request)

    def open_fde_mcp_session(
        self,
        application_id: str,
        session_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> Mapping[str, object]:
        return self._fde_mcp.open_session(ctx or RequestContext(), application_id, session_id)

    def fde_mcp_session_events(
        self,
        application_id: str,
        session_id: str,
        *,
        after_sequence: int = 0,
        ctx: RequestContext | None = None,
    ) -> list[Mapping[str, object]]:
        return [
            dict(event)
            for event in self._fde_mcp.session_events(
                ctx or RequestContext(),
                application_id,
                session_id,
                after_sequence=after_sequence,
            )
        ]

    def claim_fde_mcp_session_stream(
        self,
        application_id: str,
        session_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> OsdkMcpStreamLease:
        return self._fde_mcp.claim_session_stream(ctx or RequestContext(), application_id, session_id)

    def release_fde_mcp_session_stream(
        self,
        application_id: str,
        session_id: str,
        lease_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> bool:
        return self._fde_mcp.release_session_stream(ctx or RequestContext(), application_id, session_id, lease_id)

    def close_fde_mcp_session(
        self,
        application_id: str,
        session_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> Mapping[str, object]:
        return self._fde_mcp.close_session(ctx or RequestContext(), application_id, session_id)

    def approve_fde_mcp_confirmation(
        self,
        application_id: str,
        challenge_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> Mapping[str, object]:
        return self._fde_mcp.approve_confirmation(
            ctx or RequestContext(),
            application_id,
            challenge_id,
        )

    def plan_pilot_application(
        self, arguments: Mapping[str, object], *, ctx: RequestContext | None = None
    ) -> Mapping[str, object]:
        actor = ctx or RequestContext()
        self._fde_runtime.catalog(actor)
        return self._fde_pilot.plan(arguments)

    def generate_pilot_application(
        self,
        plan: Mapping[str, object],
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> Mapping[str, object]:
        return self._fde_pilot.generate(ctx or RequestContext(), plan, idempotency_key)

    def get_pilot_application(self, rid: str, *, ctx: RequestContext | None = None) -> Mapping[str, object]:
        return self._fde_pilot.get_bundle(ctx or RequestContext(), rid)

    def resolve_citation_navigation(
        self,
        *,
        navigation_ref: str,
        ctx: RequestContext | None = None,
    ) -> CitationNavigationResolveResult:
        return self._citation.resolve_navigation(ctx or RequestContext(), navigation_ref)
