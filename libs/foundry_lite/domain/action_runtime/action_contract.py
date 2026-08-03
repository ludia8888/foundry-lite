"""Canonical Action Contract v3.

The contract normalizes legacy action definitions and new v3 declarations into
one immutable representation.  API schemas, dynamic forms, MCP tools, and the
runtime all consume this model instead of interpreting YAML independently.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from foundry_lite.domain.action_runtime.action_conditions import (
    referenced_condition_parameters,
    validate_action_condition,
)
from foundry_lite.domain.errors import ValidationFailed

ACTION_PARAMETER_TYPES = frozenset(
    {
        "string",
        "boolean",
        "integer",
        "long",
        "float",
        "decimal",
        "date",
        "timestamp",
        "object",
        "interface",
        "objectSet",
        "array",
        "struct",
        "media",
        "attachment",
    }
)
ACTION_RISK_LEVELS = frozenset({"low", "medium", "high"})
AGENT_EXECUTION_POLICIES = frozenset({"plan_only", "approval_required", "autonomous"})


@dataclass(frozen=True, slots=True)
class ActionTargetV3:
    kind: str
    api_name: str


@dataclass(frozen=True, slots=True)
class ActionParameterOverrideV3:
    when: Mapping[str, object]
    config: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ActionParameterV3:
    api_name: str
    data_type: str
    required: bool
    description: str | None
    default: Mapping[str, object] | None
    constraints: Mapping[str, object]
    metadata: Mapping[str, object]
    overrides: tuple[ActionParameterOverrideV3, ...]


@dataclass(frozen=True, slots=True)
class ActionFunctionRefV3:
    api_name: str
    version: str


@dataclass(frozen=True, slots=True)
class ActionDefinitionV3:
    api_name: str
    display_name: str
    description: str | None
    target: ActionTargetV3
    parameters: tuple[ActionParameterV3, ...]
    rules: tuple[Mapping[str, object], ...]
    function: ActionFunctionRefV3 | None
    submission_criteria: Mapping[str, object] | None
    permissions: Mapping[str, object]
    effects: tuple[Mapping[str, object], ...]
    risk_level: str
    agent_execution_policy: str
    agent_tool_description: str | None
    log_policy: Mapping[str, object]
    revert_policy: Mapping[str, object]
    branch_policy: Mapping[str, object]
    source_version: int


def compile_action_contract(definition: Mapping[str, object]) -> ActionDefinitionV3:
    """Normalize a persisted v1/v2/v3 action definition."""
    api_name = _required_text(definition, "apiName")
    target = _target(definition)
    parameters = _parameters(definition.get("parameters", ()))
    _validate_parameter_order(parameters)
    rules = _rules(definition)
    function = _function_ref(definition.get("function"))
    if function is not None and rules:
        raise ValidationFailed("function-backed action cannot also declare rules", details={"apiName": api_name})
    criteria = _optional_mapping(definition.get("submissionCriteria"))
    if criteria is not None:
        validate_action_condition(criteria)
    risk_level = _enum(definition.get("riskLevel"), ACTION_RISK_LEVELS, "high", "riskLevel")
    agent_policy = _enum(
        definition.get("agentExecutionPolicy"), AGENT_EXECUTION_POLICIES, "approval_required", "agentExecutionPolicy"
    )
    return ActionDefinitionV3(
        api_name=api_name,
        display_name=_optional_text(definition.get("displayName")) or api_name,
        description=_optional_text(definition.get("description")),
        target=target,
        parameters=parameters,
        rules=rules,
        function=function,
        submission_criteria=criteria,
        permissions=_mapping_or_empty(definition.get("permissions")),
        effects=_effects(definition),
        risk_level=risk_level,
        agent_execution_policy=agent_policy,
        agent_tool_description=_optional_text(definition.get("agentToolDescription")),
        log_policy=_mapping_or_empty(definition.get("actionLog")),
        revert_policy=_mapping_or_empty(definition.get("revert")),
        branch_policy=_mapping_or_empty(definition.get("branchPolicy")),
        source_version=_source_version(definition),
    )


def action_contract_payload(contract: ActionDefinitionV3) -> dict[str, object]:
    """Return the stable public representation used for fingerprints and APIs."""
    return {
        "contractVersion": 3,
        "sourceVersion": contract.source_version,
        "apiName": contract.api_name,
        "displayName": contract.display_name,
        "description": contract.description,
        "target": {"kind": contract.target.kind, "apiName": contract.target.api_name},
        "parameters": [_parameter_payload(parameter) for parameter in contract.parameters],
        "rules": [dict(rule) for rule in contract.rules],
        "function": _function_payload(contract.function),
        "submissionCriteria": dict(contract.submission_criteria) if contract.submission_criteria else None,
        "permissions": dict(contract.permissions),
        "effects": [dict(effect) for effect in contract.effects],
        "riskLevel": contract.risk_level,
        "agentExecutionPolicy": contract.agent_execution_policy,
        "agentToolDescription": contract.agent_tool_description,
        "actionLog": dict(contract.log_policy),
        "revert": dict(contract.revert_policy),
        "branchPolicy": dict(contract.branch_policy),
    }


def action_contract_fingerprint(contract: ActionDefinitionV3) -> str:
    canonical = json.dumps(action_contract_payload(contract), sort_keys=True, separators=(",", ":"), default=str)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def action_parameter_json_schema(contract: ActionDefinitionV3) -> dict[str, object]:
    """Generate deterministic JSON Schema for UI, SDK, and MCP consumers."""
    properties = {parameter.api_name: _parameter_schema(parameter) for parameter in contract.parameters}
    required = [parameter.api_name for parameter in contract.parameters if parameter.required]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
        "x-foundry-action": contract.api_name,
        "x-foundry-contract-fingerprint": action_contract_fingerprint(contract),
    }


def _target(definition: Mapping[str, object]) -> ActionTargetV3:
    raw = definition.get("target")
    if isinstance(raw, Mapping):
        payload = cast(Mapping[str, object], raw)
        return ActionTargetV3(kind=_target_kind(payload.get("kind")), api_name=_required_text(payload, "apiName"))
    if not isinstance(raw, str) or not raw:
        return _legacy_target(definition)
    return ActionTargetV3(kind=_target_kind(definition.get("targetKind")), api_name=raw)


def _legacy_target(definition: Mapping[str, object]) -> ActionTargetV3:
    if definition.get("contractVersion", 1) == 3:
        raise ValidationFailed("action target is required")
    for raw_rule in _sequence(definition.get("rulesV2", ()), "rulesV2"):
        rule = _mapping(raw_rule, "action rule")
        object_type = rule.get("objectType")
        if isinstance(object_type, str) and object_type:
            return ActionTargetV3(kind="object", api_name=object_type)
    return ActionTargetV3(kind="object", api_name="__multi__")


def _target_kind(raw: object) -> str:
    kind = raw if isinstance(raw, str) and raw else "object"
    if kind not in {"object", "interface"}:
        raise ValidationFailed("unsupported action target kind", details={"kind": kind})
    return kind


def _parameters(raw: object) -> tuple[ActionParameterV3, ...]:
    values = _sequence(raw, "parameters")
    result = tuple(_parameter(_mapping(item, "parameter")) for item in values)
    names = [parameter.api_name for parameter in result]
    if len(names) != len(set(names)):
        raise ValidationFailed("duplicate action parameter", details={"parameters": names})
    return result


def _parameter(raw: Mapping[str, object]) -> ActionParameterV3:
    data_type = _required_text(raw, "type")
    if data_type not in ACTION_PARAMETER_TYPES:
        raise ValidationFailed("unsupported action parameter type", details={"type": data_type})
    known = {"apiName", "type", "required", "description", "default", "constraints", "overrides"}
    return ActionParameterV3(
        api_name=_required_text(raw, "apiName"),
        data_type=data_type,
        required=_optional_bool(raw.get("required"), False, "required"),
        description=_optional_text(raw.get("description")),
        default=_optional_mapping(raw.get("default")),
        constraints=_mapping_or_empty(raw.get("constraints")),
        metadata={key: value for key, value in raw.items() if key not in known},
        overrides=_overrides(raw.get("overrides", ())),
    )


def _overrides(raw: object) -> tuple[ActionParameterOverrideV3, ...]:
    result: list[ActionParameterOverrideV3] = []
    for item in _sequence(raw, "overrides"):
        payload = _mapping(item, "parameter override")
        when = _mapping(payload.get("when"), "parameter override condition")
        validate_action_condition(when)
        result.append(ActionParameterOverrideV3(when=when, config=_mapping(payload.get("config"), "override config")))
    return tuple(result)


def _validate_parameter_order(parameters: tuple[ActionParameterV3, ...]) -> None:
    available: set[str] = set()
    for parameter in parameters:
        for override in parameter.overrides:
            invalid = referenced_condition_parameters(override.when) - available
            if invalid:
                raise ValidationFailed(
                    "parameter override may reference earlier parameters only",
                    details={"parameter": parameter.api_name, "invalidReferences": sorted(invalid)},
                )
        _validate_default_order(parameter, available)
        available.add(parameter.api_name)


def _validate_default_order(parameter: ActionParameterV3, available: set[str]) -> None:
    default = parameter.default
    if default is None:
        return
    kind = default.get("kind")
    if kind not in {"literal", "parameter", "objectProperty", "currentUser", "currentTime", "generatedId"}:
        raise ValidationFailed("unsupported action parameter default", details={"kind": kind})
    if kind == "objectProperty":
        _required_text(default, "property")
        return
    if kind in {"currentUser", "currentTime"}:
        return
    if kind == "generatedId":
        _required_text(default, "strategy")
        return
    if kind != "parameter":
        return
    referenced = default.get("parameter")
    if not isinstance(referenced, str) or referenced not in available:
        raise ValidationFailed(
            "parameter default may reference earlier parameters only",
            details={"parameter": parameter.api_name, "reference": referenced},
        )


def _rules(definition: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    raw = definition.get("rules")
    if raw is None:
        raw = definition.get("rulesV2", ())
    return tuple(_mapping(item, "action rule") for item in _sequence(raw, "rules"))


def _function_ref(raw: object) -> ActionFunctionRefV3 | None:
    if raw is None:
        return None
    payload = _mapping(raw, "function")
    return ActionFunctionRefV3(api_name=_required_text(payload, "apiName"), version=_required_text(payload, "version"))


def _effects(definition: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    if "effects" in definition:
        return tuple(_mapping(item, "effect") for item in _sequence(definition.get("effects"), "effects"))
    combined = list(_sequence(definition.get("writebacks", ()), "writebacks"))
    combined.extend(_sequence(definition.get("sideEffects", ()), "sideEffects"))
    return tuple(_mapping(item, "effect") for item in combined)


def _parameter_payload(parameter: ActionParameterV3) -> dict[str, object]:
    payload: dict[str, object] = {
        "apiName": parameter.api_name,
        "type": parameter.data_type,
        "required": parameter.required,
        "description": parameter.description,
        "default": dict(parameter.default) if parameter.default else None,
        "constraints": dict(parameter.constraints),
        "overrides": [{"when": dict(item.when), "config": dict(item.config)} for item in parameter.overrides],
    }
    payload.update(parameter.metadata)
    return payload


def _function_payload(function: ActionFunctionRefV3 | None) -> dict[str, str] | None:
    return None if function is None else {"apiName": function.api_name, "version": function.version}


def _parameter_schema(parameter: ActionParameterV3) -> dict[str, object]:
    schema = _base_parameter_schema(parameter)
    if parameter.description:
        schema["description"] = parameter.description
    schema.update(parameter.constraints)
    schema["x-foundry-parameter-config"] = _parameter_payload(parameter)
    return schema


def _base_parameter_schema(parameter: ActionParameterV3) -> dict[str, object]:
    data_type = parameter.data_type
    if data_type in {"string"}:
        return {"type": "string"}
    if data_type in {"integer", "long"}:
        return {"type": "integer"}
    if data_type in {"float", "decimal"}:
        return {"type": "number"}
    if data_type == "boolean":
        return {"type": "boolean"}
    if data_type in {"date", "timestamp"}:
        return {"type": "string", "format": "date" if data_type == "date" else "date-time"}
    if data_type in {"object", "interface", "media", "attachment"}:
        return {"type": "string", "format": f"foundry-{data_type}"}
    if data_type in {"objectSet", "array"}:
        return {"type": "array", "items": _array_item_schema(parameter), "uniqueItems": data_type == "objectSet"}
    return _struct_schema(parameter)


def _array_item_schema(parameter: ActionParameterV3) -> dict[str, object]:
    item_type = parameter.metadata.get("itemType")
    normalized = str(item_type or "string")
    if normalized not in ACTION_PARAMETER_TYPES or normalized in {"array", "objectSet"}:
        raise ValidationFailed("unsupported array item type", details={"itemType": normalized})
    synthetic = ActionParameterV3("item", normalized, False, None, None, {}, {}, ())
    return _base_parameter_schema(synthetic)


def _struct_schema(parameter: ActionParameterV3) -> dict[str, object]:
    fields = parameter.metadata.get("fields")
    if not isinstance(fields, Sequence) or isinstance(fields, str | bytes):
        return {"type": "object", "additionalProperties": True}
    properties: dict[str, object] = {}
    required: list[str] = []
    for raw in cast(Sequence[object], fields):
        field = _parameter(_mapping(raw, "struct field"))
        properties[field.api_name] = _parameter_schema(field)
        if field.required:
            required.append(field.api_name)
    return {"type": "object", "additionalProperties": False, "properties": properties, "required": required}


def _source_version(definition: Mapping[str, object]) -> int:
    raw = definition.get("contractVersion", 1)
    if not isinstance(raw, int) or raw not in {1, 2, 3}:
        raise ValidationFailed("unsupported action contract version", details={"contractVersion": raw})
    return raw


def _sequence(raw: object, field: str) -> tuple[object, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        raise ValidationFailed(f"{field} must be a list")
    return tuple(cast(Sequence[object], raw))


def _mapping(raw: object, field: str) -> Mapping[str, object]:
    if not isinstance(raw, Mapping):
        raise ValidationFailed(f"{field} must be an object")
    return cast(Mapping[str, object], raw)


def _mapping_or_empty(raw: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], raw) if isinstance(raw, Mapping) else {}


def _optional_mapping(raw: object) -> Mapping[str, object] | None:
    if raw is None:
        return None
    return _mapping(raw, "action field")


def _required_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValidationFailed("action field is required", details={"field": key})
    return value


def _optional_text(raw: object) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValidationFailed("action field must be text")
    return raw or None


def _optional_bool(raw: object, is_default: bool, field: str) -> bool:
    if raw is None:
        return is_default
    if not isinstance(raw, bool):
        raise ValidationFailed("action field must be boolean", details={"field": field})
    return raw


def _enum(raw: object, allowed: frozenset[str], default: str, field: str) -> str:
    value = raw if isinstance(raw, str) and raw else default
    if value not in allowed:
        raise ValidationFailed("unsupported action field value", details={"field": field, "value": value})
    return value
