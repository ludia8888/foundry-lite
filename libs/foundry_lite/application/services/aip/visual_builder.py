"""AIP Visual Builder validation (P0l, §14.6).

This first builder slice is intentionally read-only. It validates an authored
agent/context/tool/Logic/eval draft against the governed AIP boundaries before
any runtime execution or release promotion can happen.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from foundry_lite.application.services.aip.eval_identifiers import hash_json
from foundry_lite.application.services.aip.logic_runtime import LogicBlock
from foundry_lite.application.services.aip.tool_broker import ToolSpec, is_direct_vendor_tool_id
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext

JsonObject = Mapping[str, object]
_CANONICAL_BLOCKS = frozenset(
    {
        "Input",
        "RetrieveObject",
        "QueryObjects",
        "TraverseLinks",
        "RetrieveContent",
        "CallLLM",
        "CallFunction",
        "Condition",
        "Map",
        "Reduce",
        "UpdateState",
        "CreateActionProposal",
        "ApplyAction",
        "HumanApproval",
        "Output",
    }
)
_RUNTIME_READY_BLOCKS = frozenset(
    {"Input", "CallFunction", "Condition", "CreateActionProposal", "HumanApproval", "Output"}
)
_TOOL_EFFECTS = frozenset({"READ", "PROPOSE_WRITE", "WRITE"})
_CONFIRMATION_POLICIES = frozenset({"NONE", "USER", "HUMAN_REVIEW"})
_RELEASE_CHANNELS = frozenset({"draft", "dev", "canary", "stable", "sunset"})
_SUPPORTED_AXES = frozenset({"retrieval", "answer", "citation", "tool", "action", "security", "operations"})
_STABLE_REQUIRED_AXES = frozenset({"security", "action"})
_SAFE_BOUNDARIES = (
    "model alias version pinned",
    "prompt version pinned",
    "context sources tenant partition checked",
    "tool manifest denies generic executors",
    "logic graph validated before execution",
    "writes require proposal and human review",
    "release requires eval axes",
)


@dataclass(frozen=True)
class VisualBuilderContextSource:
    source_id: str
    kind: str
    security_partition: str
    selected_properties: tuple[str, ...] = ()
    token_budget: int = 800


@dataclass(frozen=True)
class VisualBuilderDraftRequest:
    agent_version_id: str
    release_channel: str
    model_alias_version: str
    prompt_version_id: str
    context_sources: tuple[VisualBuilderContextSource, ...]
    tool_manifest: tuple[ToolSpec, ...]
    logic_blocks: tuple[LogicBlock, ...]
    eval_axes: tuple[str, ...]
    agent_allowed_actions: tuple[str, ...] = ()
    max_logic_blocks: int = 25


@dataclass(frozen=True)
class VisualBuilderIssue:
    code: str
    section: str
    severity: str
    message: str

    def to_payload(self) -> dict[str, object]:
        return {"code": self.code, "section": self.section, "severity": self.severity, "message": self.message}


@dataclass(frozen=True)
class VisualBuilderValidationResult:
    agent_version_id: str
    release_channel: str
    validation_status: str
    draft_hash: str
    graph_hash: str
    is_release_ready: bool
    node_count: int
    context_source_count: int
    tool_count: int
    eval_axis_count: int
    blocking_issues: tuple[VisualBuilderIssue, ...]
    warnings: tuple[VisualBuilderIssue, ...]
    safe_boundaries: tuple[str, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "agentVersionId": self.agent_version_id,
            "releaseChannel": self.release_channel,
            "validationStatus": self.validation_status,
            "draftHash": self.draft_hash,
            "graphHash": self.graph_hash,
            "releaseReady": self.is_release_ready,
            "nodeCount": self.node_count,
            "contextSourceCount": self.context_source_count,
            "toolCount": self.tool_count,
            "evalAxisCount": self.eval_axis_count,
            "blockingIssues": [issue.to_payload() for issue in self.blocking_issues],
            "warnings": [issue.to_payload() for issue in self.warnings],
            "safeBoundaries": list(self.safe_boundaries),
        }


class VisualBuilderService(CoreService):
    """Validate authored AIP Builder drafts without mutating runtime state."""

    required_dependencies = ()
    required_collaborators = ()

    def validate_draft(self, ctx: RequestContext, request: VisualBuilderDraftRequest) -> VisualBuilderValidationResult:
        issues = _definition_issues(request)
        issues += _context_issues(ctx, request.context_sources)
        issues += _tool_manifest_issues(request.tool_manifest)
        issues += _logic_issues(request)
        issues += _eval_release_issues(request)
        blocking = tuple(issue for issue in issues if issue.severity == "blocker")
        warnings = tuple(issue for issue in issues if issue.severity != "blocker")
        return _result(request, blocking, warnings)


def _definition_issues(request: VisualBuilderDraftRequest) -> tuple[VisualBuilderIssue, ...]:
    issues = [_missing_issue(name) for name, value in _definition_fields(request) if not value]
    if request.release_channel not in _RELEASE_CHANNELS:
        issues.append(_issue("unsupported_release_channel", "release", f"{request.release_channel} is not supported"))
    if request.max_logic_blocks <= 0:
        issues.append(_issue("invalid_logic_budget", "logic", "max_logic_blocks must be positive"))
    return tuple(issues)


def _definition_fields(request: VisualBuilderDraftRequest) -> tuple[tuple[str, str], ...]:
    return (
        ("agent_version_id", request.agent_version_id),
        ("model_alias_version", request.model_alias_version),
        ("prompt_version_id", request.prompt_version_id),
    )


def _context_issues(
    ctx: RequestContext, sources: Sequence[VisualBuilderContextSource]
) -> tuple[VisualBuilderIssue, ...]:
    if not sources:
        return (_issue("missing_context_source", "context", "at least one context source is required"),)
    issues: list[VisualBuilderIssue] = []
    for source in sources:
        issues.extend(_single_context_issues(ctx, source))
    return tuple(issues)


def _single_context_issues(ctx: RequestContext, source: VisualBuilderContextSource) -> tuple[VisualBuilderIssue, ...]:
    issues: list[VisualBuilderIssue] = []
    if not source.source_id:
        issues.append(_issue("missing_context_id", "context", "context source_id is required"))
    if not source.security_partition.startswith(f"{ctx.tenant_id}:"):
        issues.append(_issue("tenant_partition_mismatch", "context", "context partition must match caller tenant"))
    if source.token_budget <= 0:
        issues.append(_issue("invalid_context_budget", "context", "context token_budget must be positive"))
    return tuple(issues)


def _tool_manifest_issues(tools: Sequence[ToolSpec]) -> tuple[VisualBuilderIssue, ...]:
    if not tools:
        return (_issue("missing_tool_manifest", "tools", "at least one governed tool spec is required"),)
    issues: list[VisualBuilderIssue] = []
    seen: set[tuple[str, str]] = set()
    for tool in tools:
        key = (tool.tool_id, tool.version)
        issues.extend(_tool_identity_issues(tool, key, seen))
        issues.extend(_tool_effect_issues(tool))
        seen.add(key)
    return tuple(issues)


def _tool_identity_issues(
    tool: ToolSpec, key: tuple[str, str], seen: set[tuple[str, str]]
) -> tuple[VisualBuilderIssue, ...]:
    issues: list[VisualBuilderIssue] = []
    if key in seen:
        issues.append(_issue("duplicate_tool_version", "tools", f"{tool.tool_id}@{tool.version} is duplicated"))
    if is_direct_vendor_tool_id(tool.tool_id):
        issues.append(
            _issue(
                "direct_vendor_tool_denied",
                "tools",
                f"{tool.tool_id} must use governed connectors, webhooks, or action proposals",
            )
        )
    if tool.status != "published":
        issues.append(_issue("tool_not_published", "tools", f"{tool.tool_id}@{tool.version} is not published"))
    return tuple(issues)


def _tool_effect_issues(tool: ToolSpec) -> tuple[VisualBuilderIssue, ...]:
    if tool.effect not in _TOOL_EFFECTS:
        return (_issue("unsupported_tool_effect", "tools", f"{tool.effect} is not supported"),)
    if tool.confirmation_policy not in _CONFIRMATION_POLICIES:
        return (_issue("unsupported_confirmation_policy", "tools", f"{tool.confirmation_policy} is not supported"),)
    if tool.effect == "WRITE":
        return (_issue("write_tool_denied", "tools", "direct WRITE tools must use action proposals instead"),)
    if tool.effect == "PROPOSE_WRITE" and tool.confirmation_policy != "HUMAN_REVIEW":
        return (_issue("proposal_requires_review", "tools", "write proposals require HUMAN_REVIEW"),)
    if tool.effect == "READ" and tool.confirmation_policy != "NONE":
        return (_warning("read_tool_confirmation", "tools", "read tools normally use confirmation_policy NONE"),)
    return ()


def _logic_issues(request: VisualBuilderDraftRequest) -> tuple[VisualBuilderIssue, ...]:
    if not request.logic_blocks:
        return (_issue("missing_logic_graph", "logic", "Logic graph must contain at least one block"),)
    issues: list[VisualBuilderIssue] = []
    if len(request.logic_blocks) > request.max_logic_blocks:
        issues.append(_issue("logic_budget_exceeded", "logic", "Logic graph exceeds max_logic_blocks"))
    by_id, block_issues = _logic_block_index(request.logic_blocks)
    issues.extend(block_issues)
    issues.extend(_dependency_issues(request.logic_blocks, by_id))
    issues.extend(_block_safety_issues(request, by_id))
    return tuple(issues)


def _logic_block_index(
    blocks: Sequence[LogicBlock],
) -> tuple[dict[str, LogicBlock], tuple[VisualBuilderIssue, ...]]:
    by_id: dict[str, LogicBlock] = {}
    issues: list[VisualBuilderIssue] = []
    for block in blocks:
        if not block.block_id:
            issues.append(_issue("missing_block_id", "logic", "Logic block id is required"))
        elif block.block_id in by_id:
            issues.append(_issue("duplicate_block", "logic", f"{block.block_id} is duplicated"))
        else:
            by_id[block.block_id] = block
        issues.extend(_block_kind_issues(block))
    return by_id, tuple(issues)


def _block_kind_issues(block: LogicBlock) -> tuple[VisualBuilderIssue, ...]:
    if block.kind not in _CANONICAL_BLOCKS:
        return (_issue("unsupported_block", "logic", f"{block.kind} is not a canonical AIP Logic block"),)
    if block.kind not in _RUNTIME_READY_BLOCKS and block.kind != "ApplyAction":
        return (_issue("block_kind_deferred", "logic", f"{block.kind} is not executable in the current runtime slice"),)
    return ()


def _dependency_issues(blocks: Sequence[LogicBlock], by_id: Mapping[str, LogicBlock]) -> tuple[VisualBuilderIssue, ...]:
    issues: list[VisualBuilderIssue] = []
    for block in blocks:
        for dependency in block.depends_on:
            issue = _missing_dependency_issue(block, dependency, by_id)
            if issue is not None:
                issues.append(issue)
    if _has_cycle(blocks, by_id):
        issues.append(_issue("cycle_detected", "logic", "Logic graph contains a dependency cycle"))
    return tuple(issues)


def _missing_dependency_issue(
    block: LogicBlock, dependency_id: str, by_id: Mapping[str, LogicBlock]
) -> VisualBuilderIssue | None:
    if not dependency_id:
        return _issue("missing_dependency_id", "logic", f"{block.block_id} has a blank dependency")
    if dependency_id not in by_id:
        return _issue("missing_dependency", "logic", f"{block.block_id} depends on missing block {dependency_id}")
    return None


def _has_cycle(blocks: Sequence[LogicBlock], by_id: Mapping[str, LogicBlock]) -> bool:
    """Iterative DFS cycle detection so deep dependency chains cannot raise RecursionError."""
    visited: set[str] = set()
    for root in blocks:
        if root.block_id not in by_id or root.block_id in visited:
            continue
        on_path: set[str] = {root.block_id}
        stack: list[tuple[LogicBlock, int]] = [(root, 0)]
        while stack:
            block, index = stack[-1]
            if index < len(block.depends_on):
                stack[-1] = (block, index + 1)
                dependency = by_id.get(block.depends_on[index])
                if dependency is None or dependency.block_id in visited:
                    continue
                if dependency.block_id in on_path:
                    return True
                on_path.add(dependency.block_id)
                stack.append((dependency, 0))
            else:
                stack.pop()
                on_path.discard(block.block_id)
                visited.add(block.block_id)
    return False


def _block_safety_issues(
    request: VisualBuilderDraftRequest, by_id: Mapping[str, LogicBlock]
) -> tuple[VisualBuilderIssue, ...]:
    issues: list[VisualBuilderIssue] = []
    tool_keys = {(tool.tool_id, tool.version) for tool in request.tool_manifest if tool.status == "published"}
    for block in by_id.values():
        issues.extend(_call_function_issues(block, tool_keys))
        issues.extend(_action_block_issues(block, request.agent_allowed_actions))
        issues.extend(_output_source_issues(block, by_id))
    return tuple(issues)


def _call_function_issues(block: LogicBlock, tool_keys: set[tuple[str, str]]) -> tuple[VisualBuilderIssue, ...]:
    if block.kind != "CallFunction":
        return ()
    tool_id = _text_input(block, "toolId")
    version = _text_input(block, "version")
    if not tool_id or not version:
        return (_issue("tool_ref_missing", "logic", f"{block.block_id} must reference toolId and version"),)
    if (tool_id, version) not in tool_keys:
        return (_issue("tool_ref_not_in_manifest", "logic", f"{tool_id}@{version} is not in the published manifest"),)
    return ()


def _action_block_issues(block: LogicBlock, allowed_actions: Sequence[str]) -> tuple[VisualBuilderIssue, ...]:
    if block.kind == "ApplyAction":
        return (_issue("direct_apply_action_blocked", "logic", "ApplyAction needs a later approved signal path"),)
    if block.kind != "CreateActionProposal":
        return ()
    action_type = _text_input(block, "actionType")
    if not action_type:
        return (_issue("action_ref_missing", "logic", f"{block.block_id} must declare actionType"),)
    if action_type not in allowed_actions:
        return (_issue("action_not_allowed", "logic", f"{action_type} is not in the agent action allowlist"),)
    return ()


def _output_source_issues(block: LogicBlock, by_id: Mapping[str, LogicBlock]) -> tuple[VisualBuilderIssue, ...]:
    if block.kind != "Output":
        return ()
    source = _text_input(block, "fromBlock")
    if not source:
        return (_issue("output_source_missing", "logic", f"{block.block_id} must declare fromBlock"),)
    if source not in by_id:
        return (_issue("output_source_missing", "logic", f"Output source {source} does not exist"),)
    return ()


def _eval_release_issues(request: VisualBuilderDraftRequest) -> tuple[VisualBuilderIssue, ...]:
    issues: list[VisualBuilderIssue] = []
    axes = set(request.eval_axes)
    if not axes:
        issues.append(_issue("missing_eval_axes", "evals", "at least one eval axis is required"))
    issues.extend(_unsupported_axis_issues(axes))
    if request.release_channel == "stable" and not _STABLE_REQUIRED_AXES <= axes:
        issues.append(
            _issue("stable_requires_security_action_eval", "release", "stable requires security and action axes")
        )
    return tuple(issues)


def _unsupported_axis_issues(axes: set[str]) -> tuple[VisualBuilderIssue, ...]:
    return tuple(
        _issue("unsupported_eval_axis", "evals", f"{axis} is not supported") for axis in sorted(axes - _SUPPORTED_AXES)
    )


def _result(
    request: VisualBuilderDraftRequest,
    blocking: tuple[VisualBuilderIssue, ...],
    warnings: tuple[VisualBuilderIssue, ...],
) -> VisualBuilderValidationResult:
    return VisualBuilderValidationResult(
        agent_version_id=request.agent_version_id,
        release_channel=request.release_channel,
        validation_status="blocked" if blocking else "ready",
        draft_hash=hash_json(_draft_payload(request)),
        graph_hash=hash_json(_logic_payload(request.logic_blocks)),
        is_release_ready=not blocking,
        node_count=len(request.logic_blocks),
        context_source_count=len(request.context_sources),
        tool_count=len(request.tool_manifest),
        eval_axis_count=len(request.eval_axes),
        blocking_issues=blocking,
        warnings=warnings,
        safe_boundaries=_SAFE_BOUNDARIES,
    )


def _draft_payload(request: VisualBuilderDraftRequest) -> dict[str, object]:
    return {
        "agentVersionId": request.agent_version_id,
        "releaseChannel": request.release_channel,
        "modelAliasVersion": request.model_alias_version,
        "promptVersionId": request.prompt_version_id,
        "contextSources": [_context_payload(source) for source in request.context_sources],
        "toolManifest": [_tool_payload(tool) for tool in request.tool_manifest],
        "logicBlocks": _logic_payload(request.logic_blocks),
        "evalAxes": list(request.eval_axes),
        "agentAllowedActions": list(request.agent_allowed_actions),
    }


def _context_payload(source: VisualBuilderContextSource) -> dict[str, object]:
    return {
        "sourceId": source.source_id,
        "kind": source.kind,
        "securityPartition": source.security_partition,
        "selectedProperties": list(source.selected_properties),
        "tokenBudget": source.token_budget,
    }


def _tool_payload(tool: ToolSpec) -> dict[str, object]:
    return {
        "toolId": tool.tool_id,
        "version": tool.version,
        "effect": tool.effect,
        "confirmationPolicy": tool.confirmation_policy,
        "status": tool.status,
        "objectTypeAllowlist": list(tool.object_type_allowlist),
        "propertyAllowlist": list(tool.property_allowlist),
    }


def _logic_payload(blocks: Sequence[LogicBlock]) -> list[dict[str, object]]:
    return [
        {
            "blockId": block.block_id,
            "kind": block.kind,
            "inputs": dict(block.inputs),
            "dependsOn": list(block.depends_on),
        }
        for block in blocks
    ]


def _missing_issue(field_name: str) -> VisualBuilderIssue:
    return _issue("missing_field", "definition", f"{field_name} is required")


def _issue(code: str, section: str, message: str) -> VisualBuilderIssue:
    return VisualBuilderIssue(code=code, section=section, severity="blocker", message=message)


def _warning(code: str, section: str, message: str) -> VisualBuilderIssue:
    return VisualBuilderIssue(code=code, section=section, severity="warning", message=message)


def _text_input(block: LogicBlock, key: str) -> str | None:
    value = block.inputs.get(key)
    return value if isinstance(value, str) and value else None
