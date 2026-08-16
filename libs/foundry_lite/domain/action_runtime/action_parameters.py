"""Deterministic parameter resolution for Action Contract v3."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast

from foundry_lite.domain.action_runtime.action_conditions import (
    StaticActionConditionContext,
    evaluate_action_condition,
)
from foundry_lite.domain.action_runtime.action_contract import ActionDefinitionV3, ActionParameterV3
from foundry_lite.domain.action_runtime.action_parameter_constraints import (
    validate_action_parameter_constraints_for_value,
)
from foundry_lite.domain.action_runtime.action_parameter_types import matches_action_parameter_type
from foundry_lite.domain.errors import ValidationFailed


@dataclass(frozen=True, slots=True)
class ResolvedActionParameterConfig:
    required: bool
    is_visible: bool
    is_editable: bool
    constraints: Mapping[str, object]
    default: Mapping[str, object] | None
    matched_override: int | None


@dataclass(frozen=True, slots=True)
class ActionParameterResolution:
    values: Mapping[str, object]
    configs: Mapping[str, ResolvedActionParameterConfig]


@dataclass(frozen=True, slots=True)
class ActionParameterContext:
    submitted: Mapping[str, object]
    object_properties: Mapping[str, object]
    actor_user_id: str
    actor_groups: tuple[str, ...]
    now: datetime
    generate_id: Callable[[str], str]
    actor_attributes: Mapping[str, object] = field(default_factory=dict[str, object])


def resolve_action_parameters(
    contract: ActionDefinitionV3,
    context: ActionParameterContext,
) -> ActionParameterResolution:
    """Resolve overrides/defaults in declaration order and validate values."""
    declared = {parameter.api_name for parameter in contract.parameters}
    unexpected = sorted(set(context.submitted) - declared)
    if unexpected:
        raise ValidationFailed("unexpected action parameters", details={"unexpected": unexpected})
    values: dict[str, object] = {}
    configs: dict[str, ResolvedActionParameterConfig] = {}
    parameter_types = {parameter.api_name: parameter.data_type for parameter in contract.parameters}
    for parameter in contract.parameters:
        config = _effective_config(parameter, values, context, parameter_types)
        value, is_present = _parameter_value(parameter, config, values, context)
        _validate_parameter_presence(parameter, config, is_present)
        if is_present:
            _validate_parameter_value(parameter, value, config.constraints)
            values[parameter.api_name] = value
        configs[parameter.api_name] = config
    return ActionParameterResolution(values=values, configs=configs)


def default_action_parameter_context(
    submitted: Mapping[str, object],
    object_properties: Mapping[str, object],
    actor_user_id: str,
    actor_groups: tuple[str, ...],
    actor_attributes: Mapping[str, object],
    generate_id: Callable[[str], str],
) -> ActionParameterContext:
    return ActionParameterContext(
        submitted=submitted,
        object_properties=object_properties,
        actor_user_id=actor_user_id,
        actor_groups=actor_groups,
        actor_attributes=actor_attributes,
        now=datetime.now(UTC),
        generate_id=generate_id,
    )


def parameter_config_payload(config: ResolvedActionParameterConfig) -> dict[str, object]:
    return {
        "required": config.required,
        "visible": config.is_visible,
        "editable": config.is_editable,
        "constraints": dict(config.constraints),
        "default": dict(config.default) if config.default else None,
        "matchedOverride": config.matched_override,
    }


def _effective_config(
    parameter: ActionParameterV3,
    values: Mapping[str, object],
    context: ActionParameterContext,
    parameter_types: Mapping[str, str],
) -> ResolvedActionParameterConfig:
    base: dict[str, object] = {
        "required": parameter.required,
        "visible": True,
        "editable": True,
        "constraints": dict(parameter.constraints),
        "default": parameter.default,
    }
    matched: int | None = None
    condition_context = _condition_context(values, context, parameter_types)
    for index, override in enumerate(parameter.overrides):
        if evaluate_action_condition(override.when, condition_context):
            base = _merge_config(base, override.config)
            matched = index
            break
    return _resolved_config(base, matched)


def _condition_context(
    values: Mapping[str, object],
    context: ActionParameterContext,
    parameter_types: Mapping[str, str],
) -> StaticActionConditionContext:
    return StaticActionConditionContext(
        parameters=values,
        object_properties=context.object_properties,
        actor_user_id=context.actor_user_id,
        actor_groups=context.actor_groups,
        actor_attributes=context.actor_attributes,
        parameter_types=parameter_types,
    )


def _merge_config(base: Mapping[str, object], override: Mapping[str, object]) -> dict[str, object]:
    allowed = {"required", "visible", "editable", "constraints", "default"}
    unexpected = sorted(set(override) - allowed)
    if unexpected:
        raise ValidationFailed("unsupported parameter override config", details={"fields": unexpected})
    result = dict(base)
    result.update(override)
    return result


def _resolved_config(raw: Mapping[str, object], matched: int | None) -> ResolvedActionParameterConfig:
    return ResolvedActionParameterConfig(
        required=_bool_config(raw, "required"),
        is_visible=_bool_config(raw, "visible"),
        is_editable=_bool_config(raw, "editable"),
        constraints=_mapping_config(raw.get("constraints"), "constraints"),
        default=_optional_mapping_config(raw.get("default"), "default"),
        matched_override=matched,
    )


def _parameter_value(
    parameter: ActionParameterV3,
    config: ResolvedActionParameterConfig,
    values: Mapping[str, object],
    context: ActionParameterContext,
) -> tuple[object, bool]:
    if parameter.api_name in context.submitted:
        if not config.is_editable:
            raise ValidationFailed(
                "action parameter is not editable",
                details={"parameter": parameter.api_name, "invalid": [parameter.api_name]},
            )
        return context.submitted[parameter.api_name], True
    if config.default is None:
        return None, False
    return _resolve_default(config.default, values, context), True


def _resolve_default(
    default: Mapping[str, object], values: Mapping[str, object], context: ActionParameterContext
) -> object:
    kind = default.get("kind")
    if kind == "literal":
        return default.get("value")
    if kind == "parameter":
        return values.get(_required_text(default, "parameter"))
    if kind == "objectProperty":
        return context.object_properties.get(_required_text(default, "property"))
    if kind == "currentUser":
        return _current_user_default(default, context)
    if kind == "currentTime":
        return _current_time_default(default, context)
    if kind == "generatedId":
        return context.generate_id(_required_text(default, "strategy"))
    raise ValidationFailed("unsupported action parameter default", details={"kind": kind})


def _current_user_default(default: Mapping[str, object], context: ActionParameterContext) -> object:
    attribute = default.get("attribute")
    if attribute in (None, "id"):
        return context.actor_user_id
    if attribute in {"group", "groups", "roles"}:
        return list(context.actor_groups)
    if isinstance(attribute, str) and attribute.strip():
        return context.actor_attributes.get(attribute)
    raise ValidationFailed("unsupported current-user default attribute", details={"attribute": attribute})


def _current_time_default(default: Mapping[str, object], context: ActionParameterContext) -> str:
    unit = default.get("unit")
    if unit == "date":
        return context.now.date().isoformat()
    if unit == "timestamp":
        return context.now.isoformat().replace("+00:00", "Z")
    raise ValidationFailed("unsupported current-time default unit", details={"unit": unit})


def _validate_parameter_presence(
    parameter: ActionParameterV3, config: ResolvedActionParameterConfig, is_present: bool
) -> None:
    if config.required and not is_present:
        raise ValidationFailed("missing required action parameters", details={"missing": [parameter.api_name]})


def _validate_parameter_value(parameter: ActionParameterV3, value: object, constraints: Mapping[str, object]) -> None:
    if not _matches_parameter_type(parameter, value):
        raise ValidationFailed(
            "invalid action parameter types", details={"invalid": [parameter.api_name], "type": parameter.data_type}
        )
    validate_action_parameter_constraints_for_value(parameter.api_name, parameter.data_type, value, constraints)


def _matches_parameter_type(parameter: ActionParameterV3, value: object) -> bool:
    return matches_action_parameter_type(parameter, value)


def _bool_config(raw: Mapping[str, object], field: str) -> bool:
    value = raw.get(field)
    if not isinstance(value, bool):
        raise ValidationFailed("parameter config must be boolean", details={"field": field})
    return value


def _mapping_config(raw: object, field: str) -> Mapping[str, object]:
    if not isinstance(raw, Mapping):
        raise ValidationFailed("parameter config must be an object", details={"field": field})
    return cast(Mapping[str, object], raw)


def _optional_mapping_config(raw: object, field: str) -> Mapping[str, object] | None:
    if raw is None:
        return None
    return _mapping_config(raw, field)


def _required_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValidationFailed("parameter default field is required", details={"field": key})
    return value
