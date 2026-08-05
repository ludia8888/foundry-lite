"""Typed payload normalization helpers for the AIP workspace facade."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.application.ports.tool_executor import ConfirmationPolicy, ToolEffect
from foundry_lite.application.services.aip.logic_runtime import LogicBlock
from foundry_lite.application.services.aip.runtime_services import (
    AgentRuntimeRequest,
    BuilderRuntimeRequest,
)
from foundry_lite.application.services.aip.tool_broker import ToolSpec
from foundry_lite.application.services.aip.visual_builder import (
    VisualBuilderContextSource,
    VisualBuilderDraftRequest,
)


def builder_request_from_payload(payload: Mapping[str, object]) -> VisualBuilderDraftRequest:
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


def builder_runtime_request_from_payload(payload: Mapping[str, object]) -> BuilderRuntimeRequest:
    draft = builder_request_from_payload(payload)
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


def agent_runtime_request_from_payload(payload: Mapping[str, object]) -> AgentRuntimeRequest:
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
    return _text_default(payload, "modelAliasVersion", "default-completion").split("@", maxsplit=1)[0]


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
        description=_text_default(payload, "description", ""),
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
    return () if key not in payload else _mapping_items(payload, key)


def _text_items(payload: Mapping[str, object], key: str) -> tuple[str, ...]:
    return tuple(cast(Sequence[str], payload.get(key, ())))


def _text_items_default(payload: Mapping[str, object], key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    return default if key not in payload else _text_items(payload, key)


def _optional_text_items(payload: Mapping[str, object], key: str) -> tuple[str, ...] | None:
    return None if key not in payload or payload.get(key) is None else _text_items(payload, key)


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
