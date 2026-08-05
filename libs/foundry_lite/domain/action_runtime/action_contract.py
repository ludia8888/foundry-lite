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
    referenced_condition_value_kinds,
    validate_action_condition,
)
from foundry_lite.domain.action_runtime.action_effects import (
    ActionEffectV3,
    action_effect_payload,
    compile_action_effects,
)
from foundry_lite.domain.action_runtime.action_permissions import compile_action_permissions
from foundry_lite.domain.action_runtime.action_presentation import (
    ActionFormLayoutV3,
    action_form_layout_payload,
    action_inline_eligibility,
    compile_action_form_layout,
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
ACTION_FUNCTION_EXECUTION_MODES = frozenset({"per_request", "batched"})
ACTION_FUNCTION_PER_REQUEST_BATCH_LIMIT = 20
ACTION_FUNCTION_BATCH_LIMIT = 10_000
ACTION_MEDIA_PARAMETER_TYPES = frozenset({"media", "attachment"})
ACTION_ATTACHMENT_MAX_BYTES = 200 * 1024 * 1024


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
    execution_mode: str
    batch_input_name: str | None
    max_batch_size: int


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
    effects: tuple[ActionEffectV3, ...]
    risk_level: str
    agent_execution_policy: str
    agent_tool_description: str | None
    log_policy: Mapping[str, object]
    revert_policy: Mapping[str, object]
    branch_policy: Mapping[str, object]
    form_layout: ActionFormLayoutV3
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
        _validate_submission_criteria_references(criteria, parameters)
    risk_level = _enum(definition.get("riskLevel"), ACTION_RISK_LEVELS, "high", "riskLevel")
    agent_policy = _enum(
        definition.get("agentExecutionPolicy"), AGENT_EXECUTION_POLICIES, "approval_required", "agentExecutionPolicy"
    )
    effects = compile_action_effects(definition)
    form_layout = compile_action_form_layout(
        definition.get("formLayout"), tuple(parameter.api_name for parameter in parameters)
    )
    source_version = _source_version(definition)
    _validate_effect_contract(effects, rules, source_version)
    return ActionDefinitionV3(
        api_name=api_name,
        display_name=_optional_text(definition.get("displayName")) or api_name,
        description=_optional_text(definition.get("description")),
        target=target,
        parameters=parameters,
        rules=rules,
        function=function,
        submission_criteria=criteria,
        permissions=compile_action_permissions(definition.get("permissions")),
        effects=effects,
        risk_level=risk_level,
        agent_execution_policy=agent_policy,
        agent_tool_description=_optional_text(definition.get("agentToolDescription")),
        log_policy=_mapping_or_empty(definition.get("actionLog")),
        revert_policy=_mapping_or_empty(definition.get("revert")),
        branch_policy=_mapping_or_empty(definition.get("branchPolicy")),
        form_layout=form_layout,
        source_version=source_version,
    )


def compile_action_contract_snapshot(snapshot: Mapping[str, object]) -> ActionDefinitionV3:
    """Restore an immutable canonical payload without weakening public v3 validation."""
    if snapshot.get("contractVersion") != 3:
        raise ValidationFailed("stored action contract must use canonical contractVersion 3")
    source_version = snapshot.get("sourceVersion")
    if not isinstance(source_version, int) or isinstance(source_version, bool) or source_version not in {1, 2, 3}:
        raise ValidationFailed(
            "stored action contract has an invalid sourceVersion",
            details={"sourceVersion": source_version},
        )
    restored = dict(snapshot)
    restored["contractVersion"] = source_version
    return compile_action_contract(restored)


def _validate_effect_contract(
    effects: tuple[ActionEffectV3, ...],
    rules: tuple[Mapping[str, object], ...],
    source_version: int,
) -> None:
    if source_version < 3:
        return
    before = next((effect for effect in effects if effect.phase == "before_commit"), None)
    fields = _webhook_response_fields(rules)
    if fields and before is None:
        raise ValidationFailed(
            "webhook response rule values require a before-commit Action effect",
            details={"fields": sorted(fields)},
        )
    if before is not None and (unknown := fields - set(before.response_fields)):
        raise ValidationFailed(
            "webhook response rule value is not declared by the before-commit effect",
            details={"fields": sorted(unknown), "effectId": before.effect_id},
        )


def action_contract_uses_webhook_response(contract: ActionDefinitionV3) -> bool:
    """Return whether rule assignments depend on a typed gating-webhook response."""
    return bool(_webhook_response_fields(contract.rules))


def _webhook_response_fields(rules: tuple[Mapping[str, object], ...]) -> set[str]:
    all_fields = _response_fields_in(rules)
    assignment_fields: set[str] = set()
    for rule in rules:
        for assignment in _sequence(rule.get("assignments"), "assignments"):
            payload = _mapping(assignment, "assignment")
            assignment_fields.update(_response_fields_in(payload.get("value")))
    if all_fields != assignment_fields:
        raise ValidationFailed("webhook response values may be used only in Action property assignments")
    return all_fields


def _response_fields_in(value: object) -> set[str]:
    if isinstance(value, Mapping):
        payload = cast(Mapping[object, object], value)
        field = payload.get("field")
        found_fields: set[str] = set()
        if payload.get("kind") == "webhookResponse" and isinstance(field, str):
            found_fields.add(field)
        for child in payload.values():
            found_fields.update(_response_fields_in(child))
        return {found_field for found_field in found_fields if found_field}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        sequence_fields: set[str] = set()
        for child in cast(Sequence[object], value):
            sequence_fields.update(_response_fields_in(child))
        return sequence_fields
    return set()


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
        "effects": [action_effect_payload(effect) for effect in contract.effects],
        "riskLevel": contract.risk_level,
        "agentExecutionPolicy": contract.agent_execution_policy,
        "agentToolDescription": contract.agent_tool_description,
        "actionLog": dict(contract.log_policy),
        "revert": dict(contract.revert_policy),
        "branchPolicy": dict(contract.branch_policy),
        "formLayout": action_form_layout_payload(contract.form_layout),
        "inlineEligibility": _inline_eligibility(contract),
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
        "x-foundry-form-layout": action_form_layout_payload(contract.form_layout),
        "x-foundry-inline-eligibility": _inline_eligibility(contract),
    }


def _inline_eligibility(contract: ActionDefinitionV3) -> dict[str, object]:
    return action_inline_eligibility(
        target_kind=contract.target.kind,
        target_api_name=contract.target.api_name,
        parameter_types={parameter.api_name: parameter.data_type for parameter in contract.parameters},
        rules=contract.rules,
        has_function=contract.function is not None,
        effect_count=len(contract.effects),
    )


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
    parameter = ActionParameterV3(
        api_name=_required_text(raw, "apiName"),
        data_type=data_type,
        required=_optional_bool(raw.get("required"), False, "required"),
        description=_optional_text(raw.get("description")),
        default=_optional_mapping(raw.get("default")),
        constraints=_mapping_or_empty(raw.get("constraints")),
        metadata={key: value for key, value in raw.items() if key not in known},
        overrides=_overrides(raw.get("overrides", ())),
    )
    _validate_parameter_shape(parameter)
    return parameter


def _validate_parameter_shape(parameter: ActionParameterV3) -> None:
    if parameter.data_type in {"array", "objectSet"}:
        _array_item_schema(parameter)
    if parameter.data_type == "struct":
        _struct_fields(parameter)
    if _media_parameter_kind(parameter) is not None:
        _validate_media_parameter_shape(parameter)


def _media_parameter_kind(parameter: ActionParameterV3) -> str | None:
    if parameter.data_type in ACTION_MEDIA_PARAMETER_TYPES:
        return parameter.data_type
    item_type = parameter.metadata.get("itemType")
    if parameter.data_type in {"array", "objectSet"} and item_type in ACTION_MEDIA_PARAMETER_TYPES:
        return str(item_type)
    return None


def _validate_media_parameter_shape(parameter: ActionParameterV3) -> None:
    _validate_media_set_reference(parameter)
    _validate_allowed_mime_types(parameter)
    _validate_media_max_bytes(parameter)
    _validate_media_render_mode(parameter)


def _validate_media_set_reference(parameter: ActionParameterV3) -> None:
    media_set = parameter.metadata.get("mediaSet")
    if not isinstance(media_set, str) or len(media_set.split(".")) != 2 or not all(media_set.split(".")):
        raise ValidationFailed(
            "media and attachment parameters require a namespace.name mediaSet",
            details={"parameter": parameter.api_name, "mediaSet": media_set},
        )


def _validate_allowed_mime_types(parameter: ActionParameterV3) -> None:
    allowed_mime_types = parameter.metadata.get("allowedMimeTypes")
    if allowed_mime_types is not None and not _is_non_empty_text_sequence(allowed_mime_types):
        raise ValidationFailed(
            "allowedMimeTypes must be a non-empty string list",
            details={"parameter": parameter.api_name},
        )


def _validate_media_max_bytes(parameter: ActionParameterV3) -> None:
    maximum = parameter.metadata.get("maxBytes")
    if maximum is not None and not _is_valid_media_max_bytes(parameter, maximum):
        raise ValidationFailed(
            "media parameter maxBytes is invalid",
            details={"parameter": parameter.api_name, "maxBytes": maximum},
        )


def _is_valid_media_max_bytes(parameter: ActionParameterV3, maximum: object) -> bool:
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum <= 0:
        return False
    return _media_parameter_kind(parameter) != "attachment" or maximum <= ACTION_ATTACHMENT_MAX_BYTES


def _validate_media_render_mode(parameter: ActionParameterV3) -> None:
    render = parameter.metadata.get("render", "filePicker")
    if render not in {"filePicker", "textInput"}:
        raise ValidationFailed(
            "media parameter render must be filePicker or textInput",
            details={"parameter": parameter.api_name, "render": render},
        )


def _is_non_empty_text_sequence(value: object) -> bool:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes) or not value:
        return False
    return all(isinstance(item, str) and bool(item) for item in cast(Sequence[object], value))


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
            if "linkedObjectProperty" in referenced_condition_value_kinds(override.when):
                raise ValidationFailed(
                    "parameter overrides cannot read linked objects",
                    details={"parameter": parameter.api_name},
                )
            invalid = referenced_condition_parameters(override.when) - available
            if invalid:
                raise ValidationFailed(
                    "parameter override may reference earlier parameters only",
                    details={"parameter": parameter.api_name, "invalidReferences": sorted(invalid)},
                )
        _validate_default_order(parameter, available)
        available.add(parameter.api_name)


def _validate_submission_criteria_references(
    criteria: Mapping[str, object], parameters: tuple[ActionParameterV3, ...]
) -> None:
    declared = {parameter.api_name for parameter in parameters}
    invalid = referenced_condition_parameters(criteria) - declared
    if invalid:
        raise ValidationFailed(
            "submission criteria references unknown parameters",
            details={"invalidReferences": sorted(invalid)},
        )


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
    execution_mode = _enum(
        payload.get("executionMode"),
        ACTION_FUNCTION_EXECUTION_MODES,
        "per_request",
        "function.executionMode",
    )
    batch_input_name = _optional_text(payload.get("batchInputName"))
    if execution_mode == "batched" and batch_input_name is None:
        raise ValidationFailed("batched Action function requires batchInputName")
    if execution_mode == "per_request" and batch_input_name is not None:
        raise ValidationFailed("per-request Action function cannot declare batchInputName")
    limit_ceiling = (
        ACTION_FUNCTION_BATCH_LIMIT if execution_mode == "batched" else ACTION_FUNCTION_PER_REQUEST_BATCH_LIMIT
    )
    max_batch_size = _bounded_positive_int(payload.get("maxBatchSize"), limit_ceiling, limit_ceiling)
    return ActionFunctionRefV3(
        api_name=_required_text(payload, "apiName"),
        version=_required_text(payload, "version"),
        execution_mode=execution_mode,
        batch_input_name=batch_input_name,
        max_batch_size=max_batch_size,
    )


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


def _function_payload(function: ActionFunctionRefV3 | None) -> dict[str, object] | None:
    if function is None:
        return None
    return {
        "apiName": function.api_name,
        "version": function.version,
        "executionMode": function.execution_mode,
        "batchInputName": function.batch_input_name,
        "maxBatchSize": function.max_batch_size,
    }


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
    if data_type in {"object", "interface"}:
        return {
            "oneOf": [
                {"type": "string", "format": f"foundry-{data_type}"},
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"objectType": {"type": "string"}, "objectId": {"type": "string"}},
                    "required": ["objectType", "objectId"],
                },
            ]
        }
    if data_type in ACTION_MEDIA_PARAMETER_TYPES:
        return {
            "oneOf": [
                {"type": "string", "format": f"foundry-{data_type}-version-id"},
                _immutable_media_reference_schema(data_type),
            ]
        }
    if data_type in {"objectSet", "array"}:
        return {"type": "array", "items": _array_item_schema(parameter), "uniqueItems": data_type == "objectSet"}
    return _struct_schema(parameter)


def _array_item_schema(parameter: ActionParameterV3) -> dict[str, object]:
    item_type = parameter.metadata.get("itemType")
    normalized = str(item_type or "string")
    if normalized not in ACTION_PARAMETER_TYPES or normalized in {"array", "objectSet"}:
        raise ValidationFailed("unsupported array item type", details={"itemType": normalized})
    metadata = {key: value for key, value in parameter.metadata.items() if key != "itemType"}
    synthetic = ActionParameterV3("item", normalized, False, None, None, {}, metadata, ())
    return _base_parameter_schema(synthetic)


def _immutable_media_reference_schema(reference_kind: str) -> dict[str, object]:
    properties: dict[str, object] = {
        "referenceKind": {"const": reference_kind},
        "mediaSetId": {"type": "string"},
        "mediaItemId": {"type": "string"},
        "mediaItemVersionId": {"type": "string"},
        "logicalPath": {"type": "string"},
        "contentHash": {"type": "string"},
        "mimeType": {"type": "string"},
        "byteSize": {"type": "integer", "minimum": 0},
        "classification": {"type": "string"},
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
    }


def _struct_schema(parameter: ActionParameterV3) -> dict[str, object]:
    fields = _struct_fields(parameter)
    properties: dict[str, object] = {}
    required: list[str] = []
    for field in fields:
        properties[field.api_name] = _parameter_schema(field)
        if field.required:
            required.append(field.api_name)
    return {"type": "object", "additionalProperties": False, "properties": properties, "required": required}


def _struct_fields(parameter: ActionParameterV3) -> tuple[ActionParameterV3, ...]:
    fields = parameter.metadata.get("fields")
    if not isinstance(fields, Sequence) or isinstance(fields, str | bytes) or not fields:
        raise ValidationFailed("struct action parameter requires at least one typed field")
    result = tuple(_parameter(_mapping(raw, "struct field")) for raw in cast(Sequence[object], fields))
    names = [field.api_name for field in result]
    if len(names) != len(set(names)):
        raise ValidationFailed("duplicate struct field", details={"fields": names})
    return result


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


def _bounded_positive_int(raw: object, default: int, maximum: int) -> int:
    value = default if raw is None else raw
    if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > maximum:
        raise ValidationFailed(
            "Action function maxBatchSize is invalid",
            details={"value": value, "maximum": maximum},
        )
    return value


def _enum(raw: object, allowed: frozenset[str], default: str, field: str) -> str:
    value = raw if isinstance(raw, str) and raw else default
    if value not in allowed:
        raise ValidationFailed("unsupported action field value", details={"field": field, "value": value})
    return value
