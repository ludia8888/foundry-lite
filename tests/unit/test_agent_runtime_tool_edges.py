from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from foundry_lite.application.ports.language_model import ModelResponse, ModelToolCall
from foundry_lite.application.services.aip.agent_runtime_tools import (
    AgentRuntimeToolLoopError,
    AgentRuntimeToolRequest,
    _guard_action_proposal_spec,
    _guard_tool_call_budget,
    _int_arg,
    _mapping_arg,
    _optional_text_arg,
    _resolved_tool_ref,
    _response_schema,
    _text_arg,
    _text_tuple_arg,
    guard_final_response,
)
from foundry_lite.application.services.aip.tool_broker import ToolSpec


def _spec(
    *,
    tool_id: str = "action.propose",
    effect: str = "PROPOSE_WRITE",
    confirmation_policy: str = "HUMAN_REVIEW",
) -> ToolSpec:
    return ToolSpec(
        tool_id=tool_id,
        version="2026-07-28",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        effect=effect,  # type: ignore[arg-type]
        required_permission="action:propose",
        confirmation_policy=confirmation_policy,  # type: ignore[arg-type]
    )


def _response(*tool_calls: ModelToolCall) -> ModelResponse:
    return ModelResponse(
        provider="fake",
        resolved_model_id="model",
        resolved_model_revision="revision",
        content="answer",
        finish_reason="stop",
        input_tokens=1,
        output_tokens=1,
        normalized_tool_calls=tuple(tool_calls),
    )


def test_agent_runtime_tool_ref_supports_explicit_version_and_unique_manifest_match() -> None:
    manifest = (_spec(tool_id="ontology.get_object", effect="READ", confirmation_policy="NONE"),)

    assert _resolved_tool_ref(manifest, "ontology.get_object@v2") == ("ontology.get_object", "v2")
    assert _resolved_tool_ref(manifest, "ontology.get_object") == (
        "ontology.get_object",
        "2026-07-28",
    )
    with pytest.raises(AgentRuntimeToolLoopError) as missing:
        _resolved_tool_ref(manifest, "missing")
    assert missing.value.reason == "tool_not_in_agent_manifest"


@pytest.mark.parametrize(
    ("spec", "reason"),
    [
        (_spec(effect="READ"), "unsupported_tool_effect"),
        (_spec(tool_id="custom.propose"), "unsupported_action_proposal_tool"),
        (_spec(confirmation_policy="NONE"), "proposal_requires_human_review"),
    ],
)
def test_agent_runtime_action_proposal_requires_exact_governed_spec(
    spec: ToolSpec,
    reason: str,
) -> None:
    with pytest.raises(AgentRuntimeToolLoopError) as captured:
        _guard_action_proposal_spec(spec)
    assert captured.value.reason == reason


def test_agent_runtime_action_argument_helpers_accept_aliases_and_reject_bad_shapes() -> None:
    arguments = {
        "target_object_id": "O-1",
        "priority": "high",
        "expected_object_version": 7,
        "parameters": {"reason": "reviewed"},
        "evidence_context_ids": ["ctx-1"],
    }

    assert _text_arg(arguments, "targetObjectId", "target_object_id") == "O-1"
    assert _optional_text_arg(arguments, "priority") == "high"
    assert _optional_text_arg(arguments, "assigneeUserId") is None
    assert _int_arg(arguments, "expectedObjectVersion", "expected_object_version") == 7
    assert _mapping_arg(arguments, "parameters") == {"reason": "reviewed"}
    assert _text_tuple_arg(arguments, "evidenceContextIds", "evidence_context_ids") == ("ctx-1",)

    invalid_calls = (
        lambda: _text_arg({}, "targetObjectId"),
        lambda: _optional_text_arg({"priority": 3}, "priority"),
        lambda: _int_arg({"version": True}, "version"),
        lambda: _mapping_arg({"parameters": []}, "parameters"),
        lambda: _text_tuple_arg({"evidence": ["ctx-1", ""]}, "evidence"),
    )
    for invalid_call in invalid_calls:
        with pytest.raises(AgentRuntimeToolLoopError) as captured:
            invalid_call()
        assert captured.value.reason == "invalid_action_proposal_arguments"


def test_agent_runtime_tool_budget_and_followup_loop_fail_closed() -> None:
    calls = (
        ModelToolCall("ontology.get_object", "{}"),
        ModelToolCall("ontology.search", "{}"),
    )
    disabled = cast(AgentRuntimeToolRequest, SimpleNamespace(max_tool_calls=0))
    bounded = cast(AgentRuntimeToolRequest, SimpleNamespace(max_tool_calls=1))

    with pytest.raises(AgentRuntimeToolLoopError) as disabled_error:
        _guard_tool_call_budget(disabled, calls[:1])
    assert disabled_error.value.reason == "tool_calls_not_supported_in_readonly_runtime"

    with pytest.raises(AgentRuntimeToolLoopError) as budget_error:
        _guard_tool_call_budget(bounded, calls)
    assert budget_error.value.reason == "tool_call_budget_exceeded"

    with pytest.raises(AgentRuntimeToolLoopError) as loop_error:
        guard_final_response(_response(calls[0]))
    assert loop_error.value.reason == "tool_call_loop_limit_exceeded"


def test_agent_runtime_tool_response_schema_is_optional_and_canonical() -> None:
    assert _response_schema(None) is None
    assert _response_schema({}) is None
    assert _response_schema({"type": "object", "required": ["answer"]}) == ('{"required":["answer"],"type":"object"}')
