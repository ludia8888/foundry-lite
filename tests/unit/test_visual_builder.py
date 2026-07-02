"""Unit tests for the AIP Visual Builder validation slice (P0l, §14.6)."""

from __future__ import annotations

from typing import Any

from foundry_lite.application.ports.tool_executor import ConfirmationPolicy, ToolEffect
from foundry_lite.application.services.aip.logic_runtime import LogicBlock
from foundry_lite.application.services.aip.tool_broker import ToolSpec
from foundry_lite.application.services.aip.visual_builder import VisualBuilderContextSource, _has_cycle
from foundry_lite.domain.context import RequestContext

_CTX = RequestContext(
    tenant_id="tenant-demo",
    actor_user_id="ops-user",
    roles=("admin", "ops_manager", "data_engineer"),
    request_id="req-visual-builder",
)


def test_visual_builder_ready_draft_pins_context_tool_logic_and_eval(foundry: Any) -> None:
    result = foundry.aip.validate_builder_draft(
        agent_version_id="agent.order-ops.v1",
        release_channel="dev",
        model_alias_version="gpt-governed@v1",
        prompt_version_id="prompt-order-copilot@v1",
        context_sources=(_context(),),
        tool_manifest=(_tool(),),
        logic_blocks=_safe_blocks(),
        eval_axes=("retrieval", "answer", "citation", "tool", "action", "security"),
        agent_allowed_actions=("ApproveOrder",),
        ctx=_CTX,
    )

    payload = result.to_payload()
    assert result.validation_status == "ready"
    assert result.is_release_ready is True
    assert result.draft_hash.startswith("sha256:")
    assert result.graph_hash.startswith("sha256:")
    assert payload["safeBoundaries"][-1] == "release requires eval axes"


def test_visual_builder_blocks_direct_apply_action_and_dangerous_tools(foundry: Any) -> None:
    result = foundry.aip.validate_builder_draft(
        agent_version_id="agent.order-ops.v1",
        release_channel="dev",
        model_alias_version="gpt-governed@v1",
        prompt_version_id="prompt-order-copilot@v1",
        context_sources=(_context(),),
        tool_manifest=(_tool(tool_id="shell_execute", effect="WRITE", confirmation_policy="NONE"),),
        logic_blocks=(LogicBlock("apply", "ApplyAction", inputs={"actionType": "ApproveOrder"}),),
        eval_axes=("security",),
        agent_allowed_actions=("ApproveOrder",),
        ctx=_CTX,
    )

    codes = {issue.code for issue in result.blocking_issues}
    assert result.validation_status == "blocked"
    assert "direct_vendor_tool_denied" in codes
    assert "write_tool_denied" in codes
    assert "direct_apply_action_blocked" in codes


def test_visual_builder_blocks_direct_vendor_tool_ids(foundry: Any) -> None:
    result = foundry.aip.validate_builder_draft(
        agent_version_id="agent.order-ops.v1",
        release_channel="dev",
        model_alias_version="gpt-governed@v1",
        prompt_version_id="prompt-order-copilot@v1",
        context_sources=(_context(),),
        tool_manifest=(_tool(tool_id="http.request"),),
        logic_blocks=(
            LogicBlock("input", "Input"),
            LogicBlock("output", "Output", inputs={"fromBlock": "input"}, depends_on=("input",)),
        ),
        eval_axes=("security", "action"),
        agent_allowed_actions=("ApproveOrder",),
        ctx=_CTX,
    )

    assert result.validation_status == "blocked"
    assert {issue.code for issue in result.blocking_issues} == {"direct_vendor_tool_denied"}


def test_visual_builder_blocks_cross_tenant_context_partition(foundry: Any) -> None:
    result = foundry.aip.validate_builder_draft(
        agent_version_id="agent.order-ops.v1",
        release_channel="dev",
        model_alias_version="gpt-governed@v1",
        prompt_version_id="prompt-order-copilot@v1",
        context_sources=(_context(security_partition="tenant-other:internal"),),
        tool_manifest=(_tool(),),
        logic_blocks=_safe_blocks(),
        eval_axes=("retrieval", "answer", "tool"),
        agent_allowed_actions=("ApproveOrder",),
        ctx=_CTX,
    )

    assert {issue.code for issue in result.blocking_issues} == {"tenant_partition_mismatch"}


def test_visual_builder_stable_release_requires_security_and_action_eval(foundry: Any) -> None:
    result = foundry.aip.validate_builder_draft(
        agent_version_id="agent.order-ops.v1",
        release_channel="stable",
        model_alias_version="gpt-governed@v1",
        prompt_version_id="prompt-order-copilot@v1",
        context_sources=(_context(),),
        tool_manifest=(_tool(),),
        logic_blocks=_safe_blocks(),
        eval_axes=("retrieval", "answer"),
        agent_allowed_actions=("ApproveOrder",),
        ctx=_CTX,
    )

    assert result.validation_status == "blocked"
    assert result.blocking_issues[0].code == "stable_requires_security_action_eval"


def test_visual_builder_blocks_missing_logic_dependency(foundry: Any) -> None:
    result = foundry.aip.validate_builder_draft(
        agent_version_id="agent.order-ops.v1",
        release_channel="dev",
        model_alias_version="gpt-governed@v1",
        prompt_version_id="prompt-order-copilot@v1",
        context_sources=(_context(),),
        tool_manifest=(_tool(),),
        logic_blocks=(LogicBlock("output", "Output", inputs={"fromBlock": "missing"}, depends_on=("missing",)),),
        eval_axes=("retrieval", "answer"),
        agent_allowed_actions=("ApproveOrder",),
        ctx=_CTX,
    )

    codes = {issue.code for issue in result.blocking_issues}
    assert {"missing_dependency", "output_source_missing"} <= codes


def test_visual_builder_requires_output_source(foundry: Any) -> None:
    result = foundry.aip.validate_builder_draft(
        agent_version_id="agent.order-ops.v1",
        release_channel="dev",
        model_alias_version="gpt-governed@v1",
        prompt_version_id="prompt-order-copilot@v1",
        context_sources=(_context(),),
        tool_manifest=(_tool(),),
        logic_blocks=(LogicBlock("output", "Output"),),
        eval_axes=("retrieval", "answer"),
        agent_allowed_actions=("ApproveOrder",),
        ctx=_CTX,
    )

    assert {issue.code for issue in result.blocking_issues} == {"output_source_missing"}


def test_visual_builder_blocks_missing_definition_context_tool_logic_and_eval(foundry: Any) -> None:
    result = foundry.aip.validate_builder_draft(
        agent_version_id="",
        release_channel="preview",
        model_alias_version="",
        prompt_version_id="",
        context_sources=(),
        tool_manifest=(),
        logic_blocks=(),
        eval_axes=(),
        ctx=_CTX,
        max_logic_blocks=0,
    )

    payload = result.to_payload()
    codes = {issue.code for issue in result.blocking_issues}
    assert {
        "missing_field",
        "unsupported_release_channel",
        "invalid_logic_budget",
        "missing_context_source",
        "missing_tool_manifest",
        "missing_logic_graph",
        "missing_eval_axes",
    } <= codes
    assert payload["blockingIssues"][0]["severity"] == "blocker"


def test_visual_builder_reports_context_tool_warnings_and_manifest_errors(foundry: Any) -> None:
    result = foundry.aip.validate_builder_draft(
        agent_version_id="agent.order-ops.v1",
        release_channel="dev",
        model_alias_version="gpt-governed@v1",
        prompt_version_id="prompt-order-copilot@v1",
        context_sources=(
            VisualBuilderContextSource(
                source_id="",
                kind="ontology.object_set",
                security_partition="tenant-demo:internal",
                token_budget=0,
            ),
        ),
        tool_manifest=(
            _tool(),
            _tool(),
            _tool(tool_id="draft_tool", status="draft"),
            _tool(tool_id="unsupported_effect", effect="EXECUTE"),  # type: ignore[arg-type]
            _tool(tool_id="unsupported_policy", confirmation_policy="MAYBE"),  # type: ignore[arg-type]
            _tool(tool_id="proposal_without_review", effect="PROPOSE_WRITE", confirmation_policy="USER"),
            _tool(tool_id="read_with_review", confirmation_policy="USER"),
        ),
        logic_blocks=_safe_blocks(),
        eval_axes=("retrieval", "answer", "security", "action"),
        agent_allowed_actions=("ApproveOrder",),
        ctx=_CTX,
    )

    assert {
        "missing_context_id",
        "invalid_context_budget",
        "duplicate_tool_version",
        "tool_not_published",
        "unsupported_tool_effect",
        "unsupported_confirmation_policy",
        "proposal_requires_review",
    } <= {issue.code for issue in result.blocking_issues}
    assert {issue.code for issue in result.warnings} == {"read_tool_confirmation"}


def test_visual_builder_reports_logic_graph_guard_details(foundry: Any) -> None:
    result = foundry.aip.validate_builder_draft(
        agent_version_id="agent.order-ops.v1",
        release_channel="dev",
        model_alias_version="gpt-governed@v1",
        prompt_version_id="prompt-order-copilot@v1",
        context_sources=(_context(),),
        tool_manifest=(_tool(),),
        logic_blocks=(
            LogicBlock("", "Input"),
            LogicBlock("dup", "Input"),
            LogicBlock("dup", "Input"),
            LogicBlock("custom", "CustomBlock"),
            LogicBlock("deferred", "RetrieveObject"),
            LogicBlock("a", "Input", depends_on=("b",)),
            LogicBlock("b", "Input", depends_on=("a",)),
            LogicBlock("blank-dep", "Input", depends_on=("",)),
            LogicBlock("call-missing-ref", "CallFunction"),
            LogicBlock(
                "call-outside-manifest",
                "CallFunction",
                inputs={"toolId": "ontology.query_objects", "version": "2026-06-25"},
            ),
            LogicBlock("proposal-missing-action", "CreateActionProposal"),
            LogicBlock("proposal-denied-action", "CreateActionProposal", inputs={"actionType": "RefundOrder"}),
            LogicBlock("output", "Output", inputs={"fromBlock": "proposal-denied-action"}),
        ),
        eval_axes=("retrieval", "answer", "security", "action", "unknown_axis"),
        agent_allowed_actions=("ApproveOrder",),
        ctx=_CTX,
        max_logic_blocks=3,
    )

    assert {
        "logic_budget_exceeded",
        "missing_block_id",
        "duplicate_block",
        "unsupported_block",
        "block_kind_deferred",
        "missing_dependency_id",
        "cycle_detected",
        "tool_ref_missing",
        "tool_ref_not_in_manifest",
        "action_ref_missing",
        "action_not_allowed",
        "unsupported_eval_axis",
    } <= {issue.code for issue in result.blocking_issues}


def _safe_blocks() -> tuple[LogicBlock, ...]:
    return (
        LogicBlock("input", "Input", inputs={"objectId": "O-1001"}),
        LogicBlock("retrieve", "CallFunction", inputs=_tool_inputs(), depends_on=("input",)),
        LogicBlock("proposal", "CreateActionProposal", inputs={"actionType": "ApproveOrder"}, depends_on=("retrieve",)),
        LogicBlock("review", "HumanApproval", inputs={"reason": "approval_required"}, depends_on=("proposal",)),
        LogicBlock("output", "Output", inputs={"fromBlock": "proposal"}, depends_on=("proposal",)),
    )


def _context(security_partition: str = "tenant-demo:internal") -> VisualBuilderContextSource:
    return VisualBuilderContextSource(
        source_id="ctx-order-ops",
        kind="ontology.object_set",
        security_partition=security_partition,
        selected_properties=("orderId", "status"),
    )


def _tool(
    *,
    tool_id: str = "ontology.get_object",
    effect: ToolEffect = "READ",
    confirmation_policy: ConfirmationPolicy = "NONE",
    status: str = "published",
) -> ToolSpec:
    return ToolSpec(
        tool_id=tool_id,
        version="2026-06-25",
        input_schema={"type": "object", "required": ["object_type", "object_id"]},
        output_schema={"type": "object"},
        effect=effect,
        required_permission="objects:read",
        confirmation_policy=confirmation_policy,
        object_type_allowlist=("Order",),
        property_allowlist=("orderId", "status"),
        status=status,
    )


def _tool_inputs() -> dict[str, object]:
    return {
        "toolId": "ontology.get_object",
        "version": "2026-06-25",
        "arguments": {"object_type": "Order", "object_id": "O-1001"},
    }


def test_has_cycle_handles_deep_chains_without_recursion_error() -> None:
    # A deeply-nested valid chain must not raise RecursionError before reaching
    # validation; a recursive DFS crashes to a 500 in ``validate_draft``.
    depth = 6000
    chain = [LogicBlock("b0", "Input")]
    for index in range(1, depth):
        chain.append(LogicBlock(f"b{index}", "Condition", depends_on=(f"b{index - 1}",)))
    blocks = tuple(reversed(chain))
    by_id = {block.block_id: block for block in blocks}

    assert _has_cycle(blocks, by_id) is False


def test_has_cycle_detects_deep_cycle_iteratively() -> None:
    depth = 6000
    chain = [LogicBlock("b0", "Input", depends_on=(f"b{depth - 1}",))]
    for index in range(1, depth):
        chain.append(LogicBlock(f"b{index}", "Condition", depends_on=(f"b{index - 1}",)))
    blocks = tuple(reversed(chain))
    by_id = {block.block_id: block for block in blocks}

    assert _has_cycle(blocks, by_id) is True
