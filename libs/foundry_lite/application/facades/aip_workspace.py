"""Thin facade entrypoints for aip workspace workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.application.ports.tool_executor import ConfirmationPolicy, ToolEffect
from foundry_lite.application.services.aip.action_proposal import (
    ActionProposalRequest,
    ActionProposalResult,
    ActionProposalService,
)
from foundry_lite.application.services.aip.agent_runtime import (
    AgentRuntimeRequest,
    AgentRuntimeResult,
    AgentRuntimeService,
)
from foundry_lite.application.services.aip.approval_execution import (
    ApprovalExecutionRequest,
    ApprovalExecutionResult,
    ApprovalExecutionService,
)
from foundry_lite.application.services.aip.builder_runtime import (
    BuilderRuntimeRequest,
    BuilderRuntimeResult,
    BuilderRuntimeService,
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
from foundry_lite.application.services.aip.logic_runtime import (
    LogicBlock,
    LogicRunRequest,
    LogicRunResult,
    LogicRuntimeService,
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
        visual_builder: VisualBuilderService,
        citation: CitationService,
    ) -> None:
        self._agent_runtime = agent_runtime
        self._action_proposal = action_proposal
        self._approval_execution = approval_execution
        self._builder_runtime = builder_runtime
        self._logic_runtime = logic_runtime
        self._evals = evals
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

    def resolve_citation_navigation(
        self,
        *,
        navigation_ref: str,
        ctx: RequestContext | None = None,
    ) -> CitationNavigationResolveResult:
        return self._citation.resolve_navigation(ctx or RequestContext(), navigation_ref)


def _builder_request_from_payload(payload: Mapping[str, object]) -> VisualBuilderDraftRequest:
    return VisualBuilderDraftRequest(
        agent_version_id=_text(payload, "agentVersionId"),
        release_channel=_text(payload, "releaseChannel"),
        model_alias_version=_text(payload, "modelAliasVersion"),
        prompt_version_id=_text(payload, "promptVersionId"),
        context_sources=tuple(_builder_context_source(item) for item in _mapping_items(payload, "contextSources")),
        tool_manifest=tuple(_builder_tool_spec(item) for item in _mapping_items(payload, "toolManifest")),
        logic_blocks=tuple(_builder_logic_block(item) for item in _mapping_items(payload, "logicBlocks")),
        eval_axes=_text_items(payload, "evalAxes"),
        agent_allowed_actions=_text_items(payload, "agentAllowedActions"),
        max_logic_blocks=_int(payload, "maxLogicBlocks"),
    )


def _builder_runtime_request_from_payload(payload: Mapping[str, object]) -> BuilderRuntimeRequest:
    draft = _builder_request_from_payload(payload)
    logic_run_id = _text(payload, "logicRunId")
    return BuilderRuntimeRequest(
        logic_run_id=logic_run_id,
        ai_run_id=_optional_text(payload, "aiRunId"),
        session_id=_optional_text(payload, "sessionId"),
        draft=draft,
        input_json=_mapping(payload, "inputJson"),
        user_message=_text_default(payload, "userMessage", ""),
        agent_allowed_tools=_text_items(payload, "agentAllowedTools"),
        model_allowed_classifications=_text_items_default(
            payload, "modelAllowedClassifications", ("public", "internal")
        ),
        policy_version=_text_default(payload, "policyVersion", "policy-v1"),
    )


def _agent_runtime_request_from_payload(payload: Mapping[str, object]) -> AgentRuntimeRequest:
    return AgentRuntimeRequest(
        agent_run_id=_text_default(payload, "agentRunId", "agent-run-default"),
        agent_version_id=_text(payload, "agentVersionId"),
        model_alias=_text_default(payload, "modelAlias", _model_alias_from_version(payload)),
        prompt_version_id=_text(payload, "promptVersionId"),
        user_message=_text(payload, "userMessage"),
        agent_instruction=_text_default(payload, "agentInstruction", "Answer the operator using cited context."),
        security_partition=_text(payload, "securityPartition"),
        allowed_security_partitions=_text_items_default(
            payload,
            "allowedSecurityPartitions",
            (_text(payload, "securityPartition"),),
        ),
        state_json=_mapping(payload, "stateJson"),
        output_schema=_optional_mapping(payload, "outputSchema"),
        ai_run_id=_optional_text(payload, "aiRunId"),
        session_id=_optional_text(payload, "sessionId"),
        ontology_version_id=_text_default(payload, "ontologyVersionId", "active-ontology"),
        environment=_text_default(payload, "environment", "prod"),
        data_classification=_text_default(payload, "dataClassification", "internal"),
        allowed_classifications=_optional_text_items(payload, "modelAllowedClassifications"),
        region_requirement=_optional_text(payload, "regionRequirement"),
        max_context_items=_int_default(payload, "maxContextItems", 4),
        max_context_tokens=_int_default(payload, "maxContextTokens", 1200),
        max_model_calls=_int_default(payload, "maxModelCalls", 1),
        max_loop_iterations=_int_default(payload, "maxLoopIterations", 1),
        max_tool_calls=_int_default(payload, "maxToolCalls", 0),
        max_tool_output_bytes=_int_default(payload, "maxToolOutputBytes", 4096),
        max_output_tokens=_int_default(payload, "maxOutputTokens", 512),
        policy_version=_text_default(payload, "policyVersion", "policy-v1"),
        tool_manifest=tuple(_builder_tool_spec(item) for item in _mapping_items_default(payload, "toolManifest")),
        agent_allowed_tools=_text_items(payload, "agentAllowedTools"),
        agent_allowed_actions=_text_items(payload, "agentAllowedActions"),
    )


def _model_alias_from_version(payload: Mapping[str, object]) -> str:
    alias_version = _text_default(payload, "modelAliasVersion", "default-completion")
    return alias_version.split("@", maxsplit=1)[0]


def _builder_context_source(payload: Mapping[str, object]) -> VisualBuilderContextSource:
    return VisualBuilderContextSource(
        source_id=_text(payload, "sourceId"),
        kind=_text(payload, "kind"),
        security_partition=_text(payload, "securityPartition"),
        selected_properties=_text_items(payload, "selectedProperties"),
        token_budget=_int(payload, "tokenBudget"),
    )


def _builder_tool_spec(payload: Mapping[str, object]) -> ToolSpec:
    return ToolSpec(
        tool_id=_text(payload, "toolId"),
        version=_text(payload, "version"),
        input_schema=_mapping(payload, "inputSchema"),
        output_schema=_mapping(payload, "outputSchema"),
        effect=cast(ToolEffect, _text(payload, "effect")),
        required_permission=_text_default(payload, "requiredPermission", "object:read"),
        confirmation_policy=cast(ConfirmationPolicy, _text(payload, "confirmationPolicy")),
        object_type_allowlist=_text_items(payload, "objectTypeAllowlist"),
        property_allowlist=_text_items(payload, "propertyAllowlist"),
        timeout_seconds=_int_default(payload, "timeoutSeconds", 30),
        max_result_items=_int_default(payload, "maxResultItems", 50),
        result_classification=_text_default(payload, "resultClassification", "public"),
        status=_text_default(payload, "status", "published"),
    )


def _builder_logic_block(payload: Mapping[str, object]) -> LogicBlock:
    return LogicBlock(
        block_id=_text(payload, "blockId"),
        kind=_text(payload, "kind"),
        inputs=_mapping(payload, "inputs"),
        depends_on=_text_items(payload, "dependsOn"),
    )


def _mapping_items(payload: Mapping[str, object], key: str) -> tuple[Mapping[str, object], ...]:
    return tuple(cast(Sequence[Mapping[str, object]], payload[key]))


def _mapping_items_default(payload: Mapping[str, object], key: str) -> tuple[Mapping[str, object], ...]:
    if key not in payload:
        return ()
    return _mapping_items(payload, key)


def _text_items(payload: Mapping[str, object], key: str) -> tuple[str, ...]:
    return tuple(cast(Sequence[str], payload.get(key, ())))


def _text_items_default(payload: Mapping[str, object], key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    if key not in payload:
        return default
    return _text_items(payload, key)


def _optional_text_items(payload: Mapping[str, object], key: str) -> tuple[str, ...] | None:
    if key not in payload or payload.get(key) is None:
        return None
    return _text_items(payload, key)


def _mapping(payload: Mapping[str, object], key: str) -> Mapping[str, object]:
    return cast(Mapping[str, object], payload.get(key, {}))


def _optional_mapping(payload: Mapping[str, object], key: str) -> Mapping[str, object] | None:
    value = payload.get(key)
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else None


def _text(payload: Mapping[str, object], key: str) -> str:
    return cast(str, payload[key])


def _optional_text(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _text_default(payload: Mapping[str, object], key: str, default: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) and value else default


def _int(payload: Mapping[str, object], key: str) -> int:
    return cast(int, payload[key])


def _int_default(payload: Mapping[str, object], key: str, default: int) -> int:
    value = payload.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else default
