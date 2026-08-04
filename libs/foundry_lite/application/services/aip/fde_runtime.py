"""Governed multi-domain AI FDE turn orchestration over Agent Runtime."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from foundry_lite.application.ports.ai_run_repository import AiRunRepository
from foundry_lite.application.ports.context_provider import RetrievedContextItem
from foundry_lite.application.primitives import _new_id
from foundry_lite.application.services.aip.agent_runtime import (
    AgentRuntimeRequest,
    AgentRuntimeResult,
    AgentRuntimeService,
)
from foundry_lite.application.services.aip.fde_catalog import (
    FDE_MODE_ONTOLOGY,
    FDE_TOOL_DISCOVERY_EAGER,
    current_fde_mode,
    fde_catalog_payload,
    fde_tool_catalog,
    fde_tool_manifest,
)
from foundry_lite.application.services.aip.fde_context import FdeContextService
from foundry_lite.application.services.aip.tool_broker import ToolSpec
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied, ValidationFailed

JsonObject = Mapping[str, object]


@dataclass(frozen=True)
class FdeTurnRequest:
    user_message: str
    workspace_ref: str
    branch_id: str | None = None
    mode: str = FDE_MODE_ONTOLOGY
    capabilities: tuple[str, ...] = ()
    approved_tool_ids: tuple[str, ...] = ()
    attached_context_refs: tuple[str, ...] = ()
    model_alias: str = "default-completion"
    session_id: str | None = None
    agent_run_id: str | None = None
    tool_discovery: str = FDE_TOOL_DISCOVERY_EAGER
    max_context_items: int = 6
    max_context_tokens: int = 2400
    max_tool_calls: int = 4
    max_output_tokens: int = 512


@dataclass(frozen=True)
class FdeTurnResult:
    mode: str
    workspace_ref: str
    branch_id: str | None
    capabilities: tuple[str, ...]
    approved_tool_ids: tuple[str, ...]
    tool_discovery: str
    structured_operations: tuple[Mapping[str, object], ...]
    result: AgentRuntimeResult

    def to_payload(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "workspaceRef": self.workspace_ref,
            "branchId": self.branch_id,
            "capabilities": list(self.capabilities),
            "approvedToolIds": list(self.approved_tool_ids),
            "toolDiscovery": self.tool_discovery,
            "structuredOperations": [dict(item) for item in self.structured_operations],
            **self.result.to_payload(),
        }


@dataclass(frozen=True)
class _FdeRuntimeBuild:
    ctx: RequestContext
    request: FdeTurnRequest
    catalog: tuple[ToolSpec, ...]
    manifest: tuple[ToolSpec, ...]
    approved_tools: tuple[str, ...]
    attached_context: tuple[RetrievedContextItem, ...]
    run_id: str
    capabilities: tuple[str, ...]


class FdeRuntimeService(CoreService):
    """Run one bounded, permission-scoped FDE turn over native platform tools."""

    required_dependencies = ("engine", "policy", "ai_run_repository")
    required_collaborators = ("agent_runtime_service", "fde_context_service")
    agent_runtime_service: AgentRuntimeService
    fde_context_service: FdeContextService
    ai_run_repository: AiRunRepository

    def catalog(self, ctx: RequestContext) -> dict[str, object]:
        self.policy.require(ctx, "ontology:read")
        payload = fde_catalog_payload()
        all_tools = cast(list[dict[str, object]], payload["tools"])
        all_modes = cast(list[dict[str, object]], payload["modes"])
        tools = [item for item in all_tools if self.policy.decide(ctx, str(item["requiredPermission"])).allowed]
        mode_ids = {
            mode_id
            for item in tools
            if not str(item["toolId"]).startswith("fde.")
            for mode_id in cast(list[str], item["modeIds"])
        }
        modes = [mode for mode in all_modes if mode["modeId"] in mode_ids]
        return {**payload, "modes": modes, "tools": tools}

    def run_turn(self, ctx: RequestContext, request: FdeTurnRequest) -> FdeTurnResult:
        _validate_turn(request)
        self.fde_context_service.validate_scope(ctx, request.mode, request.workspace_ref)
        requested_catalog = fde_tool_catalog(request.mode, request.capabilities)
        requested_manifest = fde_tool_manifest(request.mode, request.capabilities, request.tool_discovery)
        catalog = self._permitted_tools(ctx, requested_catalog)
        manifest = self._permitted_tools(ctx, requested_manifest)
        if not any(not tool.tool_id.startswith("fde.") for tool in catalog):
            raise PermissionDenied("AI FDE mode has no tools available to the invoking user")
        approved = _approved_tools(request.approved_tool_ids, catalog)
        attached = self.fde_context_service.resolve(ctx, request.attached_context_refs, request.workspace_ref)
        runtime_request = _runtime_request(ctx, request, catalog, manifest, approved, attached)
        result = self.agent_runtime_service.run(ctx, runtime_request)
        capabilities = request.capabilities or current_fde_mode(request.mode).capabilities
        return FdeTurnResult(
            request.mode,
            request.workspace_ref,
            request.branch_id,
            capabilities,
            approved,
            request.tool_discovery,
            self._structured_operations(ctx, result.ai_run_id),
            result,
        )

    def _permitted_tools(self, ctx: RequestContext, tools: tuple[ToolSpec, ...]) -> tuple[ToolSpec, ...]:
        return tuple(tool for tool in tools if self.policy.decide(ctx, tool.required_permission).allowed)

    def _structured_operations(self, ctx: RequestContext, ai_run_id: str | None) -> tuple[Mapping[str, object], ...]:
        if ai_run_id is None:
            return ()
        with self.engine.begin() as conn:
            ledger = self.ai_run_repository.ledger_for_run(
                transaction=conn, tenant_id=ctx.tenant_id, ai_run_id=ai_run_id
            )
        calls = ledger.get("toolCalls", ()) if ledger else ()
        results = [item.get("result_json") for item in calls if isinstance(item, Mapping)]
        return tuple(
            item
            for item in results
            if isinstance(item, Mapping) and item.get("operationType") in {"plan", "clarification"}
        )


def fde_turn_request_from_payload(payload: JsonObject) -> FdeTurnRequest:
    workspace_ref = _workspace_ref(payload)
    return FdeTurnRequest(
        user_message=_text(payload, "userMessage"),
        workspace_ref=workspace_ref,
        branch_id=_branch_id(payload, workspace_ref),
        mode=_text_default(payload, "mode", FDE_MODE_ONTOLOGY),
        capabilities=_text_items(payload, "capabilities"),
        approved_tool_ids=_text_items(payload, "approvedToolIds"),
        attached_context_refs=_text_items(payload, "attachedContextRefs"),
        model_alias=_text_default(payload, "modelAlias", "default-completion"),
        session_id=_optional_text(payload, "sessionId"),
        agent_run_id=_optional_text(payload, "agentRunId"),
        tool_discovery=_text_default(payload, "toolDiscovery", FDE_TOOL_DISCOVERY_EAGER),
        max_context_items=_int_default(payload, "maxContextItems", 6),
        max_context_tokens=_int_default(payload, "maxContextTokens", 2400),
        max_tool_calls=_int_default(payload, "maxToolCalls", 4),
        max_output_tokens=_int_default(payload, "maxOutputTokens", 512),
    )


def _runtime_request(
    ctx: RequestContext,
    request: FdeTurnRequest,
    catalog: tuple[ToolSpec, ...],
    manifest: tuple[ToolSpec, ...],
    approved_tools: tuple[str, ...],
    attached_context: tuple[RetrievedContextItem, ...],
) -> AgentRuntimeRequest:
    run_id = request.agent_run_id or _new_id("aip-fde")
    capabilities = request.capabilities or current_fde_mode(request.mode).capabilities
    build = _FdeRuntimeBuild(ctx, request, catalog, manifest, approved_tools, attached_context, run_id, capabilities)
    return _build_runtime_request(build)


def _build_runtime_request(build: _FdeRuntimeBuild) -> AgentRuntimeRequest:
    request = build.request
    return AgentRuntimeRequest(
        agent_run_id=build.run_id,
        agent_version_id=f"ai-fde:{request.mode}:v2",
        model_alias=request.model_alias,
        prompt_version_id="ai-fde-system-v2",
        user_message=request.user_message,
        agent_instruction=_fde_instruction(request.workspace_ref, build.capabilities, build.approved_tools),
        security_partition=f"{build.ctx.tenant_id}:ai-fde",
        allowed_security_partitions=(f"{build.ctx.tenant_id}:ai-fde",),
        state_json=_runtime_state(request, build.capabilities),
        session_id=request.session_id or _session_id(build.ctx, request),
        ontology_version_id=_ontology_version(request),
        data_classification="internal",
        allowed_classifications=("public", "internal"),
        max_context_items=request.max_context_items,
        max_context_tokens=request.max_context_tokens,
        max_model_calls=request.max_tool_calls + 1,
        max_loop_iterations=request.max_tool_calls,
        max_tool_calls=request.max_tool_calls,
        max_tool_output_bytes=65536,
        max_output_tokens=request.max_output_tokens,
        policy_version="ai-fde-policy-v2",
        tool_manifest=build.manifest,
        tool_catalog=build.catalog,
        agent_allowed_tools=tuple(tool.tool_id for tool in build.manifest),
        runtime_profile="fde",
        branch_id=request.branch_id,
        fde_scope_ref=request.workspace_ref,
        tool_discovery=request.tool_discovery,
        approved_tool_ids=build.approved_tools,
        pinned_context_items=build.attached_context,
        allow_dynamic_retrieval=False,
    )


def _runtime_state(request: FdeTurnRequest, capabilities: tuple[str, ...]) -> dict[str, object]:
    return {
        "mode": request.mode,
        "workspaceRef": request.workspace_ref,
        "branchId": request.branch_id,
        "capabilities": list(capabilities),
        "toolDiscovery": request.tool_discovery,
        "attachedContextRefs": list(request.attached_context_refs),
    }


def _fde_instruction(scope_ref: str, capabilities: tuple[str, ...], approved: tuple[str, ...]) -> str:
    return (
        "You are Foundry-lite AI FDE. Operate only through server-owned governed tools. "
        f"Your selected workspace is {scope_ref}. Inspect before editing and present a structured plan when needed. "
        "Ontology and Pipeline changes must remain on branches and move through human proposals; never approve, merge, "
        "deploy, or activate your own work. Do not invent tool results or claim production changed. "
        f"Enabled capabilities: {', '.join(capabilities)}. Pre-approved tools: {', '.join(approved) or 'none'}."
    )


def _validate_turn(request: FdeTurnRequest) -> None:
    if not request.user_message.strip() or not request.workspace_ref.strip():
        raise ValidationFailed("AI FDE userMessage and workspaceRef are required")
    if len(request.attached_context_refs) > 20:
        raise ValidationFailed("AI FDE supports at most 20 attached context references")
    if request.max_context_items < 1 or request.max_context_items > 20:
        raise ValidationFailed("AI FDE maxContextItems must be between 1 and 20")
    if request.max_tool_calls < 1 or request.max_tool_calls > 8:
        raise ValidationFailed("AI FDE maxToolCalls must be between 1 and 8")


def _approved_tools(approved: tuple[str, ...], catalog: tuple[ToolSpec, ...]) -> tuple[str, ...]:
    allowed = {tool.tool_id for tool in catalog if tool.effect != "READ"}
    unknown = set(approved) - allowed
    if unknown:
        raise ValidationFailed(
            "AI FDE approval references an unavailable write tool",
            details={"tools": sorted(unknown)},
        )
    return tuple(tool.tool_id for tool in catalog if tool.tool_id in approved)


def _workspace_ref(payload: JsonObject) -> str:
    workspace = _optional_text(payload, "workspaceRef")
    if workspace:
        return workspace
    branch_id = _optional_text(payload, "branchId")
    if branch_id:
        return f"ontology-branch:{branch_id}"
    raise ValidationFailed("workspaceRef or legacy branchId is required")


def _branch_id(payload: JsonObject, workspace_ref: str) -> str | None:
    legacy = _optional_text(payload, "branchId")
    if legacy:
        return legacy
    if workspace_ref.startswith("ontology-branch:"):
        return workspace_ref.removeprefix("ontology-branch:")
    return None


def _ontology_version(request: FdeTurnRequest) -> str:
    return f"branch:{request.branch_id}" if request.branch_id else "active-ontology"


def _session_id(ctx: RequestContext, request: FdeTurnRequest) -> str:
    raw = f"{ctx.tenant_id}:{ctx.actor_user_id}:{request.mode}:{request.workspace_ref}"
    return f"aip-fde-session-{hashlib.sha256(raw.encode()).hexdigest()[:20]}"


def _text(payload: JsonObject, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValidationFailed(f"{key} is required")
    return value


def _text_default(payload: JsonObject, key: str, default: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) and value else default


def _optional_text(payload: JsonObject, key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _text_items(payload: JsonObject, key: str) -> tuple[str, ...]:
    value = payload.get(key, ())
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ValidationFailed(f"{key} must be a string list")
    if not all(isinstance(item, str) and item for item in value):
        raise ValidationFailed(f"{key} must be a string list")
    return tuple(item for item in value if isinstance(item, str))


def _int_default(payload: JsonObject, key: str, default: int) -> int:
    value = payload.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else default
