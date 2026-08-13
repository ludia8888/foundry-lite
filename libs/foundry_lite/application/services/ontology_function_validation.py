"""Function type ("Functions on Objects") parsing, validation, and import.

Ontology YAML may declare top-level ``functionTypes``: named, typed, and
permissioned Logic DAGs that persist per ontology version in the
``function_types`` table (mirroring ``interface_types``). The persisted
definition is self-contained — blocks plus governed tool manifest — so
execution never needs a per-request manifest.

Block and tool semantics are validated by delegating to the EXISTING visual
builder surface (``VisualBuilderService.validate_draft``); this module only
adapts one function definition into the draft shape the builder expects.
Non-logic draft fields a function does not declare (model alias, prompt,
context source, eval axes) receive fixed function-scoped placeholders that
always satisfy the builder's non-logic checks, so every logic/tool blocker
(unknown block kind, dangling output, unpublished tool, direct ApplyAction,
CreateActionProposal outside an — always empty — action allowlist) fails the
ontology apply exactly as it would fail a builder draft. Functions are
therefore READ/compute-only by construction.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from typing import cast

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.ontology_repository import (
    FunctionTypeDefinition,
    FunctionTypeRecord,
    OntologyJsonObject,
    OntologyRepository,
)
from foundry_lite.application.ports.tool_executor import ConfirmationPolicy, ToolEffect
from foundry_lite.application.primitives import _new_id
from foundry_lite.application.safe_expression import validate_action_request
from foundry_lite.application.services.aip.logic_runtime import LogicBlock
from foundry_lite.application.services.aip.tool_broker import ToolSpec
from foundry_lite.application.services.aip.visual_builder import (
    VisualBuilderContextSource,
    VisualBuilderDraftRequest,
    VisualBuilderService,
)
from foundry_lite.application.services.ontology_yaml import (
    YamlObject,
    action_allowed_roles,
    action_parameter_schema,
    mapping_sequence,
    optional_str,
    required_mapping,
    required_str,
    string_sequence,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.domain.ontology.function_types import (
    CODE_FUNCTION_RUNTIMES,
    FUNCTION_RUNTIME_PYTHON,
    normalized_function_definition,
)

#: Ontology sections whose apiNames share the generated-SDK constant namespace.
_SDK_CONSTANT_SECTIONS = ("objectTypes", "interfaces", "linkTypes", "actionTypes")

#: Placeholder manifest entry for pure-compute functions (no CallFunction
#: blocks, no declared tools). The builder requires at least one governed
#: tool spec; this entry is never added to the runtime tool allowlist, so it
#: can never be executed.
_PURE_COMPUTE_TOOL = ToolSpec(
    tool_id="function.pure-compute",
    version="v1",
    input_schema={"type": "object"},
    output_schema={"type": "object"},
    effect="READ",
    required_permission="object:read",
    confirmation_policy="NONE",
)


def function_definitions_by_api(definition: YamlObject) -> dict[str, FunctionTypeDefinition]:
    """Parse top-level ``functionTypes`` into normalized definitions, rejecting duplicates."""
    functions: dict[str, FunctionTypeDefinition] = {}
    for item in mapping_sequence(definition, "functionTypes"):
        normalized = cast(FunctionTypeDefinition, normalized_function_definition(item))
        api_name = normalized["apiName"]
        if api_name in functions:
            raise ValidationFailed("duplicate function apiName", details={"functionType": api_name})
        functions[api_name] = normalized
    return functions


def validate_ontology_functions(
    definition: YamlObject,
    ctx: RequestContext,
    visual_builder: VisualBuilderService,
) -> dict[str, FunctionTypeDefinition]:
    """Validate every declared function and return the normalized definitions."""
    functions = function_definitions_by_api(definition)
    _require_function_names_disjoint(functions, definition)
    for function in functions.values():
        require_valid_function_logic(ctx, function, visual_builder)
    return functions


def _require_function_names_disjoint(
    functions: Mapping[str, FunctionTypeDefinition],
    definition: YamlObject,
) -> None:
    # Generated SDKs emit one const per apiName, so a function shadowing an
    # object/interface/link/action would collide in every generated module.
    declared = {
        required_str(item, "apiName")
        for section in _SDK_CONSTANT_SECTIONS
        for item in mapping_sequence(definition, section)
    }
    collisions = sorted(set(functions) & declared)
    if collisions:
        raise ValidationFailed(
            "function apiName collides with another ontology apiName",
            details={"apiNames": collisions},
        )


def require_valid_function_logic(
    ctx: RequestContext,
    function: FunctionTypeDefinition,
    visual_builder: VisualBuilderService,
) -> None:
    """Fail closed unless the function's Logic DAG passes builder validation."""
    if function["runtime"] in CODE_FUNCTION_RUNTIMES:
        _require_valid_code_function(function)
        return
    result = visual_builder.validate_draft(ctx, function_draft_request(ctx, function))
    if result.validation_status == "ready":
        return
    raise ValidationFailed(
        "function definition failed logic validation",
        details={
            "functionType": function["apiName"],
            "issues": [issue.to_payload() for issue in result.blocking_issues],
        },
    )


def _require_valid_code_function(function: FunctionTypeDefinition) -> None:
    """Validate code-runtime authoring invariants before a version is activated."""
    if function["runtime"] != FUNCTION_RUNTIME_PYTHON:
        return
    definition = required_mapping(function, "definition")
    source = required_str(definition, "source")
    entrypoint = required_str(definition, "entrypoint")
    try:
        module = ast.parse(source)
    except SyntaxError as exc:
        raise ValidationFailed(
            "Python function source has invalid syntax",
            details={"functionType": function["apiName"], "line": exc.lineno},
        ) from exc
    exported_functions = {node.name for node in module.body if isinstance(node, ast.FunctionDef)}
    if entrypoint not in exported_functions:
        raise ValidationFailed(
            "Python function entrypoint was not found",
            details={"functionType": function["apiName"], "entrypoint": entrypoint},
        )


def function_draft_request(ctx: RequestContext, function: FunctionTypeDefinition) -> VisualBuilderDraftRequest:
    """Adapt one function definition into the visual-builder draft shape."""
    blocks = function_logic_blocks(function)
    tools = function_tool_specs(function)
    if not tools and not any(block.kind == "CallFunction" for block in blocks):
        tools = (_PURE_COMPUTE_TOOL,)
    return VisualBuilderDraftRequest(
        agent_version_id=f"ontology-function:{function['apiName']}",
        release_channel="draft",
        model_alias_version="function-runtime@pinned",
        prompt_version_id=f"ontology-function-prompt:{function['apiName']}",
        context_sources=(_function_context_source(ctx, function),),
        tool_manifest=tools,
        logic_blocks=blocks,
        eval_axes=("security",),
        agent_allowed_actions=(),
    )


def _function_context_source(ctx: RequestContext, function: FunctionTypeDefinition) -> VisualBuilderContextSource:
    return VisualBuilderContextSource(
        source_id=f"ontology-function:{function['apiName']}",
        kind="object",
        security_partition=f"{ctx.tenant_id}:ontology-functions",
    )


def function_logic_blocks(function: FunctionTypeDefinition) -> tuple[LogicBlock, ...]:
    """Rebuild the Logic blocks persisted with a function definition.

    Malformed persisted blocks raise instead of being skipped so import
    validation and execution never silently disagree about the DAG shape.
    """
    logic = required_mapping(function, "definition")
    blocks: list[LogicBlock] = []
    for block in mapping_sequence(logic, "blocks"):
        blocks.append(
            LogicBlock(
                block_id=required_str(block, "blockId"),
                kind=required_str(block, "kind"),
                inputs=dict(required_mapping(block, "inputs")) if block.get("inputs") else {},
                depends_on=string_sequence(block.get("dependsOn", ()), "dependsOn"),
            )
        )
    return tuple(blocks)


def function_tool_specs(function: FunctionTypeDefinition) -> tuple[ToolSpec, ...]:
    """Rebuild the governed tool manifest persisted with a function definition."""
    logic = required_mapping(function, "definition")
    return tuple(_tool_spec(tool) for tool in mapping_sequence(logic, "tools"))


def function_tool_ids(function: FunctionTypeDefinition) -> tuple[str, ...]:
    """Return the tool ids a function may call (its runtime tool allowlist)."""
    return tuple(spec.tool_id for spec in function_tool_specs(function))


def _tool_spec(tool: YamlObject) -> ToolSpec:
    return ToolSpec(
        tool_id=required_str(tool, "toolId"),
        version=required_str(tool, "version"),
        input_schema=dict(required_mapping(tool, "inputSchema")) if tool.get("inputSchema") else {},
        output_schema=dict(required_mapping(tool, "outputSchema")) if tool.get("outputSchema") else {},
        effect=cast(ToolEffect, optional_str(tool, "effect", "READ")),
        required_permission=optional_str(tool, "requiredPermission", "object:read") or "object:read",
        confirmation_policy=cast(ConfirmationPolicy, optional_str(tool, "confirmationPolicy", "NONE")),
        object_type_allowlist=string_sequence(tool.get("objectTypeAllowlist", ()), "objectTypeAllowlist"),
        property_allowlist=string_sequence(tool.get("propertyAllowlist", ()), "propertyAllowlist"),
        timeout_seconds=_int_field(tool, "timeoutSeconds", 30),
        max_result_items=_int_field(tool, "maxResultItems", 50),
        result_classification=optional_str(tool, "resultClassification", "public") or "public",
        status=optional_str(tool, "status", "published") or "published",
    )


def _int_field(tool: YamlObject, key: str, default: int) -> int:
    value = tool.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationFailed(f"{key} must be an integer")
    return value


def function_allowed_roles(definition: OntologyJsonObject) -> tuple[str, ...] | None:
    """Return ``permissions.allowedRoles`` from a persisted function definition.

    Unlike actions, ``None`` never falls back to a static permission map:
    execution stays admin-only fail-closed when no roles are declared.
    """
    return action_allowed_roles(definition)


def validate_function_inputs(function: FunctionTypeDefinition, inputs: Mapping[str, object]) -> None:
    """Validate call inputs against the declared input schema (fail closed).

    Reuses the action parameter validation semantics: missing required inputs,
    undeclared inputs, and type mismatches all raise ``ValidationFailed``.
    """
    schema = action_parameter_schema(mapping_sequence(function, "inputs"))
    error = validate_action_request({"parameter_schema": schema, "definition": {}}, {"properties": {}}, inputs)
    if error is not None:
        details = dict(getattr(error, "details", None) or {})
        details["functionType"] = function["apiName"]
        details["reason"] = str(error)
        raise ValidationFailed("function inputs do not match the declared schema", details=details)


def import_function_types(
    repository: OntologyRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    ontology_version_id: str,
    functions: Mapping[str, FunctionTypeDefinition],
) -> None:
    """Persist the normalized function definitions for a draft ontology version."""
    for api_name, function in sorted(functions.items()):
        repository.insert_function_type(
            transaction=conn,
            record=FunctionTypeRecord(
                function_type_id=_new_id("ftype"),
                tenant_id=ctx.tenant_id,
                ontology_version_id=ontology_version_id,
                api_name=api_name,
                display_name=function["displayName"],
                definition=function,
            ),
        )
