"""AIP Builder-to-runtime execution (P0m, §14.6/§8.11).

This slice turns a validated Visual Builder draft into one bounded interactive
Logic run. It does not add a new execution bypass: the draft is validated first,
the run is seeded in the AI ledger, read tools still go through ToolBroker, and
write intent still becomes an ActionProposal review.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from foundry_lite.application.ports.ai_run_repository import (
    AiContextItemRecord,
    AiExecutionRunRecord,
    AiMessageRecord,
    AiSessionRecord,
)
from foundry_lite.application.ports.transaction_context import AI_RUN_FAILED, AI_RUN_SUCCEEDED
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.aip.eval_identifiers import hash_json
from foundry_lite.application.services.aip.logic_runtime import (
    LogicRunRequest,
    LogicRunResult,
    LogicRuntimeError,
    LogicRuntimeService,
)
from foundry_lite.application.services.aip.tool_broker import ToolSpec
from foundry_lite.application.services.aip.visual_builder import (
    VisualBuilderContextSource,
    VisualBuilderDraftRequest,
    VisualBuilderService,
    VisualBuilderValidationResult,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.runtime_error_payloads import scrub_error_text
from foundry_lite.domain.context import RequestContext

JsonObject = Mapping[str, object]


@dataclass(frozen=True)
class BuilderRuntimeRequest:
    """Run one Builder draft through the bounded AIP Logic runtime."""

    logic_run_id: str
    draft: VisualBuilderDraftRequest
    input_json: JsonObject
    user_message: str = ""
    ai_run_id: str | None = None
    session_id: str | None = None
    agent_allowed_tools: tuple[str, ...] = ()
    model_allowed_classifications: tuple[str, ...] = ("public", "internal")
    policy_version: str = "policy-v1"


@dataclass(frozen=True)
class BuilderRuntimeResult:
    """Public result returned by the Builder runtime product surface."""

    logic_run_id: str
    ai_run_id: str | None
    run_status: str
    validation: VisualBuilderValidationResult
    logic_result: LogicRunResult | None
    error: JsonObject | None = None

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "logicRunId": self.logic_run_id,
            "aiRunId": self.ai_run_id,
            "runStatus": self.run_status,
            "validation": self.validation.to_payload(),
            "operations": _operations_ref(self.ai_run_id),
        }
        if self.logic_result is not None:
            payload["logic"] = _logic_payload(self.logic_result)
        if self.error is not None:
            payload["error"] = dict(self.error)
        return payload


class BuilderRuntimeService(CoreService):
    """Validate a Builder draft, seed AI ledger rows, then execute Logic."""

    required_dependencies = ("engine", "ai_run_repository")
    required_collaborators = ("logic_runtime_service", "visual_builder_service")
    logic_runtime_service: LogicRuntimeService
    visual_builder_service: VisualBuilderService

    def run(self, ctx: RequestContext, request: BuilderRuntimeRequest) -> BuilderRuntimeResult:
        validation = self.visual_builder_service.validate_draft(ctx, request.draft)
        if validation.validation_status != "ready":
            return BuilderRuntimeResult(
                logic_run_id=request.logic_run_id,
                ai_run_id=None,
                run_status="blocked",
                validation=validation,
                logic_result=None,
            )
        ai_run_id = request.ai_run_id or f"aip-builder-run-{_new_id('logic')}"
        self._seed_ledger(ctx, request, validation, ai_run_id)
        try:
            logic_result = self.logic_runtime_service.run(ctx, _logic_request(request, ai_run_id))
        except Exception as exc:
            self._finish_run(ctx, ai_run_id, "failed", _error_payload(exc))
            return BuilderRuntimeResult(
                logic_run_id=request.logic_run_id,
                ai_run_id=ai_run_id,
                run_status="failed",
                validation=validation,
                logic_result=None,
                error=_error_payload(exc),
            )
        self._finish_run(ctx, ai_run_id, "succeeded", None, logic_result)
        return BuilderRuntimeResult(
            logic_run_id=request.logic_run_id,
            ai_run_id=ai_run_id,
            run_status=logic_result.status,
            validation=validation,
            logic_result=logic_result,
        )

    def _seed_ledger(
        self,
        ctx: RequestContext,
        request: BuilderRuntimeRequest,
        validation: VisualBuilderValidationResult,
        ai_run_id: str,
    ) -> None:
        now = _now()
        session_id = request.session_id or f"aip-builder-session-{request.logic_run_id}"
        message = request.user_message or f"Run Builder draft {validation.draft_hash}"
        with self.engine.begin() as transaction:
            self.ai_run_repository.create_session(
                transaction=transaction,
                record=_session_record(ctx, request, session_id, now),
            )
            self.ai_run_repository.insert_message_or_get_existing(
                transaction=transaction,
                record=_message_record(ctx, request, session_id, message, validation, now),
            )
            self.ai_run_repository.create_execution_run(
                transaction=transaction,
                record=_run_record(ctx, request, validation, ai_run_id, session_id, now),
            )
            for index, context_source in enumerate(request.draft.context_sources, start=1):
                self.ai_run_repository.record_context_item(
                    transaction=transaction,
                    record=_context_item_record(ctx, ai_run_id, request.logic_run_id, context_source, index),
                )

    def _finish_run(
        self,
        ctx: RequestContext,
        ai_run_id: str,
        status: str,
        error: JsonObject | None,
        logic_result: LogicRunResult | None = None,
    ) -> None:
        if status == "failed":
            transition = AI_RUN_FAILED
        elif status == "succeeded":
            transition = AI_RUN_SUCCEEDED
        else:
            raise ValueError("Builder AI run status must be failed or succeeded")
        usage = _usage_payload(logic_result)
        with self.engine.begin() as transaction:
            self.ai_run_repository.update_execution_run_status(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                ai_run_id=ai_run_id,
                transition=transition,
                usage_json=usage,
                error_json=error,
                completed_at=_now(),
            )


def _logic_request(request: BuilderRuntimeRequest, ai_run_id: str) -> LogicRunRequest:
    return LogicRunRequest(
        logic_run_id=request.logic_run_id,
        ai_run_id=ai_run_id,
        blocks=request.draft.logic_blocks,
        input_json=request.input_json,
        tool_manifest=request.draft.tool_manifest,
        agent_allowed_tools=request.agent_allowed_tools,
        agent_allowed_actions=request.draft.agent_allowed_actions,
        model_allowed_classifications=request.model_allowed_classifications,
        policy_version=request.policy_version,
        max_blocks=request.draft.max_logic_blocks,
    )


def _session_record(ctx: RequestContext, request: BuilderRuntimeRequest, session_id: str, now: str) -> AiSessionRecord:
    return AiSessionRecord(
        id=session_id,
        tenant_id=ctx.tenant_id,
        agent_version_id=request.draft.agent_version_id,
        actor_user_id=ctx.actor_user_id,
        status="active",
        created_at=now,
        last_activity_at=now,
    )


def _message_record(
    ctx: RequestContext,
    request: BuilderRuntimeRequest,
    session_id: str,
    message: str,
    validation: VisualBuilderValidationResult,
    now: str,
) -> AiMessageRecord:
    return AiMessageRecord(
        id=f"aip-builder-message-{request.logic_run_id}",
        tenant_id=ctx.tenant_id,
        session_id=session_id,
        role="user",
        client_message_id=f"{request.logic_run_id}:builder-user-message",
        content_ref=f"aip-builder://{request.logic_run_id}/user-message",
        content_hash=hash_json({"message": message, "draftHash": validation.draft_hash}),
        created_at=now,
    )


def _run_record(
    ctx: RequestContext,
    request: BuilderRuntimeRequest,
    validation: VisualBuilderValidationResult,
    ai_run_id: str,
    session_id: str,
    now: str,
) -> AiExecutionRunRecord:
    return AiExecutionRunRecord(
        id=ai_run_id,
        tenant_id=ctx.tenant_id,
        session_id=session_id,
        agent_version_id=request.draft.agent_version_id,
        actor_user_id=ctx.actor_user_id,
        request_id=ctx.request_id,
        trace_id=f"aip-builder-trace-{request.logic_run_id}",
        status="running",
        ontology_version_id="active-ontology",
        model_alias_version=request.draft.model_alias_version,
        resolved_model_id=_model_id(request.draft.model_alias_version),
        resolved_model_revision=_model_revision(request.draft.model_alias_version),
        prompt_version_id=request.draft.prompt_version_id,
        compiled_prompt_hash=hash_json({"draftHash": validation.draft_hash, "input": dict(request.input_json)}),
        tool_manifest_hash=hash_json([_tool_payload(tool) for tool in request.draft.tool_manifest]),
        context_manifest_hash=hash_json([_context_payload(source) for source in request.draft.context_sources]),
        state_snapshot_hash=hash_json(dict(request.input_json)),
        policy_snapshot_hash=hash_json(
            {
                "policyVersion": request.policy_version,
                "modelAllowedClassifications": list(request.model_allowed_classifications),
            }
        ),
        budget_json={"maxLogicBlocks": request.draft.max_logic_blocks},
        usage_json=None,
        error_json=None,
        started_at=now,
        completed_at=None,
    )


def _context_item_record(
    ctx: RequestContext,
    ai_run_id: str,
    logic_run_id: str,
    source: VisualBuilderContextSource,
    index: int,
) -> AiContextItemRecord:
    payload = _context_payload(source)
    return AiContextItemRecord(
        id=f"aip-builder-context-{logic_run_id}-{index}",
        tenant_id=ctx.tenant_id,
        ai_run_id=ai_run_id,
        context_id=source.source_id,
        kind=source.kind,
        source_resource_type=_source_resource_type(source.kind),
        source_resource_id=source.source_id,
        source_version=f"builder-source-{index}",
        content_hash=hash_json(payload),
        retrieval_method="builder-preselected",
        relevance_score=1.0,
        security_partition=source.security_partition,
        token_estimate=source.token_budget,
        is_selected=True,
        omission_reason=None,
    )


def _logic_payload(result: LogicRunResult) -> dict[str, object]:
    return {
        "status": result.status,
        "graphHash": result.graph_hash,
        "resultHash": result.result_hash,
        "output": dict(result.output_json),
        "blockResults": [
            {
                "blockId": block.block_id,
                "kind": block.kind,
                "status": block.status,
                "outputHash": block.output_hash,
                "outputPreview": dict(block.output_preview),
            }
            for block in result.block_results
        ],
    }


def _operations_ref(ai_run_id: str | None) -> dict[str, object] | None:
    if ai_run_id is None:
        return None
    return {
        "runType": "ai",
        "runId": ai_run_id,
        "detailPath": f"/api/operations/runs/ai/{ai_run_id}",
    }


def _usage_payload(result: LogicRunResult | None) -> dict[str, object] | None:
    if result is None:
        return None
    return {
        "logicRunId": result.logic_run_id,
        "logicRunStatus": result.status,
        "blockCount": len(result.block_results),
        "graphHash": result.graph_hash,
        "resultHash": result.result_hash,
    }


def _error_payload(exc: Exception) -> dict[str, object]:
    reason = exc.reason if isinstance(exc, LogicRuntimeError) else exc.__class__.__name__
    return {"reason": reason, "detail": scrub_error_text(str(exc))[:240]}


def _model_id(alias_version: str) -> str:
    return alias_version.split("@", maxsplit=1)[0] or alias_version


def _model_revision(alias_version: str) -> str:
    if "@" in alias_version:
        revision = alias_version.rsplit("@", maxsplit=1)[1]
        if revision:
            return revision
    return "pinned-builder-runtime"


def _source_resource_type(kind: str) -> str:
    if "media" in kind:
        return "media_item_version"
    if "content" in kind:
        return "content_unit"
    return "object"


def _context_payload(context: VisualBuilderContextSource) -> dict[str, object]:
    return {
        "sourceId": context.source_id,
        "kind": context.kind,
        "securityPartition": context.security_partition,
        "selectedProperties": list(context.selected_properties),
        "tokenBudget": context.token_budget,
    }


def _tool_payload(spec: ToolSpec) -> dict[str, object]:
    return {
        "toolId": spec.tool_id,
        "version": spec.version,
        "effect": spec.effect,
        "requiredPermission": spec.required_permission,
        "confirmationPolicy": spec.confirmation_policy,
        "objectTypeAllowlist": list(spec.object_type_allowlist),
        "propertyAllowlist": list(spec.property_allowlist),
        "resultClassification": spec.result_classification,
        "status": spec.status,
    }
